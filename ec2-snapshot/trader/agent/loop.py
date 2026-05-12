"""SYGNIF autonomous trader — adaptive daemon.

Two ways to run:

    python -m agent.loop                     run a single cycle, exit (legacy)
    python -m agent.loop --daemon            run continuously, adaptive sleep

In daemon mode the loop:

  1. TICK every 30s — read portfolio, run review, fire emergency closes only.
     Cheap (~1-2s), keeps SYGNIF responsive to fast moves.

  2. PLAN cycle on adaptive cadence (60s..600s) — full review + plan + execute.
     Cadence chosen each iteration based on:
       * regime  (HIGH_VOL_SHOCK → 60s, TREND_* → 180s, NORMAL/RANGE → 300-600s)
       * open positions in distress (unreal < -$1 → 60s)
       * open positions near take-profit (unreal > $1 → 180s)
       * proximity to position cap (>=8 open → 120s)
       * UTC hour (12-22 = US session active → cap at 300s)

  3. INSTANT TRIGGER — if file ~/.sygnif/trader-kick is newer than last cycle,
     fire a full cycle immediately and delete the kick file. Use:
         touch ~/.sygnif/trader-kick

  4. SIGNAL — kill -USR1 <pid> wakes the loop; -USR2 forces a full cycle.

Heartbeats persist to swarm_id="trading" topic="trader.heartbeat" each cycle.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KICK_FILE = Path.home() / ".sygnif" / "trader-kick"

# global flags set by signal handlers
_FORCE_CYCLE = False
_WAKE = False


def _on_usr1(signum, frame):
    global _WAKE
    _WAKE = True


def _on_usr2(signum, frame):
    global _FORCE_CYCLE, _WAKE
    _FORCE_CYCLE = True
    _WAKE = True


# ---------------------------------------------------------------------------
# tick: cheap, every 30s — review + emergency closes only
# ---------------------------------------------------------------------------


def tick() -> dict:
    """Cheap tick: review open positions + auto-close any flagged.

    Skips verdicts whose `source` is bybit_live — bybit_daemon owns close
    execution for live-Bybit positions, and order.paper.close can't find
    a stable-pid (sha256 hash) in the paper journal anyway. Without this
    skip we got spam like:
        [daemon] tick closed 2 positions: ['33076715', 'c3b251a9']
    on every tick (those IDs are bybit_daemon stable_pids; the close
    was a no-op but the message kept emitting).
    """
    import sygnif_neurons as N

    out = {"ts": datetime.now(tz=timezone.utc).isoformat(), "closed": []}
    review = N.run("agent.trade.review", {})
    if not review.get("ok"):
        return out
    for v in review["data"]["verdicts"]:
        if v["verdict"] != "CLOSE":
            continue
        if v.get("source") == "bybit_live":
            continue   # bybit_daemon's close path handles this verdict
        close_out = N.run("order.paper.close", {"id": v["id"]})
        if not close_out.get("ok"):
            continue   # don't claim "closed" when paper.close failed
        pnl = (close_out.get("data") or {}).get("realized_pnl_usdc")
        out["closed"].append({"id": v["id"], "label": v["label"],
                              "why": v["why"], "realized_pnl_usdc": pnl})
    return out


# ---------------------------------------------------------------------------
# cycle: full review + plan + execute (the heavier path)
# ---------------------------------------------------------------------------


def cycle() -> dict:
    """One full trader cycle (calls into the existing run_one_cycle)."""
    return run_one_cycle()


def _shadow_llm_advice(N, plan_data: dict) -> dict | None:
    """Fire llm.advise on the deterministic plan; persist its reply to swarm.

    Opt-in via SYGNIF_LLM_SHADOW=1 — gated by run_one_cycle, not here. Returns
    a small dict that gets folded into the cycle summary, or None when the
    plan_data is empty (planner failed). Never raises; the trader stays
    on its rule-based path no matter what the LLM says.

    The point of this branch is to build a labeled dataset over the next
    couple of weeks: rule-based plan vs LLM opinion vs market outcome. After
    enough cycles, the audit trail tells us whether the LLM is worth wiring
    into the execution path (phase 2: veto gate; phase 3: primary planner).
    """
    if not plan_data:
        return None

    # Gather minimal context. Discovery is a cheap re-read; portfolio is
    # also cheap. We deliberately keep the snapshot small — every byte
    # costs prompt tokens, and the deterministic plan already includes
    # the structure/regime/iv context the LLM needs to reason about.
    disc_n = N.run("discovery.read", {})
    port_n = N.run("portfolio.demo", {})
    discovery = disc_n.get("data") if disc_n.get("ok") else None
    port_data = port_n.get("data") if port_n.get("ok") else {}
    snap = {
        "deterministic_plan": plan_data,
        "discovery": discovery,
        "portfolio_brief": {
            "equity_usdc": port_data.get("equity_usdc"),
            "open_count": port_data.get("open_count"),
            "drawdown_pct": port_data.get("drawdown_pct"),
        },
    }
    prompt = (
        "The deterministic SYGNIF planner just produced the plan in "
        "context.deterministic_plan. Do you concur, or would you do "
        "something different? Be specific about the structure choice and "
        "reference the regime / vol-state in your reasoning. If the plan "
        "is to skip, do you think that's correct, or is there a play the "
        "rules missed? Reply in 3-5 sentences."
    )
    advice = N.run("llm.advise", {"prompt": prompt, "snapshot": snap})
    if not advice.get("ok"):
        # Pod down or http error — record a minimal note for the summary,
        # don't persist to swarm (avoid spamming the audit log with failures).
        return {"ok": False,
                "reason": advice.get("reason"),
                "error": (advice.get("error") or "")[:120]}

    a = advice["data"]
    reply = (a.get("reply") or "").strip()
    label = plan_data.get("structure") or plan_data.get("rule") or "skip"
    # Persist to swarm.db so /swarm search and reflect-style postmortems can
    # join shadow advice with the deterministic plan and the eventual outcome.
    N.run("swarm.write", {
        "content": f"shadow advice [{plan_data.get('action')} / {label}]: {reply[:300]}",
        "swarm_id": "trading",
        "agent_id": "sygnif-llm-shadow",
        "topic": "trade.plan.shadow",
        "tags": ["shadow", "llm", str(label)],
        "meta": {
            "deterministic_plan": plan_data,
            "advice": reply,
            "elapsed_s": a.get("elapsed_s"),
            "tokens_in": a.get("tokens_in"),
            "tokens_out": a.get("tokens_out"),
            "tokens_per_sec": a.get("tokens_per_sec"),
        },
    })
    return {"ok": True,
            "advice_chars": len(reply),
            "elapsed_s": a.get("elapsed_s"),
            "tokens_out": a.get("tokens_out")}


def _compute_tier_candidates(N, plan_data: dict) -> dict:
    """Stage-1 observability for the 2026-05-04 tier rollout.

    Records the candidate signals for high_conf_short_hold (leverage tier)
    and long_term_conf (size tier) WITHOUT yet flipping the tier flags.
    Lets us backtest these conditions vs default-tier outcomes before any
    promotion. Each call adds ~200ms of cheap neuron reads. All exceptions
    are swallowed — never blocks the cycle.

    Fields:
      predict_strong       — most recent predict_loop signal == STRONG_*
      big_recent_move      — |BTC 4h % move| >= 3.0
      at_psych_barrier     — within 50bps of nearest 5k psych level
      regime_is_trend      — discovery regime in TREND_*
      predict_signal       — raw signal label (STRONG_BULLISH | BULLISH | …)
      recent_move_pct      — 4h % move (signed)
      psych_distance_bps   — bps to nearest 5k multiple
      ts_utc               — when these were sampled
    """
    import datetime as _dt
    out = {
        "predict_strong":     False,
        "big_recent_move":    False,
        "at_psych_barrier":   False,
        "regime_is_trend":    False,
        "predict_signal":     None,
        "recent_move_pct":    None,
        "psych_distance_bps": None,
        "ts_utc":             _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
    }
    # 1. predict_loop signal — read most recent rows authored by predict_loop
    try:
        r = N.run("swarm.recent", {"limit": 5, "agent_id": "predict_loop"})
        if r.get("ok"):
            for entry in (r.get("data") or {}).get("entries") or []:
                txt = str(entry.get("content") or "")
                for sig in ("STRONG_BULLISH", "STRONG_BEARISH",
                            "BULLISH", "BEARISH", "NEUTRAL"):
                    if sig in txt:
                        out["predict_signal"] = sig
                        out["predict_strong"] = sig.startswith("STRONG_")
                        break
                if out["predict_signal"]:
                    break
    except Exception:
        pass
    # 2. recent 4h move from BTC 1h klines
    try:
        t = N.run("btc.tape.live", {"interval": "60", "limit": 5, "symbol": "BTCUSDT"})
        if t.get("ok"):
            data = t.get("data") or {}
            bars = data.get("bars") or data.get("klines") or []
            if len(bars) >= 4:
                close_now = float(bars[-1].get("close") or bars[-1].get("c") or 0)
                anchor = bars[-4]
                anchor_open = float(anchor.get("open") or anchor.get("o") or 0)
                if anchor_open > 0:
                    pct = (close_now - anchor_open) / anchor_open * 100.0
                    out["recent_move_pct"] = round(pct, 2)
                    out["big_recent_move"] = abs(pct) >= 3.0
    except Exception:
        pass
    # 3. distance to nearest 5k psych barrier
    try:
        ctx = plan_data.get("context") if isinstance(plan_data, dict) else None
        ctx = ctx if isinstance(ctx, dict) else {}
        F = float(ctx.get("F") or plan_data.get("F") or 0)
        if F == 0:
            tk = N.run("btc.ticker", {"symbol": "BTCUSDT"})
            if tk.get("ok"):
                d = tk.get("data") or {}
                F = float(d.get("mid") or d.get("mark") or d.get("last") or 0)
        if F > 0:
            nearest_5k = round(F / 5000.0) * 5000.0
            dist_bps = abs(F - nearest_5k) / F * 10000.0
            out["psych_distance_bps"] = round(dist_bps, 1)
            out["at_psych_barrier"] = dist_bps <= 50.0
    except Exception:
        pass
    # 4. regime
    try:
        d = N.run("discovery.read", {})
        regime = ((d.get("data") or {}).get("regime") or "").upper()
        out["regime_is_trend"] = regime in (
            "TREND_UP", "TREND_DOWN", "STRONG_TREND", "TREND")
    except Exception:
        pass
    return out




# ---------------------------------------------------------------------------
# Phase 1 helpers (2026-05-10) — env resolution, multi-asset wallet, swarm
# event emission for tier promotions.
# ---------------------------------------------------------------------------

def _resolve_env() -> str:
    """Return 'demo' | 'live' | 'paper' from current trader env. Mirrors
    sygnif_neurons.n_agent_trade_execute resolution so heartbeats and
    swarm events agree with the executor on which environment was active."""
    mode = (os.environ.get("SYGNIF_ORDERS_MODE") or "paper").strip().lower()
    if mode not in ("paper", "demo", "live"):
        mode = "paper"
    return mode


def _read_full_wallet(N) -> dict:
    """Multi-asset wallet snapshot. Returns:

        {
          "env":              "demo" | "live" | "paper",
          "total_equity_usd": float,    # multi-asset, USD-equivalent
          "available_usd":    float,
          "wallet_balance_usd": float,
          "coins": [
              {"coin": "USDC", "balance": 1000.0, "usd_value": 999.98,
               "equity_usd": 1000.0},
              {"coin": "USDT", "balance":  928.97, "usd_value": 929.07,
               "equity_usd":  929.09},
              ...non-zero only
          ],
          "source": "wallet.demo" | "wallet.live" | "n/a",
        }

    Falls back to {"env": ..., "source": "n/a"} on error so callers can
    still emit a heartbeat. Does NOT replace portfolio.demo aggregation —
    it's an ADDITION captured in the heartbeat for the (decision, outcome)
    learning loop later."""
    env = _resolve_env()
    out = {"env": env, "source": "n/a", "coins": []}

    if env == "paper":
        return out
    neuron_name = "wallet.demo" if env == "demo" else "wallet.live"
    out["source"] = neuron_name
    try:
        r = N.run(neuron_name, {})
        if not r.get("ok"):
            return out
        d = r.get("data") or {}
        res = d.get("result") or {}
        lst = res.get("list") or []
        if not lst:
            return out
        acct = lst[0]
        try:
            out["total_equity_usd"] = float(acct.get("totalEquity") or 0)
        except (ValueError, TypeError):
            pass
        try:
            out["available_usd"] = float(
                acct.get("totalAvailableBalance") or 0)
        except (ValueError, TypeError):
            pass
        try:
            out["wallet_balance_usd"] = float(
                acct.get("totalWalletBalance") or 0)
        except (ValueError, TypeError):
            pass
        for c in (acct.get("coin") or []):
            try:
                bal = float(c.get("walletBalance") or 0)
                usd = float(c.get("usdValue") or 0)
                if bal == 0 and usd == 0:
                    continue
                eq = c.get("equity")
                eq_f = float(eq) if eq not in (None, "") else None
                out["coins"].append({
                    "coin":       c.get("coin"),
                    "balance":    bal,
                    "usd_value":  usd,
                    "equity_usd": eq_f,
                })
            except (ValueError, TypeError):
                continue
    except Exception:
        pass
    return out


def _emit_tier_promoted(N, plan_data: dict, env: str) -> None:
    """Emit `agent.tier_promoted` swarm row when planner promoted tier flags.

    Reads `plan_data.tier_promotion` (set by trader._promote_tier_flags)
    and writes a structured row. No-op when promotion didn't fire.
    Failures swallowed."""
    try:
        promo = plan_data.get("tier_promotion") if isinstance(
            plan_data, dict) else None
        if not promo or not promo.get("promotions"):
            return
        promotions = promo["promotions"]
        labels = list(promotions.keys())
        N.run("swarm.write", {
            "content": (
                f"PROMOTE [{env}] {','.join(labels)}: "
                + "; ".join(
                    f"{k}={v.get('value')} ({v.get('reason')})"
                    for k, v in promotions.items())),
            "swarm_id":  "trading",
            "agent_id":  "sygnif-trader-loop",
            "topic":     "agent.tier_promoted",
            "tags":      ["tier", "promotion", env] + labels,
            "meta": {
                "env":            env,
                "staged":         promo.get("staged"),
                "promotions":     promotions,
                "candidates":     promo.get("candidates"),
                "structure":      plan_data.get("structure"),
                "strategy":       plan_data.get("strategy"),
                "thesis":         plan_data.get("thesis"),
                "leverage_tier":  plan_data.get("leverage_tier"),
                "size_tier":      plan_data.get("size_tier"),
            },
        })
    except Exception:
        pass




def _extract_order_link_ids(exec_data: dict) -> list[str]:
    """Extract orderLinkIds from agent.trade.execute response data.

    Bybit V5 multi-leg places legs separately; each leg has its own
    orderLinkId. exec_data shape varies by execution path — handle:
      exec_data["exchange"]["legs"][i]["orderLinkId"]
      exec_data["exchange"]["orderLinkId"]   (single-leg perp)
      exec_data["paper"]["positions"][i]["order_link_id"]
    """
    ids: list[str] = []
    if not isinstance(exec_data, dict):
        return ids
    exch = exec_data.get("exchange") or {}
    if isinstance(exch, dict):
        if exch.get("orderLinkId"):
            ids.append(exch["orderLinkId"])
        for leg in (exch.get("legs") or []):
            if isinstance(leg, dict) and leg.get("orderLinkId"):
                ids.append(leg["orderLinkId"])
        # also nested under "result"/"list" pattern
        for grp in ("result", "data"):
            sub = exch.get(grp)
            if isinstance(sub, dict):
                lst = sub.get("list") or []
                for item in lst:
                    if isinstance(item, dict) and item.get("orderLinkId"):
                        ids.append(item["orderLinkId"])
    paper = exec_data.get("paper") or exec_data.get("execution") or {}
    if isinstance(paper, dict):
        for k in ("order_link_id", "orderLinkId"):
            if paper.get(k):
                ids.append(paper[k])
        for pos in (paper.get("positions") or []):
            if isinstance(pos, dict):
                for k in ("order_link_id", "orderLinkId"):
                    if pos.get(k):
                        ids.append(pos[k])
    # de-dup preserving order
    seen = set()
    out = []
    for i in ids:
        if i and i not in seen:
            seen.add(i); out.append(i)
    return out


def _emit_decision_executed(N, plan_data: dict, exec_out: dict,
                              env: str) -> None:
    """Emit decision.executed swarm row linking correlation_id to the
    order_link_ids that resulted from execution. Without this row, the
    joiner can't walk trade.close → order_link_id → correlation_id →
    decision.snapshot.

    Best-effort. No-op if correlation_id is missing or no exec data."""
    try:
        cid = plan_data.get("correlation_id") if isinstance(
            plan_data, dict) else None
        if not cid:
            return
        ed = exec_out.get("data") if isinstance(exec_out, dict) else None
        if not ed:
            return
        olids = _extract_order_link_ids(ed)
        executed = bool(ed.get("executed", False))
        N.run("swarm.write", {
            "content": (f"EXECUTED [{env}] correlation_id={cid[:8]} "
                        f"executed={executed} legs={len(olids)} "
                        f"olid={','.join(o[:14] for o in olids[:4])}"),
            "swarm_id": "trading",
            "agent_id": "sygnif-trader-loop",
            "topic":    "decision.executed",
            "tags":     ["decision", "executed", env],
            "meta": {
                "correlation_id":   cid,
                "env":              env,
                "executed":         executed,
                "mode":             ed.get("mode"),
                "order_link_ids":   olids,
                "structure":        plan_data.get("structure"),
                "strategy":         plan_data.get("strategy"),
                "instrument":       plan_data.get("instrument"),
                "leverage_tier":    plan_data.get("leverage_tier"),
                "size_tier":        plan_data.get("size_tier"),
                "exchange_error":   ((ed.get("exchange") or {}).get("error")
                                       if isinstance(ed.get("exchange"), dict) else None),
                "paper_blocked":    ((ed.get("paper") or {}).get("blocked_reason")
                                       if isinstance(ed.get("paper"), dict) else None),
            },
        })
    except Exception:
        pass


def run_one_cycle() -> dict:
    """One full iteration: review → close → plan → execute → heartbeat."""
    import sygnif_neurons as N

    started = datetime.now(tz=timezone.utc)
    actions: list[dict] = []

    # Phase 3.5 (2026-05-10): circuit-breaker check — must happen BEFORE
    # plan/execute. If tripped, in-process env vars are forced so promotion
    # is disabled. Cycle continues (planner sizes default-tier). Operator
    # must manually clear /var/lib/sygnif/circuit_breaker.json + restart.
    try:
        from agent import circuit_breaker as _CB
        _breaker_state = _CB.check_and_apply(N)
        if _breaker_state.get("state") == "tripped":
            actions.append({"kind":   "circuit_breaker",
                              "state":  "tripped",
                              "env":    _breaker_state.get("env"),
                              "reason": _breaker_state.get("reason"),
                              "since":  _breaker_state.get("since_utc")})
    except Exception as _e:
        import sys as _sys
        _sys.stderr.write(
            f"[circuit_breaker.check_and_apply] {type(_e).__name__}: {_e}\n")

    # 1. review + close (paper-side only; bybit_daemon owns live closes)
    review = N.run("agent.trade.review", {})
    if review.get("ok"):
        for v in review["data"]["verdicts"]:
            if v["verdict"] != "CLOSE":
                continue
            if v.get("source") == "bybit_live":
                # The daemon (sygnif-bybit-daemon.service) handles close
                # execution for live-Bybit positions through order.closes
                # with patient pricing. Calling order.paper.close here is
                # a no-op since the v["id"] is a stable-pid not in paper.
                continue
            close_out = N.run("order.paper.close", {"id": v["id"]})
            pnl = (close_out.get("data") or {}).get("realized_pnl_usdc")
            actions.append({"kind": "close", "id": v["id"], "label": v["label"],
                            "why": v["why"], "realized_pnl_usdc": pnl,
                            "ok": close_out.get("ok")})

    # 2. plan + execute
    plan = N.run("agent.trade.plan", {})
    plan_data = (plan.get("data") if isinstance(plan.get("data"), dict) else {}) if plan.get("ok") else {}

    # 2a. Tier-candidate observability (stage 1 of 2026-05-04 tier rollout).
    # Records signals that WOULD justify high_conf_short_hold / long_term_conf
    # tiers without yet flipping the gates. Backtest these vs outcomes before
    # promoting. ~200ms cheap-neuron cost; failures swallowed.
    tier_candidates = _compute_tier_candidates(N, plan_data) if plan_data else {}

    # 2a-bis (2026-05-10 Phase 1.3): if planner promoted tier flags, emit
    # an agent.tier_promoted swarm row so we can later attribute outcomes
    # to promoted vs default cohorts (see agent/tier_audit.py).
    _emit_tier_promoted(N, plan_data, _resolve_env())

    # 2b. shadow LLM advice (opt-in via SYGNIF_LLM_SHADOW=1).
    #     Asks the trained Qwen 3.5 9B + sygnif-lora-v3 whether it concurs
    #     with the deterministic plan. Persists to swarm.db; does NOT change
    #     execution. When the pod is down, llm.advise returns reason=pod_down
    #     in <50ms — adds negligible latency to a cycle that's typically
    #     500-2000ms anyway.
    shadow_summary = None
    if os.environ.get("SYGNIF_LLM_SHADOW") == "1":
        try:
            shadow_summary = _shadow_llm_advice(N, plan_data)
        except Exception as e:
            shadow_summary = {"ok": False, "reason": "exception",
                              "error": f"{type(e).__name__}: {e}"}

    if plan_data.get("action") == "propose":
        exec_out = N.run("agent.trade.execute", {})
        # Phase 2.1 (2026-05-10): emit decision.executed regardless of
        # success — failed executions also deserve attribution (snapshot
        # said propose, exec did/didn't happen, why).
        _emit_decision_executed(N, plan_data, exec_out, _resolve_env())
        if exec_out.get("ok"):
            ed = exec_out["data"]
            # Surface a useful error: prefer the paper journal blocked_reason/
            # error (gate / dedup / size cap), then fall back to exchange
            # error, then the legacy 'execution' key for any older callers.
            paper = ed.get("paper") or ed.get("execution") or {}
            exchange = ed.get("exchange") or {}
            err = (paper.get("error") or paper.get("blocked_reason")
                   or exchange.get("error") or exchange.get("blocked_reason"))
            actions.append({"kind": "open",
                            "structure": plan_data.get("structure"),
                            "strategy": plan_data.get("strategy"),
                            "expiry": plan_data.get("expiry"),
                            "thesis": plan_data.get("thesis"),
                            "mode": ed.get("mode"),
                            "executed": ed.get("executed", False),
                            "exchange_ok": bool(exchange.get("ok")) if exchange else None,
                            "execution_error": err,
                            "tier_candidates": tier_candidates})
        else:
            actions.append({"kind": "execute_failed",
                            "structure": plan_data.get("structure"),
                            "error": exec_out.get("error")})
    else:
        actions.append({"kind": "skip",
                        "reason": plan_data.get("reason"),
                        "rule": plan_data.get("rule")})

    # 3. heartbeat
    # Phase 1.5 (2026-05-10): use portfolio.demo for the trader-side aggregate
    # (USDC-collapsed equity, open/closed counts) AND _read_full_wallet for
    # multi-asset breakdown so the (decision, outcome) learning loop has the
    # full coin-level context. env=demo/live/paper distinguishes cohorts.
    env = _resolve_env()
    port_neuron = "portfolio.demo"  # portfolio.live not yet wired upstream
    port = N.run(port_neuron, {})
    snapshot = {"env": env}
    if port.get("ok"):
        p = port["data"]
        snapshot.update({
            "equity_usdc":         p["equity_usdc"],
            "open_count":          p["open_count"],
            "closed_count":        p["closed_count"],
            "total_realized_usdc": p["total_realized_usdc"],
            "total_unrealized_usdc": p["total_unrealized_usdc"],
            "drawdown_pct":        p["drawdown_pct"],
        })
    # Multi-asset (USDC + USDT + others) — the source of truth for sizing.
    snapshot["wallet"] = _read_full_wallet(N)

    duration_s = (datetime.now(tz=timezone.utc) - started).total_seconds()
    summary = {"started_utc": started.isoformat(), "duration_s": round(duration_s, 3),
               "actions_count": len(actions), "actions": actions,
               "portfolio": snapshot}
    if shadow_summary is not None:
        summary["llm_shadow"] = shadow_summary

    if port.get("ok"):
        N.run("swarm.write", {
            "content": (f"trader cycle @ {started.isoformat()}: "
                        f"equity=${snapshot['equity_usdc']:.2f} "
                        f"open={snapshot['open_count']} actions={len(actions)} "
                        f"({', '.join(a['kind'] for a in actions)})"),
            "swarm_id": "trading", "agent_id": "sygnif-trader-loop",
            "topic": "trader.heartbeat", "tags": ["loop", "autonomous"],
            "meta": summary,
        })
    return summary


# ---------------------------------------------------------------------------
# adaptive sleep
# ---------------------------------------------------------------------------


def next_sleep_seconds(port_data: dict, snap_data: dict) -> tuple[int, list[str]]:
    """Pick the next inter-cycle sleep based on market + portfolio state."""
    why: list[str] = []
    base = 900  # 15 min default — CPU relief

    # regime
    regime = (snap_data or {}).get("regime")
    if regime == "HIGH_VOL_SHOCK":
        base = min(base, 60); why.append("HIGH_VOL_SHOCK→60s")
    elif regime in ("TREND_UP", "TREND_DOWN"):
        base = min(base, 180); why.append(f"{regime}→180s")

    # position urgency
    for p in (port_data or {}).get("open", []) or []:
        unreal = float(p.get("unrealized_pnl_usdc", 0))
        if unreal < -1.0:
            base = min(base, 60); why.append(f"distress {p['id']} (${unreal:+.2f})→60s")
            break
        if unreal > 1.0:
            base = min(base, 180); why.append(f"winner {p['id']} (${unreal:+.2f})→180s")

    # near position cap
    open_count = int((port_data or {}).get("open_count", 0))
    if open_count >= 8:
        base = min(base, 120); why.append(f"near cap {open_count}/10→120s")

    # active session
    now_h = datetime.now(tz=timezone.utc).hour
    if 12 <= now_h < 22:
        base = min(base, 300); why.append("US session→cap 300s")
    elif 0 <= now_h < 8:
        base = max(base, 600); why.append("Asia/EU dead zone→600s")

    # floor + ceiling
    base = max(60, min(base, 1800))
    if not why:
        why.append(f"calm market→default {base}s")
    return base, why


# ---------------------------------------------------------------------------
# daemon
# ---------------------------------------------------------------------------


TICK_INTERVAL = 30  # seconds — emergency-exit poll cadence


def run_daemon() -> int:
    """Continuous adaptive daemon."""
    global _FORCE_CYCLE, _WAKE
    signal.signal(signal.SIGUSR1, _on_usr1)
    signal.signal(signal.SIGUSR2, _on_usr2)
    KICK_FILE.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    print(f"[daemon] pid={pid} started; touch {KICK_FILE} to force cycle; "
          f"kill -USR2 {pid} also forces.")
    sys.stdout.flush()

    import sygnif_neurons as N
    last_cycle_ts = 0.0
    next_full_at = 0.0

    while True:
        now = time.time()

        # check kick file
        kicked = False
        try:
            if KICK_FILE.exists() and KICK_FILE.stat().st_mtime > last_cycle_ts:
                kicked = True
                KICK_FILE.unlink(missing_ok=True)
                print(f"[daemon] kick file detected; forcing cycle")
        except Exception:
            pass

        force_cycle = _FORCE_CYCLE or kicked
        _FORCE_CYCLE = False

        # always tick
        tick_out = tick()
        if tick_out["closed"]:
            print(f"[daemon] tick closed {len(tick_out['closed'])} positions: "
                  f"{[c['id'] for c in tick_out['closed']]}")
            sys.stdout.flush()

        # full cycle when due
        if now >= next_full_at or force_cycle:
            print(f"[daemon] full cycle starting (force={force_cycle})")
            sys.stdout.flush()
            summary = run_one_cycle()
            last_cycle_ts = time.time()
            # decide next sleep based on resulting state
            sleep_s, why = next_sleep_seconds(summary.get("portfolio", {}),
                                              N.run("discovery.read", {}).get("data", {}))
            next_full_at = last_cycle_ts + sleep_s
            ek = ', '.join(a['kind'] for a in summary['actions'])
            print(f"[daemon] cycle done in {summary['duration_s']}s; actions: {ek}")
            print(f"[daemon] next full cycle in {sleep_s}s — reasons: {'; '.join(why)}")
            sys.stdout.flush()

        # sleep until next tick or wake signal
        slept = 0
        while slept < TICK_INTERVAL and not _WAKE:
            time.sleep(1)
            slept += 1
        _WAKE = False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--daemon", action="store_true",
                   help="run continuously with adaptive cadence")
    p.add_argument("--once", action="store_true",
                   help="run a single full cycle and exit (default if no flag)")
    args = p.parse_args()
    if args.daemon:
        return run_daemon()
    out = run_one_cycle()
    print(json.dumps(out, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
