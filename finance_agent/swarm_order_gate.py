"""
Gating helpers for Swarm + Nautilus/ML/**btc_future** fusion before predict-protocol demo orders.

**Evaluation order** (``swarm_fusion_allows`` — single function, short-circuits on first fail):

1. ``target`` / ``no_edge`` — ``swarm_conflict`` (optional).
2. **btc_future** branch ``ok`` (when required and branch enabled).
3. Raw **bf** vote (``sources.bf``) vs ``target`` + flat-pass.
4. Raw **hm** vote (``sources.hm``) vs ``target`` + flat-pass.
5. **Swarm mean** band + optional bull/bear label blocks.
6. Nautilus sidecar **freshness** (optional max age).
7. Nautilus **votes** (not_contrary / align).
8. **ML logreg** confidence + UP/DOWN on ``fusion_doc`` (not ``predict_out``).
9. **USD/BTC macro** (optional).
9b. **Public liquidation tape** (optional ``liquidation_tape`` on ``fusion_doc``) when ``SWARM_ORDER_LIQUIDATION_TAPE_GATE=1``.
10. **Strategy guidelines** (+ optional Hivemind explore fusion, then optional unreachable-ML fusion).
11. **Fusion align** (sum label + ``vote_btc_future`` in ``fusion_doc``) when ``SWARM_ORDER_REQUIRE_FUSION_ALIGN``.

**Nautilus research + prediction protocol sidecar (accuracy):**

- ``SWARM_ORDER_NAUTILUS_MAX_AGE_MIN`` — if **> 0**, reject when ``fusion_doc["nautilus_sidecar"].generated_utc``
  is older than this many minutes (requires ``fusion_doc`` from ``write_fused_sidecar``).
- ``SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY`` — reject **long** if ``vote_nautilus <= -1``, **short** if
  ``vote_nautilus >= 1`` (Nautilus sidecar bias must not point the wrong way).
- ``SWARM_ORDER_REQUIRE_NAUTILUS_ALIGN`` — stricter: **long** needs ``vote_nautilus >= 1`` (short: ``<=-1``);
  ``SWARM_ORDER_NAUTILUS_FLAT_PASS=1`` allows ``vote_nautilus == 0``.
- ``SWARM_ORDER_FUSION_REQUIRE_STRONG`` — fusion label must be **strong_long** / **strong_short** only
  (|Nautilus+ML+bf sum| >= 2).
- ``SWARM_ORDER_ML_LOGREG_MIN_CONF`` — minimum ``direction_logistic.confidence`` (0–100) on embedded
  ``btc_prediction`` in ``fusion_doc``; **long** requires label **UP**, **short** requires **DOWN**.

When ``SWARM_ORDER_REQUIRE_FUSION_ALIGN=1`` (default in ``swarm_gated_predict_protocol_order``):

- ``SWARM_ORDER_FUSION_ALIGN_LABEL`` (default on): fused sum-label must be lean/strong long or short.
- ``SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE`` (on in that script): ``vote_btc_future`` must align with the
  protocol target (demo position **bf** vote). Set ``SWARM_ORDER_BTC_FUTURE_FLAT_PASS=1`` to allow
  ``vote_btc_future==0`` with long/short. If ``SWARM_ORDER_BTC_FUTURE_FLAT_PASS`` is **unset**, the gate
  falls back to ``SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS`` (``swarm_auto`` sets both to ``1``).

Optional **raw Swarm hm vote** (Truthcoin Hivemind liveness, ``sources.hm``):

- ``SWARM_ORDER_REQUIRE_HIVEMIND_VOTE=1`` — long requires **hm** ``>= 1``, short requires ``<= -1``.
  ``SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS=1`` allows ``hm`` vote ``0``.

Optional **raw Swarm bf vote** (from ``swarm_knowledge_output.json`` ``sources.bf``), before fusion:

- ``SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE=1`` — shorthand for **btc_future-governed** entries: same as
  ``SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE=1`` (override off with ``SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE=0``).
- ``SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE=1`` — long requires **bf** vote ``>= 1``, short requires ``<= -1``.
  ``SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS=1`` allows ``bf`` vote ``0`` (demo flat).

**USD broad index vs BTC** (``usd_btc_macro`` on fusion sidecar): optional gate ``SWARM_ORDER_USD_BTC_MACRO_GATE=1``
— if last overlapping daily USD index return is **up** (surge) and 20d Pearson correlation is sufficiently **negative**,
**long** can be blocked; symmetric **dump** rule for **short**. Defaults: ``SWARM_ORDER_USD_INDEX_SURGE_RET=0.001``,
``SWARM_ORDER_USD_INDEX_DUMP_RET=-0.001``, ``SWARM_ORDER_USD_INDEX_MIN_NEG_CORR=-0.12``. Missing macro → pass.

See ``scripts/swarm_gated_predict_protocol_order.py`` and ``scripts/swarm_auto_predict_protocol_loop.py`` (both
``setdefault`` a wide mean band when unset). The in-function fallback in ``swarm_fusion_allows`` matches that band
(``-0.25`` / ``0.25``) so ``btc_predict_protocol_loop`` without a launcher still uses the same bias as ``swarm_auto``
until env overrides.

**Portfolio / Swarm authority (predict loop):** ``SWARM_PORTFOLIO_AUTHORITY=1`` with ``SYGNIF_SWARM_GATE_LOOP`` +
``--execute`` — when Swarm **blocks** the model’s opposite **entry**, the loop also **skips** the reduce-only
**flip-close** that would only flatten (see ``scripts/btc_predict_protocol_loop.py``). Default ``swarm_auto`` uses
``SWARM_PORTFOLIO_AUTHORITY=0`` so reduce-only closes may run and the venue can stay flat until gates allow a new open.
**New opens** still follow ``decide_side`` (prediction protocol) but only after ``swarm_fusion_allows`` passes on **Swarm** + **fusion sidecar**
(``write_fused_sidecar``: Nautilus research + ML + **btc_future**). Defaults for the full stack:
``scripts/swarm_authority_protocol_loop.sh`` or ``scripts/swarm_auto_predict_protocol_loop.py`` (sets authority,
``SYGNIF_PROTOCOL_FUSION_SYNC``, fusion align, Nautilus non-contradiction, optional Hivemind).

**SygnifStrategy-style guidelines (optional):** ``SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES=1`` requires the live
``fit_predict_live`` payload (``predict_out``) to carry ``strategy_guidelines`` from ``btc_strategy_guidelines``:

- **Long:** at least one of **sygnif_swing**-style (``sf_long`` + TA proxy ≥ split) or **orb_long** (session ORB
  first breakout on BTC/ETH), mirroring ``SygnifStrategy`` ``enter_tag`` logic.
- **Short:** **sygnif_swing_short**-style (``sf_short`` + TA proxy ≤ split). (No ORB short in the Freqtrade module.)

**Guideline + Hivemind fusion (optional):** ``SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION=1`` (recommended with
``SYGNIF_PREDICT_HIVEMIND_FUSION``) — if the **strict** ORB/swing check fails, the gate can still **pass** when
``predict_out["predictions"]["hivemind"]`` shows a live explore snapshot **and** the ML/Nautilus enhanced label
aligns with the protocol target (BULLISH family for **long**, BEARISH family for **short**), plus either
Hivemind **liveness** ``vote >= 1`` (active voting slots) or ``markets_trading_n`` ≥
``SYGNIF_GUIDELINE_HM_MIN_MARKETS`` (default **1**). Unreachable explore (``ok: false``) does **not** use this path.

**Guideline + ML when Hivemind unreachable (optional):** ``SWARM_ORDER_GUIDELINE_HIVEMIND_UNREACHABLE_ML=1`` with
fusion on — if explore is **not** ``ok`` but ``direction_logistic`` on ``predict_out`` already meets the **same**
``SWARM_ORDER_ML_LOGREG_MIN_CONF`` floor (and UP/DOWN matches ``target``) **and** ``consensus_nautilus_enhanced``
aligns, the strict guideline check can still **pass** (degraded substitute when Truthcoin CLI/RPC is down). When
``SWARM_ORDER_ML_LOGREG_MIN_CONF`` is **0** (ML gate off), this path is disabled.

Knobs: ``SWARM_ORDER_GUIDELINE_TA_SPLIT_LONG`` / ``SWARM_ORDER_GUIDELINE_TA_SPLIT_SHORT`` (default **50**),
``SYGNIF_GUIDELINE_ORB_MINUTES``, ``SYGNIF_GUIDELINE_ORB_MIN_RANGE_PCT``.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


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


def _require_btc_future_ok() -> bool:
    v = os.environ.get("SWARM_ORDER_REQUIRE_BTC_FUTURE", "1").strip().lower()
    return v in ("1", "true", "yes", "on", "")


def _require_btc_future_vote_align() -> bool:
    """
    When set, require **bf** (demo linear position vote) to agree with the protocol target.

    Long  → vote >= +1; short → vote <= -1. Use ``SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS=1`` to allow bf==0.

    ``SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE=1`` enables vote alignment unless
    ``SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE`` is explicitly ``0``/``off``.
    """
    raw = os.environ.get("SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if _env_truthy("SYGNIF_SWARM_BTC_FUTURE_GOVERNANCE"):
        return True
    if not raw:
        return False
    return raw in ("1", "true", "yes", "on")


def _btc_future_vote_flat_pass() -> bool:
    return _env_truthy("SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS")


def _btc_future_fusion_flat_pass() -> bool:
    """
    Fusion ``vote_btc_future==0`` may still align with long/short when this is on.

    Prefer ``SWARM_ORDER_BTC_FUTURE_FLAT_PASS``; if **unset**, fall back to
    ``SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS`` so ``swarm_auto`` / operators only need one knob.
    Explicit ``SWARM_ORDER_BTC_FUTURE_FLAT_PASS=0`` disables (no fallback).
    """
    raw = (os.environ.get("SWARM_ORDER_BTC_FUTURE_FLAT_PASS") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return _env_truthy("SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS")


def _require_hivemind_vote_align() -> bool:
    """When set, require ``sources.hm`` (Truthcoin Hivemind liveness vote) to agree with target."""
    return _env_truthy("SWARM_ORDER_REQUIRE_HIVEMIND_VOTE")


def _hivemind_vote_flat_pass() -> bool:
    return _env_truthy("SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS")


def _sygnif_strategy_guidelines_on() -> bool:
    return _env_truthy("SWARM_ORDER_SYGNIF_STRATEGY_GUIDELINES")


def _guideline_hivemind_fusion_on() -> bool:
    return _env_truthy("SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION")


def _guideline_hm_min_markets() -> int:
    raw = (os.environ.get("SYGNIF_GUIDELINE_HM_MIN_MARKETS") or "").strip()
    if not raw:
        return 1
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def _prediction_enhanced_label(predict_out: dict[str, Any] | None) -> str:
    if not isinstance(predict_out, dict):
        return ""
    pred = predict_out.get("predictions")
    if not isinstance(pred, dict):
        return ""
    return str(pred.get("consensus_nautilus_enhanced") or "").strip().upper()


def _predict_hivemind_explore_and_vote(predict_out: dict[str, Any] | None) -> tuple[dict[str, Any], int]:
    """``predictions.hivemind`` → ``(explore_dict, vote)``."""
    if not isinstance(predict_out, dict):
        return {}, 0
    pred = predict_out.get("predictions")
    if not isinstance(pred, dict):
        return {}, 0
    hm = pred.get("hivemind")
    if not isinstance(hm, dict):
        return {}, 0
    ex = hm.get("explore") if isinstance(hm.get("explore"), dict) else {}
    try:
        v = int(hm.get("vote") or 0)
    except (TypeError, ValueError):
        v = 0
    return ex, v


def _predict_out_direction_logreg(predict_out: dict[str, Any] | None) -> tuple[str, float]:
    """``predictions.direction_logistic`` → ``(label_upper, confidence_0_100)``."""
    if not isinstance(predict_out, dict):
        return "", 0.0
    pred = predict_out.get("predictions")
    if not isinstance(pred, dict):
        return "", 0.0
    dlr = pred.get("direction_logistic") if isinstance(pred.get("direction_logistic"), dict) else {}
    lab = str(dlr.get("label") or "").strip().upper()
    try:
        conf = float(dlr.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return lab, conf


def _guideline_unreachable_ml_passes(
    *, target: str, predict_out: dict[str, Any] | None
) -> tuple[bool, str]:
    """
    When Hivemind explore is down, optionally treat **aligned ML + enhanced label** as guideline fusion.

    Uses the **same** ``SWARM_ORDER_ML_LOGREG_MIN_CONF`` floor as ``_gate_ml_logreg_conf`` so passing here
    implies the later ML gate on ``fusion_doc`` can pass when the embedded prediction matches ``predict_out``.
    """
    if not _guideline_hivemind_fusion_on() or not _env_truthy("SWARM_ORDER_GUIDELINE_HIVEMIND_UNREACHABLE_ML"):
        return False, ""
    min_c = _env_float("SWARM_ORDER_ML_LOGREG_MIN_CONF", 0.0)
    if min_c <= 0.0:
        return False, ""
    ex, _vote = _predict_hivemind_explore_and_vote(predict_out)
    if bool(ex.get("ok")):
        return False, ""
    lab_e = _prediction_enhanced_label(predict_out)
    d_lab, d_conf = _predict_out_direction_logreg(predict_out)
    if target == "long":
        if lab_e not in ("BULLISH", "STRONG_BULLISH"):
            return False, ""
        if d_lab != "UP" or d_conf < min_c:
            return False, ""
        return True, "guideline_fusion_long_unreachable_ml_stack"
    if target == "short":
        if lab_e not in ("BEARISH", "STRONG_BEARISH"):
            return False, ""
        if d_lab != "DOWN" or d_conf < min_c:
            return False, ""
        return True, "guideline_fusion_short_unreachable_ml_stack"
    return False, ""


def _guideline_hivemind_fusion_passes(
    *, target: str, predict_out: dict[str, Any] | None
) -> tuple[bool, str]:
    """
    Soft path: live Hivemind snapshot + enhanced label agrees with ``target``.

    Uses the same ``vote`` / ``explore`` fields as ``fit_predict_live`` (liveness + market activity),
    not a directional Truthcoin price oracle.
    """
    if not _guideline_hivemind_fusion_on():
        return False, ""
    ex, vote = _predict_hivemind_explore_and_vote(predict_out)
    if not bool(ex.get("ok")):
        return False, ""
    try:
        mkt = int(ex.get("markets_trading_n") or 0)
    except (TypeError, ValueError):
        mkt = 0
    min_m = _guideline_hm_min_markets()
    hm_signal = vote >= 1 or mkt >= min_m
    if not hm_signal:
        return False, ""
    lab = _prediction_enhanced_label(predict_out)
    if target == "long":
        if lab not in ("BULLISH", "STRONG_BULLISH"):
            return False, ""
        if vote >= 1:
            return True, "guideline_fusion_long_hm_liveness"
        return True, "guideline_fusion_long_hm_markets"
    if target == "short":
        if lab not in ("BEARISH", "STRONG_BEARISH"):
            return False, ""
        if vote >= 1:
            return True, "guideline_fusion_short_hm_liveness"
        return True, "guideline_fusion_short_hm_markets"
    return False, ""


def _gate_sygnif_strategy_guidelines(
    *, target: str | None, predict_out: dict[str, Any] | None
) -> tuple[bool, str]:
    """
    Optional discipline from proven Freqtrade tags ``sygnif_swing`` / ``orb_long`` (and swing short).

    When ``strategy_guidelines`` is missing or ``ok`` is false, the gate **passes** (do not hard-block on
    guideline compute errors — see ``btc_strategy_guidelines`` detail).

    With ``SWARM_ORDER_GUIDELINE_HIVEMIND_FUSION``, a failing strict check can still pass when Hivemind
    explore is live and the enhanced prediction label aligns (see module docstring).
    """
    if not _sygnif_strategy_guidelines_on():
        return True, ""
    if target not in ("long", "short"):
        return True, ""
    if not isinstance(predict_out, dict):
        return True, ""
    g = predict_out.get("strategy_guidelines")
    if not isinstance(g, dict):
        return True, ""
    if not g.get("ok"):
        return True, ""
    if target == "long":
        if bool(g.get("sygnif_swing_long_ok")) or bool(g.get("orb_long_ok")):
            return True, ""
        strict_reason = "guideline_long_need_sygnif_swing_or_orb"
    else:
        if bool(g.get("sygnif_swing_short_ok")):
            return True, ""
        strict_reason = "guideline_short_need_sygnif_swing_short"
    ok_f, detail_f = _guideline_hivemind_fusion_passes(target=target, predict_out=predict_out)
    if ok_f:
        return True, detail_f or "guideline_fusion_ok"
    ok_u, det_u = _guideline_unreachable_ml_passes(target=target, predict_out=predict_out)
    if ok_u:
        return True, det_u or "guideline_fusion_ok"
    return False, strict_reason


def _fusion_align_label_on() -> bool:
    """Default True when unset (label gate is part of fusion aligner)."""
    raw = os.environ.get("SWARM_ORDER_FUSION_ALIGN_LABEL", "").strip()
    if not raw:
        return True
    return raw.lower() in ("1", "true", "yes", "on")


def _fusion_align_btc_future_on() -> bool:
    """Default False unless set — ``swarm_gated_predict_protocol_order`` sets it to 1."""
    raw = os.environ.get("SWARM_ORDER_FUSION_ALIGN_BTC_FUTURE", "").strip()
    if not raw:
        return False
    return raw.lower() in ("1", "true", "yes", "on")


def _nautilus_max_age_min() -> float:
    return _env_float("SWARM_ORDER_NAUTILUS_MAX_AGE_MIN", 0.0)


def _parse_iso_utc_z(s: str) -> datetime | None:
    raw = (s or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fusion_vote_nautilus(fus: dict[str, Any]) -> int:
    try:
        return int(fus.get("vote_nautilus") or 0)
    except (TypeError, ValueError):
        return 0


def _gate_nautilus_sidecar_freshness(fusion_doc: dict[str, Any] | None) -> tuple[bool, str]:
    max_m = _nautilus_max_age_min()
    if max_m <= 0.0:
        return True, ""
    if not fusion_doc:
        return False, "fusion_doc_missing_for_nautilus_age"
    ns = fusion_doc.get("nautilus_sidecar") if isinstance(fusion_doc.get("nautilus_sidecar"), dict) else {}
    gen = _parse_iso_utc_z(str(ns.get("generated_utc") or ""))
    if gen is None:
        return False, "nautilus_sidecar_missing_or_bad_ts"
    age_m = (datetime.now(timezone.utc) - gen).total_seconds() / 60.0
    if age_m > max_m:
        return False, f"nautilus_sidecar_stale age_min={age_m:.1f} max={max_m}"
    return True, ""


def _gate_nautilus_votes(*, target: str, fusion_doc: dict[str, Any] | None) -> tuple[bool, str]:
    """Nautilus research vote from fusion (same as ``nautilus_protocol_fusion`` sum term)."""
    if not _env_truthy("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY") and not _env_truthy(
        "SWARM_ORDER_REQUIRE_NAUTILUS_ALIGN"
    ):
        return True, ""
    if not fusion_doc:
        return False, "fusion_doc_missing_for_nautilus_vote"
    fus = fusion_doc.get("fusion") if isinstance(fusion_doc.get("fusion"), dict) else {}
    vn = _fusion_vote_nautilus(fus)
    if _env_truthy("SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY"):
        if target == "long" and vn <= -1:
            return False, f"nautilus_contra_long vote_nautilus={vn}"
        if target == "short" and vn >= 1:
            return False, f"nautilus_contra_short vote_nautilus={vn}"
    if _env_truthy("SWARM_ORDER_REQUIRE_NAUTILUS_ALIGN"):
        flat_ok = _env_truthy("SWARM_ORDER_NAUTILUS_FLAT_PASS")
        if target == "long":
            if vn >= 1:
                pass
            elif vn == 0 and flat_ok:
                pass
            else:
                return False, f"nautilus_align_long vote_nautilus={vn}"
        elif target == "short":
            if vn <= -1:
                pass
            elif vn == 0 and flat_ok:
                pass
            else:
                return False, f"nautilus_align_short vote_nautilus={vn}"
    return True, ""


def _gate_usd_btc_macro(*, target: str, fusion_doc: dict[str, Any] | None) -> tuple[bool, str]:
    """
    Optional macro gate: strong USD day + historically negative BTC–USD correlation → cautious on long;
    USD dump + same → cautious on short.
    """
    if not _env_truthy("SWARM_ORDER_USD_BTC_MACRO_GATE"):
        return True, ""
    if not fusion_doc:
        return True, ""
    umb = fusion_doc.get("usd_btc_macro")
    if not isinstance(umb, dict):
        return True, ""
    try:
        u_ret = float(umb.get("last_usd_index_return") or 0.0)
    except (TypeError, ValueError):
        u_ret = 0.0
    corrs = umb.get("pearson_correlation_daily_returns")
    if not isinstance(corrs, dict):
        return True, ""
    p20 = corrs.get("pearson_last_20d")
    if p20 is None:
        return True, ""
    try:
        p20f = float(p20)
    except (TypeError, ValueError):
        return True, ""
    surge = _env_float("SWARM_ORDER_USD_INDEX_SURGE_RET", 0.001)
    dump = _env_float("SWARM_ORDER_USD_INDEX_DUMP_RET", -0.001)
    min_neg = _env_float("SWARM_ORDER_USD_INDEX_MIN_NEG_CORR", -0.12)
    if target == "long" and u_ret >= surge and p20f <= min_neg:
        return False, f"usd_btc_macro_block_long usd_ret={u_ret:.5f} corr20={p20f:.3f}"
    if target == "short" and u_ret <= dump and p20f <= min_neg:
        return False, f"usd_btc_macro_block_short usd_ret={u_ret:.5f} corr20={p20f:.3f}"
    return True, ""


def _gate_liquidation_tape(*, target: str, fusion_doc: dict[str, Any] | None) -> tuple[bool, str]:
    """
    Block **long** when recent tape shows dominant **long-side** liquidations (forced sells / bearish flush).

    Block **short** when dominant **short-side** liquidations (forced buys / bullish flush).

    Uses ``tape_pressure_vote`` from ``nautilus_protocol_fusion._liquidation_tape_for_sidecar``:
    ``-1`` long flush, ``+1`` short flush, ``0`` quiet/balanced.
    """
    if not _env_truthy("SWARM_ORDER_LIQUIDATION_TAPE_GATE"):
        return True, ""
    if not fusion_doc:
        return True, ""
    lt = fusion_doc.get("liquidation_tape")
    if not isinstance(lt, dict) or not lt.get("ok"):
        return True, ""
    try:
        v = int(lt.get("tape_pressure_vote") or 0)
    except (TypeError, ValueError):
        v = 0
    lab = str(lt.get("tape_label") or "")
    if target == "long" and v <= -1:
        return False, f"liq_tape_block_long vote={v} label={lab}"
    if target == "short" and v >= 1:
        return False, f"liq_tape_block_short vote={v} label={lab}"
    return True, ""


def _gate_ml_logreg_conf(*, target: str, fusion_doc: dict[str, Any] | None) -> tuple[bool, str]:
    min_c = _env_float("SWARM_ORDER_ML_LOGREG_MIN_CONF", 0.0)
    if min_c <= 0.0:
        return True, ""
    if not fusion_doc:
        return False, "fusion_doc_missing_for_ml_conf"
    pred = fusion_doc.get("btc_prediction") if isinstance(fusion_doc.get("btc_prediction"), dict) else {}
    pr = pred.get("predictions") if isinstance(pred.get("predictions"), dict) else {}
    dlr = pr.get("direction_logistic") if isinstance(pr.get("direction_logistic"), dict) else {}
    try:
        conf = float(dlr.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    lab = str(dlr.get("label") or "").strip().upper()
    if target == "long":
        if lab != "UP":
            return False, f"ml_logreg_label={lab!r}_need_UP"
        if conf < min_c:
            return False, f"ml_logreg_conf={conf:.1f} < {min_c}"
    elif target == "short":
        if lab != "DOWN":
            return False, f"ml_logreg_label={lab!r}_need_DOWN"
        if conf < min_c:
            return False, f"ml_logreg_conf={conf:.1f} < {min_c}"
    return True, ""


def swarm_fusion_allows(
    *,
    target: str | None,
    swarm: dict[str, Any],
    fusion_doc: dict[str, Any] | None,
    predict_out: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if target is None:
        return False, "no_edge"
    min_long = _env_float("SWARM_ORDER_MIN_MEAN_LONG", -0.25)
    max_short = _env_float("SWARM_ORDER_MAX_MEAN_SHORT", 0.25)
    try:
        mean = float(swarm.get("swarm_mean") or 0.0)
    except (TypeError, ValueError):
        mean = 0.0
    conflict = bool(swarm.get("swarm_conflict"))
    label = str(swarm.get("swarm_label") or "")

    if _env_truthy("SWARM_ORDER_BLOCK_CONFLICT") and conflict:
        return False, "swarm_conflict"

    bf = swarm.get("btc_future") if isinstance(swarm.get("btc_future"), dict) else {}
    if _require_btc_future_ok() and bf.get("enabled") and not bf.get("ok"):
        return False, "btc_future_not_ok"

    if _require_btc_future_vote_align():
        src = swarm.get("sources") if isinstance(swarm.get("sources"), dict) else {}
        bf_src = src.get("bf") if isinstance(src.get("bf"), dict) else {}
        try:
            v_bf_src = int(bf_src.get("vote") or 0)
        except (TypeError, ValueError):
            v_bf_src = 0
        flat_ok = _btc_future_vote_flat_pass()
        if target == "long":
            if v_bf_src >= 1:
                pass
            elif v_bf_src == 0 and flat_ok:
                pass
            else:
                return False, f"swarm_bf_vote={v_bf_src}_need_long_or_flat_ok"
        elif target == "short":
            if v_bf_src <= -1:
                pass
            elif v_bf_src == 0 and flat_ok:
                pass
            else:
                return False, f"swarm_bf_vote={v_bf_src}_need_short_or_flat_ok"

    if _require_hivemind_vote_align():
        src = swarm.get("sources") if isinstance(swarm.get("sources"), dict) else {}
        hm_src = src.get("hm") if isinstance(src.get("hm"), dict) else {}
        try:
            v_hm_src = int(hm_src.get("vote") or 0)
        except (TypeError, ValueError):
            v_hm_src = 0
        hm_flat = _hivemind_vote_flat_pass()
        if target == "long":
            if v_hm_src >= 1:
                pass
            elif v_hm_src == 0 and hm_flat:
                pass
            else:
                return False, f"swarm_hm_vote={v_hm_src}_need_long_or_flat_ok"
        elif target == "short":
            if v_hm_src <= -1:
                pass
            elif v_hm_src == 0 and hm_flat:
                pass
            else:
                return False, f"swarm_hm_vote={v_hm_src}_need_short_or_flat_ok"

    if target == "long":
        if mean < min_long:
            return False, f"swarm_mean {mean:.3f} < SWARM_ORDER_MIN_MEAN_LONG ({min_long})"
        if "BEAR" in label.upper() and _env_truthy("SWARM_ORDER_BLOCK_SWARM_BEAR_LABEL"):
            return False, "swarm_label_bear"
    elif target == "short":
        if mean > max_short:
            return False, f"swarm_mean {mean:.3f} > SWARM_ORDER_MAX_MEAN_SHORT ({max_short})"
        if _env_truthy("SWARM_ORDER_BLOCK_SWARM_BULL_LABEL") and "BULL" in label.upper():
            return False, "swarm_label_bull"

    ok_age, reason_age = _gate_nautilus_sidecar_freshness(fusion_doc)
    if not ok_age:
        return False, reason_age

    ok_nv, reason_nv = _gate_nautilus_votes(target=target, fusion_doc=fusion_doc)
    if not ok_nv:
        return False, reason_nv

    ok_ml, reason_ml = _gate_ml_logreg_conf(target=target, fusion_doc=fusion_doc)
    if not ok_ml:
        return False, reason_ml

    ok_um, reason_um = _gate_usd_btc_macro(target=target, fusion_doc=fusion_doc)
    if not ok_um:
        return False, reason_um

    ok_lq, reason_lq = _gate_liquidation_tape(target=target, fusion_doc=fusion_doc)
    if not ok_lq:
        return False, reason_lq

    ok_sg, reason_sg = _gate_sygnif_strategy_guidelines(target=target, predict_out=predict_out)
    if not ok_sg:
        return False, reason_sg

    # --- Nautilus+ML+btc_future fusion aligner (``swarm_nautilus_protocol_sidecar.json``) ---
    if not _env_truthy("SWARM_ORDER_REQUIRE_FUSION_ALIGN"):
        return True, reason_sg or "ok"
    if not fusion_doc:
        return False, "fusion_doc_missing"

    fus = fusion_doc.get("fusion") if isinstance(fusion_doc.get("fusion"), dict) else {}

    # Sum-based label (Nautilus + ML + **btc_future** vote)
    if _fusion_align_label_on():
        flab = str(fus.get("label") or "")
        if _env_truthy("SWARM_ORDER_FUSION_REQUIRE_STRONG"):
            if target == "long" and flab != "strong_long":
                return False, f"fusion_label={flab!r}_want_strong_long"
            if target == "short" and flab != "strong_short":
                return False, f"fusion_label={flab!r}_want_strong_short"
        else:
            if target == "long" and flab not in ("strong_long", "lean_long"):
                return False, f"fusion_label={flab!r}_want_long"
            if target == "short" and flab not in ("strong_short", "lean_short"):
                return False, f"fusion_label={flab!r}_want_short"

    # **btc_future** (demo position) vote — venue anchor for swarm.orders
    if _fusion_align_btc_future_on():
        try:
            v_bf = int(fus.get("vote_btc_future") or 0)
        except (TypeError, ValueError):
            v_bf = 0
        flat_ok = _btc_future_fusion_flat_pass()
        if target == "long":
            if v_bf >= 1:
                pass
            elif v_bf == 0 and flat_ok:
                pass
            else:
                return False, f"btc_future_vote={v_bf}_need_long_or_flat_ok"
        elif target == "short":
            if v_bf <= -1:
                pass
            elif v_bf == 0 and flat_ok:
                pass
            else:
                return False, f"btc_future_vote={v_bf}_need_short_or_flat_ok"

    return True, reason_sg or "ok"
