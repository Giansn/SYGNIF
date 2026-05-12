"""agent/decision_snapshot.py — Phase 2.1 of the autonomous-trader plan.

Writes one full feature-snapshot per planner decision to swarm topic
`decision.snapshot`. Joined later (by sygnif_decision_joiner.py) with
trade.open / trade.close / outcome.attributed via correlation_id.

The snapshot is the X side of the (X, y) training pair the model needs.

Multi-asset & demo/live aware:
  - env tag distinguishes demo / live / paper trades
  - wallet captures all coins (USDC + USDT + ...) via Bybit V5 wallet
  - portfolio aggregate (USDC-collapsed) captured separately for backwards-compat
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from typing import Any


def _now_utc_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def _resolve_env() -> str:
    mode = (os.environ.get("SYGNIF_ORDERS_MODE") or "paper").strip().lower()
    return mode if mode in ("paper", "demo", "live") else "paper"


def _safe_run(N, neuron: str, args: dict | None = None) -> dict:
    try:
        r = N.run(neuron, args or {})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return r if isinstance(r, dict) else {"ok": False, "error": "non-dict"}


def _read_full_wallet(N, env: str) -> dict:
    """Multi-asset wallet snapshot. Mirrors agent/loop.py:_read_full_wallet."""
    out = {"env": env, "source": "n/a", "coins": []}
    if env == "paper":
        return out
    nname = "wallet.demo" if env == "demo" else "wallet.live"
    out["source"] = nname
    r = _safe_run(N, nname, {})
    if not r.get("ok"):
        return out
    d = r.get("data") or {}
    res = d.get("result") or {}
    lst = res.get("list") or []
    if not lst:
        return out
    acct = lst[0]
    for k_in, k_out in (("totalEquity", "total_equity_usd"),
                          ("totalAvailableBalance", "available_usd"),
                          ("totalWalletBalance", "wallet_balance_usd")):
        try:
            v = acct.get(k_in)
            if v not in (None, ""):
                out[k_out] = float(v)
        except (ValueError, TypeError):
            pass
    for c in (acct.get("coin") or []):
        try:
            bal = float(c.get("walletBalance") or 0)
            usd = float(c.get("usdValue") or 0)
            if bal == 0 and usd == 0:
                continue
            eq_raw = c.get("equity")
            eq = float(eq_raw) if eq_raw not in (None, "") else None
            out["coins"].append({
                "coin":       c.get("coin"),
                "balance":    bal,
                "usd_value":  usd,
                "equity_usd": eq,
            })
        except (ValueError, TypeError):
            continue
    return out


def _read_portfolio(N) -> dict:
    """USDC-collapsed view from portfolio.demo (the planner's own input)."""
    r = _safe_run(N, "portfolio.demo", {})
    if not r.get("ok"):
        return {}
    p = r.get("data") or {}
    return {
        "equity_usdc":          p.get("equity_usdc"),
        "open_count":           p.get("open_count"),
        "closed_count":         p.get("closed_count"),
        "total_realized_usdc":  p.get("total_realized_usdc"),
        "total_unrealized_usdc": p.get("total_unrealized_usdc"),
        "drawdown_pct":         p.get("drawdown_pct"),
    }


def _read_latest_forecast(N) -> dict | None:
    """Most-recent sygnif-predict forecast — JSON dict with regime + signal."""
    r = _safe_run(N, "swarm.recent", {"limit": 1,
                                         "agent_id": "sygnif-predict",
                                         "topic":    "forecast"})
    if not r.get("ok"):
        return None
    entries = (r.get("data") or {}).get("entries") or []
    if not entries:
        return None
    try:
        return json.loads(entries[0].get("content") or "{}")
    except (json.JSONDecodeError, TypeError):
        return None


def _read_latest_discovery(N) -> dict | None:
    """Most-recent discovery snapshot (regime/IV/GEX/max-pain)."""
    r = _safe_run(N, "discovery.read", {})
    if r.get("ok"):
        return r.get("data")
    # fallback — pull from swarm
    r = _safe_run(N, "swarm.recent", {"limit": 1,
                                         "topic": "regime"})
    if not r.get("ok"):
        return None
    entries = (r.get("data") or {}).get("entries") or []
    if not entries:
        return None
    try:
        return json.loads(entries[0].get("content") or "{}")
    except (json.JSONDecodeError, TypeError):
        return None


def _read_recent_outcomes(N, n: int = 10) -> dict:
    """Win-rate + avg P&L from last N trade.close events. Best-effort."""
    r = _safe_run(N, "swarm.recent", {"limit": n, "topic": "trade.close"})
    if not r.get("ok"):
        return {"n": 0}
    entries = (r.get("data") or {}).get("entries") or []
    pnls = []
    for e in entries:
        meta_raw = e.get("meta") or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except (json.JSONDecodeError, TypeError):
            continue
        pnl = meta.get("closed_pnl")
        if pnl is None:
            continue
        try:
            pnls.append(float(pnl))
        except (ValueError, TypeError):
            continue
    if not pnls:
        return {"n": len(entries), "n_with_pnl": 0}
    wins = sum(1 for p in pnls if p > 0)
    return {
        "n":         len(entries),
        "n_with_pnl": len(pnls),
        "win_rate":  round(wins / len(pnls), 3),
        "avg_pnl":   round(sum(pnls) / len(pnls), 4),
        "total_pnl": round(sum(pnls), 4),
    }


def build_snapshot(plan: dict, *, correlation_id: str | None = None) -> dict:
    """Build the full decision snapshot dict. Returns the snapshot — does NOT
    write. Caller should pass this to write_snapshot()."""
    try:
        import sygnif_neurons as N
    except Exception:
        N = None

    cid = correlation_id or str(uuid.uuid4())
    env = _resolve_env()

    # Plan-side: what the planner decided
    plan_decision = plan.get("action")
    plan_view = {
        "action":          plan_decision,
        "structure":       plan.get("structure"),
        "strategy":        plan.get("strategy"),
        "instrument":      plan.get("instrument"),
        "expiry":          plan.get("expiry"),
        "qty":             plan.get("qty"),
        "leverage":        plan.get("leverage"),
        "leverage_tier":   plan.get("leverage_tier"),
        "size_tier":       plan.get("size_tier"),
        "risk_pct":        plan.get("risk_pct"),
        "stop_pct":        plan.get("stop_pct"),
        "thesis":          plan.get("thesis"),
        "reason":          plan.get("reason"),
        "rule":            plan.get("rule"),
        "symbol":          plan.get("symbol"),
        "F":               plan.get("F"),
        "K_put_long":      plan.get("K_put_long"),
        "K_put_short":     plan.get("K_put_short"),
        "K_call_short":    plan.get("K_call_short"),
        "K_call_long":     plan.get("K_call_long"),
        "K_put":           plan.get("K_put"),
        "K_call":          plan.get("K_call"),
        "K_buy":           plan.get("K_buy"),
        "K_sell":          plan.get("K_sell"),
        "max_loss_usd":    plan.get("max_loss_usd"),
    }
    plan_view = {k: v for k, v in plan_view.items() if v is not None}

    # Tier promotion outcome (set by trader._promote_tier_flags)
    tier_promo = plan.get("tier_promotion") or {}

    # Live context
    if N is not None:
        portfolio = _read_portfolio(N)
        wallet    = _read_full_wallet(N, env)
        forecast  = _read_latest_forecast(N)
        discovery = _read_latest_discovery(N)
        recent    = _read_recent_outcomes(N, 10)
    else:
        portfolio = wallet = forecast = discovery = recent = {}

    # Path C (2026-05-10): whale-flow features. Reads /var/lib/sygnif/whale_flow.json
    # written by sygnif-whale-watcher.service. Always returns a structured
    # dict; missing/stale daemon → ok=False but other fields zeroed cleanly.
    try:
        from agent import whale_flow as _WF
        whales = _WF.get_whale_flow()
        whale_alignment = _WF.alignment(plan_view.get("symbol", "").lower()
                                          and ("long" if plan_view.get("structure", "").endswith("_long")
                                               else "short" if plan_view.get("structure", "").endswith("_short")
                                               else None),
                                          whales)
    except Exception:
        whales = {"ok": False, "ws_status": "import_failed"}
        whale_alignment = {"alignment": "neutral"}

    # 2026-05-10: market microstructure features (orderbook + perp ticker +
    # funding history + options chain). REST-polled with 30s in-process cache,
    # so multiple snapshots within a minute share the same data. Each source
    # fails independently (composite dict always has same shape).
    try:
        from agent import market_features as _MF
        sym = plan_view.get("symbol") or "BTCUSDT"
        market_ctx = _MF.get_market_context(sym)
    except Exception as e:
        market_ctx = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # 2026-05-10: bounce protocol — short-window mean-reversion detector.
    # Reads /var/lib/sygnif/bounce_setup.json kept fresh by sygnif-bounce-
    # watcher.service (kline.1 WS, sub-minute updates). Falls back to REST
    # poll if daemon stale.
    try:
        from agent import bounce_protocol as _BP
        bounce = _BP.get_bounce_setup_live()
    except Exception as e:
        bounce = {"ok": False, "active": False,
                   "error": f"{type(e).__name__}: {e}"}


    # 2026-05-10: macro / geopolitical news features. Pulled from swarm topic
    # ``news.event`` (written by sygnif-news-feed.service) with log-tail
    # fallback for events posted before swarm-write was wired.
    try:
        from agent import news_features as _NF
        news = _NF.get_news_features(lookback_minutes=60)
    except Exception as e:
        news = {"ok": False, "reason": f"{type(e).__name__}: {e}",
                "n_articles": 0, "n_recent": 0,
                "categories": {}, "severity_counts": {},
                "fresh_event_flag": False}

    snapshot = {
        "correlation_id":   cid,
        "ts_utc":           _now_utc_iso(),
        "env":              env,
        "plan":             plan_view,
        "tier_promotion":   tier_promo,
        "context":          plan.get("context") or {},
        "portfolio":        portfolio,
        "wallet":           wallet,
        "forecast":         forecast,
        "discovery_keys":   sorted((discovery or {}).keys()) if isinstance(discovery, dict) else [],
        "recent_outcomes":  recent,
        "whale_flow":       whales,
        "whale_alignment":  whale_alignment,
        "market":           market_ctx,
        "bounce":           bounce,
        "news":             news,
    }
    # Discovery can be huge — keep just the trading-relevant top level
    if isinstance(discovery, dict):
        snapshot["discovery"] = {
            k: discovery.get(k)
            for k in ("regime", "iv_pct", "atm_iv_nearest",
                      "atr_pct", "gex", "max_pain", "funding_bps",
                      "options", "perp", "ts_utc", "label")
            if k in discovery
        }
    return snapshot


def write_snapshot(plan: dict, *, correlation_id: str | None = None) -> str:
    """Build snapshot + emit decision.snapshot swarm row. Returns
    correlation_id (caller should attach to plan["correlation_id"])."""
    snap = build_snapshot(plan, correlation_id=correlation_id)
    cid = snap["correlation_id"]
    try:
        import sygnif_neurons as N
        action = snap["plan"].get("action") or "?"
        structure = snap["plan"].get("structure") or "n/a"
        env = snap.get("env")
        head = (f"DECISION [{env}] {action} {structure} "
                f"correlation_id={cid[:8]}")
        N.run("swarm.write", {
            "content":   head,
            "swarm_id":  "trading",
            "agent_id":  "sygnif-trader-loop",
            "topic":     "decision.snapshot",
            "tags":      ["snapshot", env, action,
                          structure if structure != "n/a" else "skip"],
            "meta":      snap,
        })
    except Exception as e:
        # Snapshots are best-effort — never block planning on swarm-write fail
        import sys
        sys.stderr.write(
            f"[decision_snapshot.write_snapshot] {type(e).__name__}: {e}\n")
    return cid
