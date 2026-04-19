"""
Swarm **weak-point bundle** for finance-agent / predict-protocol ops.

Aggregates:
  - Live ``compute_swarm()`` (``swarm_knowledge``) — compact vote digest
  - Optional persisted ``prediction_agent/swarm_knowledge_output.json``
  - ``build_bybit_closed_pnl_report()`` when ``SYGNIF_SWARM_BYBIT_CLOSED_PNL`` is enabled
  - Tail stats from ``prediction_agent/swarm_predict_protocol_dataset.jsonl``
  - ``prediction_agent/swarm_bybit_ft_state.json`` (entry cooldown / open-fail state)

Read-only except optional env ``setdefault`` for closed-PnL (same as ``swarm_demo_pnl_report``).
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _prediction_agent_dir(repo: Path) -> Path:
    return repo / "prediction_agent"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_swarm_from_compute(sw: dict[str, Any]) -> dict[str, Any]:
    """Strip ``compute_swarm()`` to stable, Telegram-safe fields."""
    if not isinstance(sw, dict):
        return {"ok": False, "detail": "not_a_dict"}
    src = sw.get("sources") or {}
    votes: dict[str, Any] = {}
    if isinstance(src, dict):
        for k, v in src.items():
            if not isinstance(v, dict):
                continue
            votes[k] = {"vote": v.get("vote"), "detail": (str(v.get("detail") or "")[:120])}
    return {
        "ok": True,
        "generated_utc": sw.get("generated_utc"),
        "swarm_mean": sw.get("swarm_mean"),
        "swarm_label": sw.get("swarm_label"),
        "swarm_conflict": sw.get("swarm_conflict"),
        "sources_n": sw.get("sources_n"),
        "votes": votes,
    }


def compact_swarm_file(repo: Path) -> dict[str, Any]:
    p = _prediction_agent_dir(repo) / "swarm_knowledge_output.json"
    if not p.is_file():
        return {"ok": False, "detail": "missing_file", "path": str(p)}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "detail": str(exc)[:120], "path": str(p)}
    return compact_swarm_from_compute(raw) | {"path": str(p), "from_file": True}


def analyze_closed_pnl_report(rep: dict[str, Any], *, tail_n: int = 50) -> dict[str, Any]:
    if not isinstance(rep, dict) or not rep.get("enabled"):
        return {"ok": False, "detail": "closed_pnl_disabled", "hint": "set SYGNIF_SWARM_BYBIT_CLOSED_PNL=1"}
    if not rep.get("ok"):
        return {
            "ok": False,
            "venue": rep.get("venue"),
            "symbol": rep.get("symbol"),
            "detail": rep.get("detail"),
            "retCode": rep.get("retCode"),
            "retMsg": rep.get("retMsg"),
        }
    recent = list(rep.get("recent") or [])
    if not recent:
        return {"ok": True, "n_closed": 0, "tail": []}

    def pnl(r: dict[str, Any]) -> float:
        try:
            return float(r.get("closed_pnl") or 0)
        except (TypeError, ValueError):
            return 0.0

    tail = recent[-tail_n:]
    pnls = [pnl(r) for r in tail]
    wins = sum(1 for x in pnls if x > 1e-9)
    losses = sum(1 for x in pnls if x < -1e-9)
    return {
        "ok": True,
        "venue": rep.get("venue"),
        "symbol": rep.get("symbol"),
        "n_closed_batch": rep.get("n_closed"),
        "sum_closed_pnl_usdt": rep.get("sum_closed_pnl_usdt"),
        "wins_batch": rep.get("wins"),
        "losses_batch": rep.get("losses"),
        f"last_{tail_n}_legs": {
            "n": len(tail),
            "wins": wins,
            "losses": losses,
            "sum": round(sum(pnls), 4),
            "avg": round(sum(pnls) / len(pnls), 4) if tail else 0.0,
            "best": round(max(pnls), 4) if pnls else 0.0,
            "worst": round(min(pnls), 4) if pnls else 0.0,
        },
    }


def analyze_predict_dataset_tail(repo: Path, *, max_lines: int = 400) -> dict[str, Any]:
    p = _prediction_agent_dir(repo) / "swarm_predict_protocol_dataset.jsonl"
    if not p.is_file():
        return {"ok": False, "detail": "missing_file", "path": str(p)}
    try:
        raw_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"ok": False, "detail": str(exc)[:120]}
    lines = [x for x in raw_lines if x.strip()][-max_lines:]
    if not lines:
        return {"ok": False, "detail": "empty_file"}
    reasons = Counter()
    targets = Counter()
    ok_c = blocked = 0
    last: dict[str, Any] = {}
    for line in lines:
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        pl = o.get("predict_protocol_line") or {}
        last = pl
        if pl.get("swarm_gate_ok"):
            ok_c += 1
        else:
            blocked += 1
            reasons[str(pl.get("swarm_reason") or "?")] += 1
        targets[str(pl.get("target_side") or "?")] += 1
    return {
        "ok": True,
        "path": str(p),
        "rows": len(lines),
        "swarm_gate_ok_true": ok_c,
        "swarm_gate_ok_false": blocked,
        "gate_ok_rate": round(ok_c / len(lines), 4) if lines else 0.0,
        "target_side_counts": dict(targets),
        "top_block_reasons": reasons.most_common(12),
        "last_row": {
            "iter": last.get("iter"),
            "ts_utc": last.get("ts_utc"),
            "target_side": last.get("target_side"),
            "swarm_gate_ok": last.get("swarm_gate_ok"),
            "swarm_reason": last.get("swarm_reason"),
            "open_target_side": last.get("open_target_side"),
        },
    }


def load_bybit_ft_state(repo: Path) -> dict[str, Any]:
    p = _prediction_agent_dir(repo) / "swarm_bybit_ft_state.json"
    if not p.is_file():
        return {"ok": False, "path": str(p), "detail": "missing"}
    try:
        o = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "detail": str(exc)[:120]}
    return {"ok": True, "path": str(p), "state": o}


def build_recommendations(
    *,
    swarm_live: dict[str, Any],
    swarm_file: dict[str, Any],
    cpnl: dict[str, Any],
    dataset: dict[str, Any],
    ft_state: dict[str, Any],
    neurolinked_connectivity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Actionable rows for operators + LLM follow-up (no auto mutation)."""
    recs: list[dict[str, Any]] = []

    votes = (swarm_live.get("votes") if swarm_live.get("ok") else None) or {}
    hm = votes.get("hm") or {}
    bf = votes.get("bf") or {}
    if str(hm.get("detail") or "").startswith("hivemind_unreachable"):
        recs.append(
            {
                "id": "hivemind_unreachable",
                "severity": "medium",
                "title": "Hivemind / Truthcoin liveness missing",
                "detail": "hm vote is 0 with unreachable detail — install/configure Truthcoin CLI or relax "
                "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE / flat-pass only after reviewing risk.",
                "env_hints": ["SYGNIF_TRUTHCOIN_DC_CLI", "SWARM_ORDER_REQUIRE_HIVEMIND_VOTE", "SWARM_ORDER_HIVEMIND_VOTE_FLAT_PASS"],
            }
        )

    top_blocks = (dataset.get("top_block_reasons") or []) if dataset.get("ok") else []
    if top_blocks:
        top1, n1 = top_blocks[0]
        if "nautilus_contra" in top1 and n1 >= max(10, int(len(top_blocks) * 0.15)):
            recs.append(
                {
                    "id": "nautilus_model_tension",
                    "severity": "medium",
                    "title": "Frequent Nautilus vs protocol target blocks",
                    "detail": f"Top block reason `{top1[:80]}` ({n1}x in recent window) — either wait for "
                    "sidecar alignment or adjust SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY / max age.",
                    "env_hints": [
                        "SWARM_ORDER_REQUIRE_NAUTILUS_NOT_CONTRARY",
                        "SWARM_ORDER_NAUTILUS_MAX_AGE_MIN",
                        "SYGNIF_PROTOCOL_FUSION_SYNC",
                    ],
                }
            )
        if top1.startswith("ml_logreg_label") and n1 >= 20:
            recs.append(
                {
                    "id": "logreg_direction_gate",
                    "severity": "low",
                    "title": "ML logreg direction often conflicts with target",
                    "detail": "Many blocks from logreg UP/DOWN vs target — review SWARM_ORDER_ML_LOGREG_MIN_CONF "
                    "and whether logreg should veto when fusion is MIXED.",
                    "env_hints": ["SWARM_ORDER_ML_LOGREG_MIN_CONF", "SWARM_ORDER_REQUIRE_FUSION_ALIGN"],
                }
            )

    tail = (cpnl.get("last_50_legs") or {}) if cpnl.get("ok") else {}
    if tail.get("n") and tail.get("losses", 0) >= max(30, int(tail["n"]) * 0.65):
        recs.append(
            {
                "id": "venue_churn",
                "severity": "high",
                "title": "Demo closed-leg churn / negative tail",
                "detail": f"Last {tail.get('n')} legs: wins={tail.get('wins')} losses={tail.get('losses')} "
                f"sum≈{tail.get('sum')} USDT — reduce notional, slow loop (SYGNIF_SWARM_LOOP_INTERVAL_SEC / "
                "--paced), widen discretionary-close guards, review SWARM_BYBIT_ENTRY_COOLDOWN_SEC.",
                "env_hints": [
                    "SYGNIF_PREDICT_OPEN_IMMEDIATE",
                    "SYGNIF_SWARM_LOOP_INTERVAL_SEC",
                    "SWARM_BYBIT_ENTRY_COOLDOWN_SEC",
                    "SYGNIF_PREDICT_MIN_DISCRETIONARY_CLOSE_SEC",
                    "PREDICT_LOOP_REFRESH_ALIGNED_EVERY_N",
                ],
            }
        )

    st = ft_state.get("state") if ft_state.get("ok") else {}
    syms = (st.get("symbols") or {}) if isinstance(st, dict) else {}
    for sym, row in syms.items():
        if not isinstance(row, dict):
            continue
        cf = int(row.get("consec_open_fails") or 0)
        if cf >= 3:
            recs.append(
                {
                    "id": "consec_open_fails",
                    "severity": "high",
                    "title": f"Repeated open failures on {sym}",
                    "detail": "Check Bybit demo keys, rate limits, and min order size; reset after successful open.",
                    "env_hints": ["SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS", "BYBIT_DEMO_API_KEY"],
                }
            )

    if bf.get("vote") not in (None, 0) and dataset.get("ok"):
        rate = float(dataset.get("gate_ok_rate") or 0)
        br = [x for x in top_blocks if str(x[0]).startswith("swarm_bf_vote")]
        br_n = sum(n for _, n in br)
        if br_n >= 30 and rate < 0.45:
            recs.append(
                {
                    "id": "bf_alignment",
                    "severity": "medium",
                    "title": "btc_future bf vote often blocks entries",
                    "detail": "Many swarm_bf_vote blocks while gate_ok_rate is low — flatten demo leg or align "
                    "SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS / portfolio authority policy.",
                    "env_hints": [
                        "SWARM_ORDER_REQUIRE_BTC_FUTURE_VOTE",
                        "SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS",
                        "SWARM_PORTFOLIO_AUTHORITY",
                    ],
                }
            )

    if not swarm_live.get("ok") and swarm_file.get("ok"):
        recs.append(
            {
                "id": "swarm_compute_fallback",
                "severity": "low",
                "title": "Live compute_swarm failed — using file snapshot only",
                "detail": str(swarm_live.get("detail") or "compute_swarm_error")[:200],
                "env_hints": ["BYBIT_DEMO_API_KEY", "SYGNIF_SWARM_BTC_FUTURE"],
            }
        )

    nl = neurolinked_connectivity if isinstance(neurolinked_connectivity, dict) else {}
    if nl.get("mismatch_suggest_url"):
        sug = str(nl.get("mismatch_suggest_url") or "")
        recs.append(
            {
                "id": "neurolinked_use_loopback_8889",
                "severity": "high",
                "title": "NeuroLinked market/Swarm feed points at wrong port",
                "detail": f"GET /api/sygnif/summary fails on `{nl.get('configured_primary')}` but succeeds on "
                f"`{sug}` — set SYGNIF_NEUROLINKED_HOST_URL and SYGNIF_NEUROLINKED_HTTP_URL to that base "
                "(systemd: sygnif-neurolinked uses :8889; :8888 is spot/BTC terminal). "
                "Otherwise BYBIT_MARKET lines never reach the brain (stale BTC context).",
                "env_hints": ["SYGNIF_NEUROLINKED_HTTP_URL", "SYGNIF_NEUROLINKED_HOST_URL"],
            }
        )
    elif (
        isinstance(nl.get("probes"), list)
        and nl.get("probes")
        and not nl.get("primary_ok")
        and not nl.get("working_url")
    ):
        recs.append(
            {
                "id": "neurolinked_summary_unreachable",
                "severity": "medium",
                "title": "NeuroLinked brain HTTP not reachable",
                "detail": "No working GET /api/sygnif/summary on loopback candidates — start "
                "`sygnif-neurolinked` or fix SYGNIF_NEUROLINKED_*_URL.",
                "env_hints": ["SYGNIF_NEUROLINKED_HTTP_URL", "SYGNIF_NEUROLINKED_HOST_URL"],
            }
        )

    return recs


def build_swarm_weak_points_bundle(
    repo_root: Path | None = None,
    *,
    dataset_tail_lines: int = 400,
    closed_pnl_tail: int = 50,
) -> dict[str, Any]:
    repo = repo_root or _repo_root()
    out: dict[str, Any] = {
        "schema": "sygnif.swarm_weak_points/v1",
        "repo": str(repo),
        "generated_utc": _utc_now_iso(),
    }

    swarm_live: dict[str, Any] = {"ok": False, "detail": "not_attempted"}
    try:
        from swarm_knowledge import compute_swarm  # noqa: PLC0415

        swarm_live = compact_swarm_from_compute(compute_swarm())
    except Exception as exc:  # noqa: BLE001
        swarm_live = {"ok": False, "detail": str(exc)[:240]}

    swarm_file = compact_swarm_file(repo)
    out["swarm_live"] = swarm_live
    out["swarm_file"] = swarm_file

    os.environ.setdefault("SYGNIF_SWARM_BYBIT_CLOSED_PNL", "1")
    os.environ.setdefault("SYGNIF_SWARM_BYBIT_CLOSED_PNL_MAX_ROWS", "500")
    os.environ.setdefault("SYGNIF_SWARM_BYBIT_CLOSED_PNL_MAX_LIST", "500")
    cpnl_raw: dict[str, Any] = {}
    try:
        from swarm_knowledge import build_bybit_closed_pnl_report  # noqa: PLC0415

        cpnl_raw = build_bybit_closed_pnl_report()
    except Exception as exc:  # noqa: BLE001
        cpnl_raw = {"enabled": True, "ok": False, "detail": str(exc)[:200]}
    cpnl = analyze_closed_pnl_report(cpnl_raw, tail_n=closed_pnl_tail)
    out["closed_pnl"] = cpnl

    dataset = analyze_predict_dataset_tail(repo, max_lines=dataset_tail_lines)
    out["predict_loop_dataset"] = dataset

    ft_state = load_bybit_ft_state(repo)
    out["bybit_ft_state"] = ft_state

    nl_diag: dict[str, Any] = {}
    try:
        from finance_agent.neurolinked_connectivity import diagnose_neurolinked_swarm_feed  # noqa: PLC0415
    except ImportError:
        try:
            from neurolinked_connectivity import diagnose_neurolinked_swarm_feed  # noqa: PLC0415
        except ImportError:
            diagnose_neurolinked_swarm_feed = None  # type: ignore[assignment,misc]
    if diagnose_neurolinked_swarm_feed is not None:
        try:
            nl_diag = diagnose_neurolinked_swarm_feed(timeout=1.5)
        except Exception as exc:  # noqa: BLE001
            nl_diag = {"error": str(exc)[:200]}
    out["neurolinked_connectivity"] = nl_diag

    out["recommendations"] = build_recommendations(
        swarm_live=swarm_live,
        swarm_file=swarm_file,
        cpnl=cpnl,
        dataset=dataset,
        ft_state=ft_state,
        neurolinked_connectivity=nl_diag if nl_diag else None,
    )
    return out


def format_swarm_weak_points_telegram(bundle: dict[str, Any], *, max_chars: int = 3900) -> str:
    """Markdown-ish text for Telegram (caller may use Markdown parse mode)."""
    lines: list[str] = []
    lines.append("*Swarm weak-points* (finance-agent + `swarm_knowledge`)")
    lines.append(f"_generated_ `{bundle.get('generated_utc')}`")

    sl = bundle.get("swarm_live") or {}
    if sl.get("ok"):
        lines.append(
            f"*Live swarm:* `{sl.get('swarm_label')}` mean=`{sl.get('swarm_mean')}` "
            f"conflict=`{sl.get('swarm_conflict')}`"
        )
        vv = sl.get("votes") or {}
        if isinstance(vv, dict):
            short = ", ".join(f"{k}={vv[k].get('vote')}" for k in sorted(vv.keys()))
            lines.append(f"_votes:_ {short}")
    else:
        lines.append(f"*Live swarm:* _failed_ — `{sl.get('detail')}`")

    sf = bundle.get("swarm_file") or {}
    if sf.get("ok") and sf.get("from_file"):
        lines.append(f"*File snapshot:* `{sf.get('swarm_label')}` @ `{sf.get('generated_utc')}`")

    cp = bundle.get("closed_pnl") or {}
    if cp.get("ok"):
        t = cp.get("last_50_legs") or {}
        lines.append(
            f"*Demo closed legs (tail):* n={t.get('n')} W/L={t.get('wins')}/{t.get('losses')} "
            f"sum≈`{t.get('sum')}` avg≈`{t.get('avg')}` venue=`{cp.get('venue')}`"
        )
    else:
        lines.append(f"*Closed PnL:* `{cp.get('detail')}`")

    ds = bundle.get("predict_loop_dataset") or {}
    if ds.get("ok"):
        lines.append(
            f"*Predict loop (last {ds.get('rows')} rows):* gate_ok_rate≈`{ds.get('gate_ok_rate')}` "
            f"targets `{ds.get('target_side_counts')}`"
        )
        lr = ds.get("last_row") or {}
        lines.append(
            f"_last:_ iter={lr.get('iter')} target={lr.get('target_side')} "
            f"gate_ok={lr.get('swarm_gate_ok')} — `{str(lr.get('swarm_reason'))[:120]}`"
        )
        lines.append("_Top blocks:_")
        for reason, n in (ds.get("top_block_reasons") or [])[:6]:
            lines.append(f"• `{n}` × `{str(reason)[:100]}`")
    else:
        lines.append(f"*Dataset tail:* `{ds.get('detail')}`")

    lines.append("*Recommendations:*")
    for i, r in enumerate(bundle.get("recommendations") or [], 1):
        sev = r.get("severity", "?")
        lines.append(f"{i}. [{sev}] *{r.get('title')}* — {str(r.get('detail') or '')[:420]}")
        hints = r.get("env_hints") or []
        if hints:
            lines.append(f"   _env:_ `{', '.join(hints[:6])}`")

    out = "\n".join(lines)
    if len(out) > max_chars:
        return out[: max_chars - 20] + "\n…(truncated)"
    return out
