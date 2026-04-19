#!/usr/bin/env python3
"""
Apply **TP / SL / trailing** to the open Bybit **demo** linear position from ``btc_prediction_output.json``.

**Not** part of ``compute_swarm()`` (read-only Swarm). This module **POST**s
``/v5/position/trading-stop`` when enabled.

Env:

- ``SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL=1`` — run apply (default **on** when ``swarm_sync_protocol`` sets it).
- ``SYGNIF_SWARM_TPSL_SYMBOL`` — default ``BTCUSDT``.
- ``SYGNIF_SWARM_TPSL_PROFILE`` — ``default`` | ``reward_risk``. **reward_risk** = moderate TP/SL %% + trail sized for
  faster predict-loop cadence (e.g. 120s entry cooldown); **default** is slightly wider SL for noise. Per-key env
  vars still override the profile base.
- ``SYGNIF_SWARM_TPSL_TP_PCT`` — fallback TP distance from avg entry (%%); profile default if unset.
- ``SYGNIF_SWARM_TPSL_SL_PCT`` — SL distance from avg entry (%%); profile default if unset.
- ``SYGNIF_SWARM_TPSL_TRAIL_USD`` / ``SYGNIF_SWARM_TPSL_TRAIL_FRAC`` — trailing; profile defaults if unset.
- ``SYGNIF_SWARM_TPSL_CHANNEL_ADJUST=1`` — nudge TP/SL from ``training_channel_output.json`` ``recognition``
  probabilities (channel training **ch** vote, aligned with btc-specialist bundle). Default **on**.
- ``SYGNIF_SWARM_TPSL_SKIP_ON_SWARM_CONFLICT=1`` — skip if ``swarm_knowledge_output.json`` has
  ``swarm_conflict`` (default **on**).

**Liquidation-anchored SL (venue ``liqPrice``):** ``SYGNIF_SWARM_SL_LIQ_ANCHOR=1`` (default **on**) — never place
a protective SL **past** the exchange liquidation price: **long** SL is raised to at least
``liqPrice * (1 + buffer)``; **short** SL is capped to at most ``liqPrice * (1 - buffer)``. Buffer:
``SYGNIF_SWARM_SL_LIQ_BUFFER_BPS`` (default **8** = 8 bp). If anchoring would violate Bybit mark clamps,
anchoring is skipped with a note in ``detail.liq_anchor``.

**Swarm memory (audit trail):** ``SYGNIF_SWARM_SL_MEMORY=1`` (default **on**) appends one JSON line per TP/SL
attempt to ``prediction_agent/swarm_sl_liquidation_memory.jsonl`` (cap ``SYGNIF_SWARM_SL_MEMORY_MAX_LINES``,
default **2000**).

Writes ``prediction_agent/swarm_btc_future_tpsl_last.json`` with the last run result (no secrets).

**Consulting:** use Cursor **Task** ``subagent_type=finance-agent`` after sync; feed paths to
``swarm_knowledge_output.json``, ``btc_prediction_output.json``, ``training_channel_output.json``,
and ``finance_agent/btc_specialist/data/manifest.json``. See ``scripts/swarm_channel_finance_consult.sh``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_dotenv_file(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if not k:
            continue
        v = v.strip().strip('"').strip("'")
        if override:
            os.environ[k] = v
        else:
            os.environ.setdefault(k, v)


def load_repo_env() -> None:
    """Same merge order as ``scripts/swarm_sync_protocol.py`` (secrets + demo keys)."""
    raw = (os.environ.get("SYGNIF_SECRETS_ENV_FILE") or "").strip()
    if raw:
        _load_dotenv_file(Path(raw).expanduser())
    _load_dotenv_file(Path.home() / "xrp_claude_bot" / ".env")
    _load_dotenv_file(_repo_root() / ".env")


def _prediction_dir() -> Path:
    for key in ("PREDICTION_AGENT_DIR", "SYGNIF_PREDICTION_AGENT_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return _repo_root() / "prediction_agent"


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def base_tpsl_from_profile() -> tuple[float, float, float, float]:
    """
    Defaults for TP/SL/trail when individual env vars are unset.

    ``reward_risk``: tighter %% bands + smaller trail anchor vs old 0.75/0.22/200 — fits shorter holds when
    ``SWARM_BYBIT_ENTRY_COOLDOWN_SEC`` is low (e.g. 120s). ``default``: a bit more SL room vs ``reward_risk``.
    """
    prof = (os.environ.get("SYGNIF_SWARM_TPSL_PROFILE") or "").strip().lower()
    if prof in ("reward_risk", "rr"):
        return 0.55, 0.20, 120.0, 0.15
    # Default profile: slightly wider SL than reward_risk for chop; smaller trail than legacy 160/0.22.
    return 0.48, 0.24, 100.0, 0.17


def channel_adjust_tpsl(
    side: str,
    tp_pct: float,
    sl_pct: float,
    trail_usd: float,
) -> tuple[float, float, float, dict[str, Any]]:
    """
    Nudge TP/SL/trail from ``training_channel_output.json`` (``ch`` / channel recognition).

    When channel probability **aligns** with the open leg (up for long, down for short), slightly
    widen TP target and loosen risk (tighter SL). When misaligned / low confidence, tighten SL.
    """
    meta: dict[str, Any] = {"enabled": False}
    if not _env_truthy("SYGNIF_SWARM_TPSL_CHANNEL_ADJUST", default=True):
        meta["reason"] = "SYGNIF_SWARM_TPSL_CHANNEL_ADJUST_off"
        return tp_pct, sl_pct, trail_usd, meta
    ch = _read_json(_prediction_dir() / "training_channel_output.json")
    if not ch:
        meta["reason"] = "missing_training_channel_output.json"
        return tp_pct, sl_pct, trail_usd, meta
    rec = ch.get("recognition") if isinstance(ch.get("recognition"), dict) else {}
    try:
        up = float(rec.get("last_bar_probability_up_pct") or 50.0)
        dn = float(rec.get("last_bar_probability_down_pct") or 50.0)
    except (TypeError, ValueError):
        up, dn = 50.0, 50.0
    su = (side or "").strip().upper()
    meta["enabled"] = True
    meta["channel_up_pct"] = round(up, 4)
    meta["channel_down_pct"] = round(dn, 4)
    note = ""
    if su == "BUY":
        if up >= 58.0:
            tp_pct *= 1.12
            sl_pct *= 0.92
            trail_usd *= 1.08
            note = "align_long_channel_up"
        elif up < 45.0:
            sl_pct *= 0.88
            note = "tighten_sl_channel_uncertain_long"
    elif su == "SELL":
        if dn >= 58.0:
            tp_pct *= 1.12
            sl_pct *= 0.92
            trail_usd *= 1.08
            note = "align_short_channel_down"
        elif dn < 45.0:
            sl_pct *= 0.88
            note = "tighten_sl_channel_uncertain_short"
    tp_pct = max(0.12, min(2.5, tp_pct))
    sl_pct = max(0.12, min(0.85, sl_pct))
    trail_usd = max(0.0, float(trail_usd))
    meta["tp_pct_adj"] = round(tp_pct, 6)
    meta["sl_pct_adj"] = round(sl_pct, 6)
    meta["trail_usd_adj"] = round(trail_usd, 4)
    if note:
        meta["note"] = note
    return tp_pct, sl_pct, trail_usd, meta


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _fmt_price(x: float) -> str:
    if x >= 1000:
        return f"{x:.2f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.6f}"


def finalize_linear_stop_loss(
    *,
    side: str,
    sl: float,
    mark: float,
    liq_price: float | None,
    liq_buffer_bps: float = 8.0,
    liq_anchor_enabled: bool = True,
) -> tuple[float, dict[str, Any]]:
    """
    Enforce **liquidation buffer** and **Bybit mark** rules on a numeric stop price.

    Mark clamp (``sl`` vs ``mark``) can pull a long stop **below** the liq floor computed earlier;
    this helper alternates liq-floor / mark-ceiling until stable so liquidation is never closer than
    the venue SL (for longs: ``stop_loss > liq * (1+buffer)`` when ``liq`` is known).
    """
    meta: dict[str, Any] = {}
    su = (side or "").strip().upper()
    sl_f = float(sl)
    mk = float(mark) if mark and mark > 0 else 0.0
    liq_f = float(liq_price) if liq_price is not None and float(liq_price) > 0 else 0.0
    bps = max(0.0, min(500.0, float(liq_buffer_bps))) / 10000.0
    eps = 1e-4

    if not liq_anchor_enabled or liq_f <= 0:
        if mk > 0:
            if su == "BUY" and sl_f >= mk:
                sl_f = mk * (1.0 - 3 * eps)
                meta["mark_clamp"] = "sl_lt_mark"
            elif su != "BUY" and sl_f <= mk:
                sl_f = mk * (1.0 + 3 * eps)
                meta["mark_clamp"] = "sl_gt_mark"
        return sl_f, meta

    for _ in range(6):
        changed = False
        if su == "BUY":
            floor = liq_f * (1.0 + bps)
            if sl_f < floor - 1e-12:
                sl_f = floor
                meta["liq_floor"] = round(floor, 4)
                meta["action"] = "raised_sl_above_liq_floor"
                changed = True
        else:
            cap = liq_f * (1.0 - bps)
            if sl_f > cap + 1e-12:
                sl_f = cap
                meta["liq_cap"] = round(cap, 4)
                meta["action"] = "capped_sl_below_liq_ceiling"
                changed = True
        if mk > 0:
            if su == "BUY" and sl_f >= mk * (1.0 - 1e-12):
                sl_f = mk * (1.0 - 3 * eps)
                meta["mark_clamp"] = "sl_lt_mark"
                changed = True
            elif su != "BUY" and sl_f <= mk * (1.0 + 1e-12):
                sl_f = mk * (1.0 + 3 * eps)
                meta["mark_clamp"] = "sl_gt_mark"
                changed = True
        if not changed:
            break

    if liq_f > 0:
        if su == "BUY":
            floor = liq_f * (1.0 + bps)
            if sl_f < floor - 1e-9:
                sl_f = floor
                meta["post_mark_reanchor"] = "forced_above_liq_after_mark_clamp"
        else:
            cap = liq_f * (1.0 - bps)
            if sl_f > cap + 1e-9:
                sl_f = cap
                meta["post_mark_reanchor"] = "forced_below_liq_after_mark_clamp"

    return sl_f, meta


def finalize_linear_take_profit(*, side: str, tp: float, mark: float) -> tuple[float, dict[str, Any]]:
    """Long TP must sit above mark; short TP below mark (Bybit full-mode TP)."""
    meta: dict[str, Any] = {}
    su = (side or "").strip().upper()
    mk = float(mark) if mark and mark > 0 else 0.0
    tp_f = float(tp)
    if mk <= 0:
        return tp_f, meta
    eps = 1e-4
    if su == "BUY" and tp_f <= mk * (1.0 + 1e-12):
        tp_f = mk * (1.0 + 3 * eps)
        meta["tp_mark_clamp"] = "tp_gt_mark"
    elif su != "BUY" and tp_f >= mk * (1.0 - 1e-12):
        tp_f = mk * (1.0 - 3 * eps)
        meta["tp_mark_clamp"] = "tp_lt_mark"
    return tp_f, meta


def _pick_open_position(rows: list[Any]) -> dict[str, Any] | None:
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            sz = abs(float(r.get("size") or 0.0))
        except (TypeError, ValueError):
            continue
        if sz >= 1e-12:
            return r
    return None


def consensus_mid_and_close(pred: dict[str, Any]) -> tuple[float, float]:
    """Return (mid_next_mean, current_close) for TP/SL anchoring."""
    close = 0.0
    try:
        close = float(pred.get("current_close") or 0.0)
    except (TypeError, ValueError):
        pass
    pr = pred.get("predictions") if isinstance(pred.get("predictions"), dict) else {}
    rf = pr.get("random_forest") if isinstance(pr.get("random_forest"), dict) else {}
    xg = pr.get("xgboost") if isinstance(pr.get("xgboost"), dict) else {}
    try:
        rf_m = float(rf.get("next_mean") or 0.0)
    except (TypeError, ValueError):
        rf_m = 0.0
    try:
        xg_m = float(xg.get("next_mean") or 0.0)
    except (TypeError, ValueError):
        xg_m = 0.0
    if rf_m > 0 and xg_m > 0:
        mid = (rf_m + xg_m) / 2.0
    elif rf_m > 0:
        mid = rf_m
    elif xg_m > 0:
        mid = xg_m
    else:
        mid = close
    return mid, close


def compute_tpsl_strings(
    *,
    side: str,
    avg: float,
    mid: float,
    mark: float,
    tp_pct: float,
    sl_pct: float,
    trail_usd: float,
    trail_frac: float,
    liq_price: float | None = None,
    liq_buffer_bps: float = 8.0,
    liq_anchor_enabled: bool = True,
) -> dict[str, Any]:
    """
    Build Bybit price strings for Full position TP/SL + trailing distance.

    Long: TP toward ``mid`` when ``mid > avg``; else minimal TP above entry.
    Short: TP toward ``mid`` when ``mid < avg``; else minimal TP below entry.

    Optional **liquidation anchor** (``liq_price``): long SL is floored at ``liq*(1+buffer)``; short SL is
    capped at ``liq*(1-buffer)`` when ``liq_anchor_enabled`` and venue ``liqPrice`` is known.

    Bybit v5: for **Buy**, TP must be **above** ``mark``; for **Sell**, TP must be **below** ``mark``
    (``base_price`` in API errors). We clamp to satisfy that.
    """
    su = (side or "").strip().upper()
    tp_pct_f = max(0.01, float(tp_pct))
    sl_pct_f = max(0.01, float(sl_pct))
    mk = float(mark) if mark and mark > 0 else 0.0
    # ~1 bp cushion vs mark (tick-size rounding handled by string formatting)
    eps = 1e-4

    liq_anchor_meta: dict[str, Any] = {}

    if su == "BUY":
        if mid > avg:
            tp = mid
            tp_note = "tp=consensus_mid_above_avg"
        else:
            tp = avg * (1.0 + tp_pct_f / 100.0)
            tp_note = "tp=fallback_pct_long"
        sl_f = avg * (1.0 - sl_pct_f / 100.0)
        if mk > 0 and tp <= mk:
            tp = mk * (1.0 + eps)
            tp_note += "|clamped_gt_mark"
    else:
        if mid < avg:
            tp = mid
            tp_note = "tp=consensus_mid_below_avg"
        else:
            tp = avg * (1.0 - tp_pct_f / 100.0)
            tp_note = "tp=fallback_pct_short"
        sl_f = avg * (1.0 + sl_pct_f / 100.0)
        if mk > 0 and tp >= mk:
            tp = mk * (1.0 - eps)
            tp_note += "|clamped_lt_mark"

    liq_use: float | None = None
    if liq_anchor_enabled and liq_price is not None and float(liq_price) > 0:
        liq_f = float(liq_price)
        bps = max(0.0, min(500.0, float(liq_buffer_bps))) / 10000.0
        liq_anchor_meta["liq_price"] = liq_f
        liq_anchor_meta["buffer_bps"] = round(float(liq_buffer_bps), 4)
        if su == "BUY":
            floor = liq_f * (1.0 + bps)
            if mk > 0 and floor >= mk:
                liq_anchor_meta["skip"] = "liq_floor_ge_mark"
            else:
                liq_use = liq_f
        else:
            cap = liq_f * (1.0 - bps)
            if mk > 0 and cap <= mk:
                liq_anchor_meta["skip"] = "liq_cap_le_mark"
            else:
                liq_use = liq_f

    sl_f, sl_meta = finalize_linear_stop_loss(
        side=su,
        sl=sl_f,
        mark=mk,
        liq_price=liq_use,
        liq_buffer_bps=liq_buffer_bps,
        liq_anchor_enabled=liq_anchor_enabled and liq_use is not None,
    )
    liq_anchor_meta.update(sl_meta)

    tp, tp_meta = finalize_linear_take_profit(side=su, tp=tp, mark=mk)
    if tp_meta:
        liq_anchor_meta.update(tp_meta)
        if tp_meta.get("tp_mark_clamp"):
            tp_note += "|tp_mark_finalize"

    sl = sl_f

    dist = abs(mid - avg) if mid > 0 and avg > 0 else 0.0
    trail = max(0.0, float(trail_usd) + trail_frac * dist)
    trail_s = f"{trail:.2f}" if trail > 0 else ""

    return {
        "take_profit": _fmt_price(tp),
        "stop_loss": _fmt_price(sl),
        "trailing_stop": trail_s,
        "tp_note": tp_note,
        "mid_next_mean": mid,
        "avg_entry": avg,
        "mark_price": mk if mk > 0 else None,
        "liq_anchor_meta": liq_anchor_meta,
    }


def _append_swarm_sl_memory_from_out(out: dict[str, Any]) -> None:
    """Append one JSONL record when TP/SL detail was computed (best-effort)."""
    if not _env_truthy("SYGNIF_SWARM_SL_MEMORY", default=True):
        return
    det = out.get("detail")
    if not isinstance(det, dict) or not det.get("stop_loss"):
        return
    path = _prediction_dir() / "swarm_sl_liquidation_memory.jsonl"
    max_lines = max(50, int(_env_float("SYGNIF_SWARM_SL_MEMORY_MAX_LINES", 2000.0)))
    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ok": bool(out.get("ok")),
        "skipped": out.get("skipped"),
        "symbol": det.get("symbol"),
        "side": det.get("side"),
        "take_profit": det.get("take_profit"),
        "stop_loss": det.get("stop_loss"),
        "liq_price_venue": det.get("liq_price_venue"),
        "liq_anchor": det.get("liq_anchor") or {},
        "channel_tpsl": det.get("channel_tpsl"),
    }
    br = out.get("bybit")
    if isinstance(br, dict):
        row["bybit_retCode"] = br.get("retCode")
        row["bybit_retMsg"] = br.get("retMsg")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        return
    try:
        txt = path.read_text(encoding="utf-8")
    except OSError:
        return
    lines = txt.splitlines()
    if len(lines) <= max_lines:
        return
    keep = lines[-max_lines:]
    try:
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError:
        pass


def apply_btc_future_tpsl(*, dry_run: bool = False) -> dict[str, Any]:
    """
    If enabled and demo keys + open position + prediction file: POST trading-stop.

    Returns a dict with ``ok``, ``skipped`` (reason or None), ``detail``, ``bybit`` (last response).
    Always updates ``swarm_btc_future_tpsl_last.json`` under the prediction dir (best-effort).
    """
    out: dict[str, Any] = {
        "ok": False,
        "skipped": None,
        "detail": {},
        "bybit": None,
    }
    try:
        return _apply_btc_future_tpsl_impl(out, dry_run=dry_run)
    finally:
        try:
            (_prediction_dir() / "swarm_btc_future_tpsl_last.json").write_text(
                json.dumps(out, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        _append_swarm_sl_memory_from_out(out)


def _apply_btc_future_tpsl_impl(out: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if not _env_truthy("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", default=False):
        out["skipped"] = "SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL_off"
        return out

    has_demo = bool(
        os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
        and os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
    )
    if not has_demo:
        out["skipped"] = "no_BYBIT_DEMO_keys"
        return out

    sym = os.environ.get("SYGNIF_SWARM_TPSL_SYMBOL", "BTCUSDT").strip().upper() or "BTCUSDT"
    pred_path = _prediction_dir() / "btc_prediction_output.json"
    pred = _read_json(pred_path)
    if not pred:
        out["skipped"] = "missing_btc_prediction_output.json"
        return out

    swarm_path = _prediction_dir() / "swarm_knowledge_output.json"
    swarm = _read_json(swarm_path)
    if swarm and _env_truthy("SYGNIF_SWARM_TPSL_SKIP_ON_SWARM_CONFLICT", default=True):
        if bool(swarm.get("swarm_conflict")):
            out["skipped"] = "swarm_conflict"
            return out

    td = _repo_root() / "trade_overseer"
    tds = str(td)
    if tds not in sys.path:
        sys.path.insert(0, tds)
    import bybit_linear_hedge as blh  # noqa: PLC0415

    try:
        resp = blh.position_list(sym)
    except RuntimeError as e:
        out["skipped"] = "credentials_error"
        out["detail"] = {"error": str(e)}
        return out

    if resp.get("retCode") != 0:
        out["skipped"] = "position_list_error"
        out["detail"] = {"retCode": resp.get("retCode"), "retMsg": resp.get("retMsg")}
        return out

    rows = (resp.get("result") or {}).get("list") or []
    pos = _pick_open_position(rows)
    if not pos:
        out["skipped"] = "flat"
        out["detail"] = {"symbol": sym}
        return out

    side = str(pos.get("side") or "")
    try:
        avg = float(pos.get("avgPrice") or 0.0)
    except (TypeError, ValueError):
        avg = 0.0
    if avg <= 0:
        out["skipped"] = "invalid_avg_price"
        return out

    try:
        mark = float(pos.get("markPrice") or 0.0)
    except (TypeError, ValueError):
        mark = 0.0
    if mark <= 0:
        mark = avg

    try:
        liq_raw = float(pos.get("liqPrice") or 0.0)
    except (TypeError, ValueError):
        liq_raw = 0.0
    liq_price = liq_raw if liq_raw > 0 else None

    try:
        pidx = int(pos.get("positionIdx") or 0)
    except (TypeError, ValueError):
        pidx = 0

    mid, _close = consensus_mid_and_close(pred)
    b_tp, b_sl, b_tr, b_tf = base_tpsl_from_profile()
    tp_pct = _env_float("SYGNIF_SWARM_TPSL_TP_PCT", b_tp)
    sl_pct = _env_float("SYGNIF_SWARM_TPSL_SL_PCT", b_sl)
    trail_usd = _env_float("SYGNIF_SWARM_TPSL_TRAIL_USD", b_tr)
    trail_frac = _env_float("SYGNIF_SWARM_TPSL_TRAIL_FRAC", b_tf)
    tp_pct, sl_pct, trail_usd, ch_meta = channel_adjust_tpsl(side, tp_pct, sl_pct, trail_usd)

    comp = compute_tpsl_strings(
        side=side,
        avg=avg,
        mid=mid,
        mark=mark,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        trail_usd=trail_usd,
        trail_frac=trail_frac,
        liq_price=liq_price,
        liq_buffer_bps=_env_float("SYGNIF_SWARM_SL_LIQ_BUFFER_BPS", 8.0),
        liq_anchor_enabled=_env_truthy("SYGNIF_SWARM_SL_LIQ_ANCHOR", default=True),
    )
    tp_s = comp["take_profit"]
    sl_s = comp["stop_loss"]
    trail_s = comp["trailing_stop"]

    out["detail"] = {
        "symbol": sym,
        "side": side,
        "positionIdx": pidx,
        "take_profit": tp_s,
        "stop_loss": sl_s,
        "trailing_stop": trail_s or None,
        "tp_note": comp.get("tp_note"),
        "mid_next_mean": comp.get("mid_next_mean"),
        "avg_entry": comp.get("avg_entry"),
        "mark_price": comp.get("mark_price"),
        "tpsl_profile": (os.environ.get("SYGNIF_SWARM_TPSL_PROFILE") or "default").strip() or "default",
        "channel_tpsl": ch_meta,
        "liq_price_venue": liq_price,
        "liq_anchor": comp.get("liq_anchor_meta") or {},
    }

    if dry_run:
        out["ok"] = True
        out["skipped"] = "dry_run"
        return out

    kwargs: dict[str, Any] = {
        "position_idx": pidx,
        "take_profit": tp_s,
        "stop_loss": sl_s,
    }
    if trail_s:
        kwargs["trailing_stop"] = trail_s

    api_r = blh.set_trading_stop_linear(sym, **kwargs)
    out["bybit"] = api_r
    out["ok"] = api_r.get("retCode") == 0
    if not out["ok"]:
        out["skipped"] = "bybit_error"
    return out


def _write_last(repo: Path, payload: dict[str, Any]) -> None:
    dest = _prediction_dir() / "swarm_btc_future_tpsl_last.json"
    try:
        dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Set demo linear TP/SL from btc_prediction_output.json (swarm sync helper)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Compute only; do not POST")
    args = ap.parse_args()

    load_repo_env()
    repo = _repo_root()
    result = apply_btc_future_tpsl(dry_run=args.dry_run)
    result["schema_version"] = 1
    _write_last(repo, result)
    print(json.dumps(result, indent=2))
    br = result.get("bybit")
    if isinstance(br, dict) and br.get("retCode") not in (0, None):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
