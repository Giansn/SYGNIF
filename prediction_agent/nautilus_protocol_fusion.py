#!/usr/bin/env python3
"""
Fuse **Nautilus research sidecar** (``nautilus_strategy_signal.json``) + **BTC ML JSON**
(``btc_prediction_output.json``) + optional **btc_future** / **bf** (Bybit API **demo** linear position when
``SYGNIF_SWARM_BTC_FUTURE`` is truthy demo mode, or **mainnet** linear position when ``SYGNIF_SWARM_BTC_FUTURE=trade`` —
via ``finance_agent.swarm_knowledge``) + optional **predict-protocol
loop tick** + **swarm_keypoints** (annotations from ``swarm_knowledge_output.json`` when present)
into one **sidecar** for swarm / briefing / dashboards.

``fusion.vote_btc_future`` is the **bf** position vote (demo or trade mode); ``fusion.btc_future_direction``
is ``long`` / ``short`` / ``flat`` for quick alignment with swarm.orders.

- **Write path:** ``prediction_agent/swarm_nautilus_protocol_sidecar.json`` (override
  ``SYGNIF_NAUTILUS_FUSION_PATH``).
- **Sync:** ``python3 prediction_agent/nautilus_protocol_fusion.py sync``
- **Briefing:** ``SYGNIF_BRIEFING_INCLUDE_NAUTILUS_FUSION=1`` in ``ruleprediction_briefing`` (compact line).

Nautilus loop hook: set ``NAUTILUS_FUSION_SIDECAR_SYNC=1`` in ``nautilus_sidecar_strategy`` environment
so each sidecar refresh also refreshes this file (host layout with SYGNIF repo next to ``research/``).

Protocol loop: ``SYGNIF_PROTOCOL_FUSION_SYNC=1`` in ``scripts/btc_predict_protocol_loop.py`` → each iteration
refreshes this file from Nautilus sidecar + ML (and optional **bf** / swarm keypoints) **without** requiring
``SYGNIF_SWARM_GATE_LOOP`` or ``--execute``. ``SYGNIF_PROTOCOL_FUSION_TICK=1`` → each iteration also embeds the
``predict_protocol_loop`` predict line in the same JSON (no venue writes here).

**Same-iteration ML:** ``write_fused_sidecar(..., btc_prediction_override=…)`` uses that dict as ``btc_prediction``
instead of reading ``btc_prediction_output.json``, so flat-**bf** adapt + fusion gates match the **in-memory**
``run_live_fit`` / ``decide_side`` payload (avoids stale or missing on-disk JSON when ``write_json_path`` is off).

**Flat venue → fusion vote (optional):** when ``SYGNIF_FUSION_BTC_FUTURE_ADAPT_WHEN_FLAT`` is on (default **on**),
if the live **bf** vote is ``0`` with detail ``flat`` and the venue read succeeded, ``vote_btc_future`` in this
JSON is **replaced** for fusion math by the ML vote if non-zero, else the Nautilus vote — so
``SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE`` can align with the model while the demo book is flat. Original venue vote is
kept in ``fusion.vote_btc_future_raw``.

**USD broad index vs BTC** (FRED ``DTWEXBGS`` + ``btc_daily_90d.json``): optional ``usd_btc_macro`` block in this
JSON when ``btc_usd_index_correlation.json`` exists under the BTC data dir (from ``pull_btc_context``) or when
``SYGNIF_PREDICT_USD_BTC_CORR_LIVE=1`` with ``FRED_API_KEY``. TTL/cache: ``SYGNIF_PREDICT_USD_BTC_CORR_TTL_SEC``
(default 3600). Disable entirely: ``SYGNIF_PREDICT_USD_BTC_MACRO_OFF=1``.

**Public liquidation tape** (``allLiquidation`` via ``bybit_stream_monitor`` → ``user_data/bybit_ws_monitor_state.json``):
optional ``liquidation_tape`` block with rolling notionals, imbalance ratio, and ``tape_pressure_vote`` in
``{-1,0,+1}`` (long flush → bearish pressure → ``-1``; short flush → ``+1``). Enable with
``SYGNIF_PREDICT_LIQUIDATION_TAPE=1`` (default **off** until set). Window: ``SYGNIF_LIQUIDATION_TAPE_WINDOW_SEC`` (default 900).
Gate entries via ``SWARM_ORDER_LIQUIDATION_TAPE_GATE=1`` in ``swarm_order_gate.swarm_fusion_allows``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import math

SCHEMA_VERSION = 2  # optional swarm_keypoints + fusion.btc_future_direction (same major)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def fused_sidecar_path(repo_root: Path | None = None) -> Path:
    raw = (os.environ.get("SYGNIF_NAUTILUS_FUSION_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    base = repo_root or _repo_root()
    return base / "prediction_agent" / "swarm_nautilus_protocol_sidecar.json"


def _btc_data_dir(repo_root: Path) -> Path:
    raw = (os.environ.get("NAUTILUS_BTC_OHLCV_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return repo_root / "finance_agent" / "btc_specialist" / "data"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _vote_nautilus_bias(raw: dict[str, Any]) -> tuple[int, str]:
    b = raw.get("bias")
    if not isinstance(b, str):
        return 0, "?"
    bl = b.lower().strip()
    if bl == "long":
        return 1, "long"
    if bl == "short":
        return -1, "short"
    return 0, "neutral"


def _btc_future_fusion_vote(repo_root: Path) -> tuple[int, str, dict[str, Any]]:
    """
    Same **bf** vote as ``swarm_knowledge.compute_swarm`` when ``SYGNIF_SWARM_BTC_FUTURE`` is **demo** or **trade**.
    Read-only; no orders.
    """
    rs = str(repo_root.resolve())
    if rs not in sys.path:
        sys.path.insert(0, rs)
    try:
        from finance_agent import swarm_knowledge as sk  # noqa: PLC0415
    except ImportError:
        return 0, "swarm_sk_missing", {"enabled": True, "ok": False}

    mode = sk.sygnif_swarm_btc_future_mode()
    if mode == "off":
        return 0, "off", {"enabled": False}

    sym = os.environ.get("SYGNIF_SWARM_BTC_FUTURE_SYMBOL", "BTCUSDT").strip().upper() or "BTCUSDT"
    try:
        cache_sec = float(os.environ.get("SYGNIF_SWARM_BTC_FUTURE_CACHE_SEC", "60") or 60)
    except ValueError:
        cache_sec = 60.0
    ttl = max(15.0, cache_sec)

    if mode == "demo":
        has_demo = bool(
            os.environ.get("BYBIT_DEMO_API_KEY", "").strip()
            and os.environ.get("BYBIT_DEMO_API_SECRET", "").strip()
        )
        if not has_demo:
            return 0, "no_demo_creds", {"enabled": True, "ok": False, "has_demo_keys": False, "profile": "btc_future"}

        resp = sk.fetch_demo_linear_position_list(sym, cache_sec=ttl)
        v, d = sk.vote_account_position_from_response(resp)
        ok = resp is not None and resp.get("retCode") == 0
        meta: dict[str, Any] = {
            "enabled": True,
            "ok": ok,
            "has_demo_keys": True,
            "symbol": sym,
            "profile": "btc_future",
            "mode": "demo",
        }
        snap = sk.linear_position_snapshot_from_response(resp)
        if snap is not None:
            meta["position"] = snap
        return v, d, meta

    has_trade = bool(
        os.environ.get("BYBIT_API_KEY", "").strip()
        and os.environ.get("BYBIT_API_SECRET", "").strip()
    )
    if not has_trade:
        return 0, "no_trade_creds", {"enabled": True, "ok": False, "has_trade_keys": False, "profile": "trade"}

    resp = sk.fetch_mainnet_linear_position_list(sym, cache_sec=ttl)
    v, d = sk.vote_account_position_from_response(resp)
    ok = resp is not None and resp.get("retCode") == 0
    meta = {
        "enabled": True,
        "ok": ok,
        "has_trade_keys": True,
        "symbol": sym,
        "profile": "trade",
        "mode": "trade",
        "mainnet": True,
    }
    snap = sk.linear_position_snapshot_from_response(resp)
    if snap is not None:
        meta["position"] = snap
    return v, d, meta


def _btc_future_direction(v: int) -> str:
    """Semantic direction from linear position vote (same as swarm ``bf``)."""
    if v >= 1:
        return "long"
    if v <= -1:
        return "short"
    return "flat"


def _fusion_adapt_flat_btc_future_enabled() -> bool:
    """When venue bf is flat, optionally lift ``vote_btc_future`` from ML / Nautilus for fusion + gates."""
    raw = (os.environ.get("SYGNIF_FUSION_BTC_FUTURE_ADAPT_WHEN_FLAT") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _adapt_btc_future_vote_for_fusion(
    v_bf: int,
    bf_lab: str,
    bf_meta: dict[str, Any],
    *,
    vn: int,
    nlab: str,
    vm: int,
    mlab: str,
) -> tuple[int, str, dict[str, Any]]:
    """
    If demo/trade book is **flat** (``bf_lab == "flat"``) and venue read **ok**, copy ML or Nautilus vote into
    ``vote_btc_future`` so ``swarm_fusion_allows`` / fusion sum are not stuck at neutral bf.

    Returns ``(vote_used, detail_label, meta_patch)`` where ``meta_patch`` is merged into ``btc_future_meta``.
    """
    meta_patch: dict[str, Any] = {}
    if not _fusion_adapt_flat_btc_future_enabled():
        return v_bf, bf_lab, meta_patch
    if v_bf != 0 or bf_lab != "flat":
        return v_bf, bf_lab, meta_patch
    if not (isinstance(bf_meta, dict) and bf_meta.get("ok")):
        return v_bf, bf_lab, meta_patch
    if vm != 0:
        meta_patch["fusion_flat_adapted"] = True
        meta_patch["fusion_flat_adapt_source"] = "ml"
        return vm, f"flat→ml:{mlab}", meta_patch
    if vn != 0:
        meta_patch["fusion_flat_adapted"] = True
        meta_patch["fusion_flat_adapt_source"] = "nautilus"
        return vn, f"flat→nautilus:{nlab}", meta_patch
    return v_bf, bf_lab, meta_patch


def _swarm_keypoints_for_fusion(repo_root: Path) -> list[dict[str, Any]]:
    """Annotations from ``swarm_knowledge_output.json`` (full ``compute_swarm`` JSON)."""
    p = repo_root / "prediction_agent" / "swarm_knowledge_output.json"
    sw = _read_json(p)
    if not sw:
        return []
    try:
        from swarm_annotations import build_swarm_keypoints  # noqa: PLC0415
    except ImportError:
        return []
    return build_swarm_keypoints(sw)


def _vote_ml_consensus(pred: dict[str, Any]) -> tuple[int, str]:
    pr = pred.get("predictions") if isinstance(pred.get("predictions"), dict) else {}
    raw = str(pr.get("consensus_nautilus_enhanced") or pr.get("consensus") or "").strip().upper()
    if raw in ("BULLISH", "STRONG_BULLISH"):
        return 1, raw
    if raw in ("BEARISH", "STRONG_BEARISH"):
        return -1, raw
    if raw == "MIXED":
        return 0, "MIXED"
    dlr = pr.get("direction_logistic") if isinstance(pr.get("direction_logistic"), dict) else {}
    lab = str(dlr.get("label") or "").strip().upper()
    try:
        conf = float(dlr.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= 65.0:
        if lab == "UP":
            return 1, f"LRup{conf:.0f}"
        if lab == "DOWN":
            return -1, f"LRdn{conf:.0f}"
    return 0, raw or "?"


def _atomic_write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _usd_btc_macro_for_sidecar(repo_root: Path) -> dict[str, Any] | None:
    """
    FRED broad USD vs BTC daily correlation for predict-protocol / Swarm context.

    Reads ``btc_usd_index_correlation.json`` when present; optional live recompute when
    ``SYGNIF_PREDICT_USD_BTC_CORR_LIVE=1`` and ``FRED_API_KEY`` (respects TTL vs snapshot mtime).
    """
    if _env_truthy("SYGNIF_PREDICT_USD_BTC_MACRO_OFF"):
        return None
    data_dir = _btc_data_dir(repo_root)
    snap_path = data_dir / "btc_usd_index_correlation.json"
    daily_path = data_dir / "btc_daily_90d.json"
    ttl = max(60.0, _env_float("SYGNIF_PREDICT_USD_BTC_CORR_TTL_SEC", 3600.0))

    def _from_snapshot() -> dict[str, Any] | None:
        raw = _read_json(snap_path)
        if not raw:
            return None
        out = dict(raw)
        out.setdefault("macro_source", "snapshot_file")
        return out

    fa = repo_root / "finance_agent"
    live = _env_truthy("SYGNIF_PREDICT_USD_BTC_CORR_LIVE")
    if live and fa.is_dir():
        rs = str(fa.resolve())
        if rs not in sys.path:
            sys.path.insert(0, rs)
        try:
            from btc_usd_index_correlation import compute_btc_usd_index_correlation
            from btc_usd_index_correlation import fred_api_key
        except ImportError:
            return _from_snapshot()
        if fred_api_key():
            stale = True
            if snap_path.is_file():
                try:
                    stale = (time.time() - snap_path.stat().st_mtime) >= ttl
                except OSError:
                    stale = True
            if not stale:
                got = _from_snapshot()
                if got:
                    got["macro_source"] = "snapshot_cache"
                    return got
            if daily_path.is_file():
                try:
                    raw_list = json.loads(daily_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return _from_snapshot()
                if isinstance(raw_list, list):
                    doc, err = compute_btc_usd_index_correlation(raw_list)
                    if doc and not err:
                        out: dict[str, Any] = {
                            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "source": "FRED + Bybit daily (nautilus_protocol_fusion live)",
                            "metric": (
                                "Pearson correlation of same-calendar-day simple returns "
                                "(BTC vs USD broad index)"
                            ),
                            "macro_source": "fred_live",
                            **doc,
                        }
                        if _env_truthy("SYGNIF_PREDICT_USD_BTC_CORR_WRITE_SNAPSHOT"):
                            try:
                                snap_path.parent.mkdir(parents=True, exist_ok=True)
                                tmp = snap_path.with_suffix(".json.tmp")
                                tmp.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
                                tmp.replace(snap_path)
                            except OSError:
                                pass
                        return out
    return _from_snapshot()


def _ws_monitor_path(repo_root: Path) -> Path:
    raw = (os.environ.get("SYGNIF_LIQUIDATION_WS_SNAPSHOT_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else repo_root / p
    return repo_root / "user_data" / "bybit_ws_monitor_state.json"


def _liquidation_tape_for_sidecar(repo_root: Path) -> dict[str, Any] | None:
    """
    Summarise recent **public** linear liquidations from the Bybit WS monitor snapshot.

    Bybit semantics: ``S`` = ``Buy`` → **long** position liquidated (forced sell); ``Sell`` → **short** liquidated.
    """
    if not _env_truthy("SYGNIF_PREDICT_LIQUIDATION_TAPE"):
        return None  # opt-in: requires ``bybit_stream_monitor`` snapshot + WS
    path = _ws_monitor_path(repo_root)
    snap = _read_json(path)
    if not snap:
        return {
            "enabled": True,
            "ok": False,
            "path": str(path),
            "detail": "snapshot_missing_or_empty",
        }
    window_sec = max(60.0, _env_float("SYGNIF_LIQUIDATION_TAPE_WINDOW_SEC", 900.0))
    min_n = max(1.0, _env_float("SYGNIF_LIQUIDATION_TAPE_MIN_NOTIONAL_USDT", 25_000.0))
    ratio_thr = max(1.05, _env_float("SYGNIF_LIQUIDATION_TAPE_RATIO", 2.0))
    now_ms = int(time.time() * 1000)
    cut = now_ms - int(window_sec * 1000)

    recent = snap.get("liquidations_recent")
    if not isinstance(recent, list):
        recent = []

    long_usdt = 0.0
    short_usdt = 0.0
    n_long = 0
    n_short = 0
    kept: list[dict[str, Any]] = []

    for row in recent:
        if not isinstance(row, dict):
            continue
        try:
            t_ev = int(row.get("T") or 0)
        except (TypeError, ValueError):
            t_ev = 0
        if t_ev and t_ev < cut:
            continue
        side = str(row.get("S") or "").strip()
        try:
            qty = float(str(row.get("v") or "0").strip() or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            px = float(str(row.get("p") or "0").strip() or 0.0)
        except (TypeError, ValueError):
            px = 0.0
        usd = abs(qty) * px if px > 0 and qty else 0.0
        su = side.upper()
        if su == "BUY":
            long_usdt += usd
            n_long += 1
        elif su == "SELL":
            short_usdt += usd
            n_short += 1
        kept.append(
            {
                "s": row.get("s"),
                "S": side,
                "v": row.get("v"),
                "p": row.get("p"),
                "T": row.get("T"),
                "usd_est": round(usd, 2) if usd else 0.0,
            }
        )

    tape_pressure_vote = 0
    label = "quiet"
    detail = "below_threshold"
    denom = max(short_usdt, 1e-9)
    numer = max(long_usdt, 1e-9)
    imb = long_usdt / denom if denom > 0 else (float("inf") if long_usdt > 0 else 0.0)
    if imb == float("inf"):
        imb = ratio_thr + 1.0
    inv_imb = short_usdt / numer if numer > 0 else (float("inf") if short_usdt > 0 else 0.0)
    if inv_imb == float("inf"):
        inv_imb = ratio_thr + 1.0

    if long_usdt >= min_n and imb >= ratio_thr and n_long >= 1:
        tape_pressure_vote = -1
        label = "long_liquidation_flush"
        detail = f"long_notional_usdt~{long_usdt:.0f}>={min_n:.0f} vs_short_ratio>{ratio_thr}"
    elif short_usdt >= min_n and inv_imb >= ratio_thr and n_short >= 1:
        tape_pressure_vote = 1
        label = "short_liquidation_flush"
        detail = f"short_notional_usdt~{short_usdt:.0f}>={min_n:.0f} vs_long_ratio>{ratio_thr}"
    elif n_long + n_short > 0:
        label = "balanced_or_moderate"
        detail = "tape_active_no_dominant_side"

    sym_focus = str(snap.get("symbol") or os.environ.get("BYBIT_WS_SYMBOL", "BTCUSDT")).upper()

    return {
        "enabled": True,
        "ok": True,
        "path": str(path.resolve()),
        "symbol_focus": sym_focus,
        "window_sec": int(window_sec),
        "cutoff_event_ms": cut,
        "ingress_total": int(snap.get("liquidation_ingress_total") or 0),
        "in_window": {"n_long_events": n_long, "n_short_events": n_short},
        "notional_usdt_est": {
            "long_side": round(long_usdt, 2),
            "short_side": round(short_usdt, 2),
            "imbalance_long_over_short": round(imb, 4) if math.isfinite(imb) else None,
        },
        "tape_pressure_vote": tape_pressure_vote,
        "tape_label": label,
        "tape_detail": detail,
        "recent_trimmed": kept[-24:],
    }


def build_fusion_payload(
    repo_root: Path,
    *,
    previous: dict[str, Any] | None = None,
    btc_prediction_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data_dir = _btc_data_dir(repo_root)
    naut = _read_json(data_dir / "nautilus_strategy_signal.json")
    if isinstance(btc_prediction_override, dict):
        pred = btc_prediction_override
    else:
        pred = _read_json(repo_root / "prediction_agent" / "btc_prediction_output.json")

    vn, nlab = _vote_nautilus_bias(naut or {})
    vm, mlab = _vote_ml_consensus(pred or {})
    v_bf_raw, bf_lab_raw, bf_meta = _btc_future_fusion_vote(repo_root)
    v_bf, bf_lab, adapt_meta = _adapt_btc_future_vote_for_fusion(
        v_bf_raw, bf_lab_raw, bf_meta, vn=vn, nlab=nlab, vm=vm, mlab=mlab
    )
    bf_meta_out = dict(bf_meta)
    bf_meta_out.update(adapt_meta)
    fused = vn + vm + v_bf
    if fused >= 2:
        label = "strong_long"
    elif fused <= -2:
        label = "strong_short"
    elif fused == 1:
        label = "lean_long"
    elif fused == -1:
        label = "lean_short"
    else:
        label = "neutral"

    prev_tick = None
    if isinstance(previous, dict):
        ppl = previous.get("predict_protocol_loop")
        if isinstance(ppl, dict):
            prev_tick = ppl

    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nautilus_strategy_signal_path": str((data_dir / "nautilus_strategy_signal.json").resolve()),
        "nautilus_sidecar": naut,
        "btc_prediction_path": str((repo_root / "prediction_agent" / "btc_prediction_output.json").resolve()),
        "btc_prediction": pred,
        "fusion": {
            "vote_nautilus": vn,
            "vote_ml": vm,
            "vote_btc_future": v_bf,
            "vote_btc_future_raw": v_bf_raw,
            "btc_future_direction": _btc_future_direction(int(v_bf)),
            "sum": fused,
            "label": label,
            "nautilus_detail": nlab,
            "ml_detail": mlab,
            "btc_future_detail": bf_lab,
            "btc_future_meta": bf_meta_out,
        },
        "swarm_keypoints": _swarm_keypoints_for_fusion(repo_root),
    }
    umb = _usd_btc_macro_for_sidecar(repo_root)
    if umb:
        out["usd_btc_macro"] = umb
    lt = _liquidation_tape_for_sidecar(repo_root)
    if lt:
        out["liquidation_tape"] = lt
    if prev_tick:
        out["predict_protocol_loop"] = prev_tick
    return out


def write_fused_sidecar(
    repo_root: Path | None = None,
    *,
    btc_prediction_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    path = fused_sidecar_path(root)
    prev = _read_json(path)
    doc = build_fusion_payload(root, previous=prev, btc_prediction_override=btc_prediction_override)
    _atomic_write(path, doc)
    return doc


def record_protocol_tick(repo_root: Path, tick: dict[str, Any]) -> dict[str, Any] | None:
    """Merge ``predict_protocol_loop`` from live loop; preserves other keys when possible."""
    path = fused_sidecar_path(repo_root)
    prev = _read_json(path)
    if prev is None:
        doc = build_fusion_payload(repo_root, previous=None)
    else:
        doc = build_fusion_payload(repo_root, previous=prev)
    doc["predict_protocol_loop"] = {
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **tick,
    }
    _atomic_write(path, doc)
    return doc


def briefing_line_nautilus_fusion(*, max_chars: int, repo_root: Path | None = None) -> str:
    if not _env_truthy("SYGNIF_BRIEFING_INCLUDE_NAUTILUS_FUSION"):
        return ""
    root = repo_root or _repo_root()
    path = fused_sidecar_path(root)
    if not path.is_file():
        return ""
    try:
        age_s = time.time() - path.stat().st_mtime
    except OSError:
        return ""
    try:
        max_h = float(os.environ.get("SYGNIF_BRIEFING_NAUTILUS_FUSION_MAX_AGE_H", "24"))
    except ValueError:
        max_h = 24.0
    if age_s > max(60.0, max_h * 3600.0):
        return ""
    data = _read_json(path)
    if not data:
        return ""
    gen = str(data.get("generated_utc", "?"))[:19]
    fus = data.get("fusion") or {}
    lab = fus.get("label", "?")
    line = (
        f"NAU_FUSE|utc={gen}|fuse={lab}|n={fus.get('nautilus_detail')}|ml={fus.get('ml_detail')}"
        f"|bf={fus.get('btc_future_detail', '?')}"
    )
    ppl = data.get("predict_protocol_loop")
    if isinstance(ppl, dict) and ppl.get("target_side") is not None:
        ts = str(ppl.get("recorded_utc", "?"))[:16]
        line += f"|loop@{ts} tgt={ppl.get('target_side')}"
    if len(line) > max_chars:
        line = line[: max_chars - 3] + "..."
    return line


def main() -> int:
    ap = argparse.ArgumentParser(description="Nautilus + BTC predict + protocol fusion sidecar")
    ap.add_argument("cmd", nargs="?", default="sync", choices=("sync", "print-briefing"))
    args = ap.parse_args()
    root = _repo_root()
    if args.cmd == "sync":
        doc = write_fused_sidecar(root)
        print(json.dumps({"ok": True, "path": str(fused_sidecar_path(root)), "fusion": doc.get("fusion")}))
        return 0
    line = briefing_line_nautilus_fusion(max_chars=400, repo_root=root)
    print(line or "(empty — enable SYGNIF_BRIEFING_INCLUDE_NAUTILUS_FUSION for briefing consumer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
