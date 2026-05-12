"""sygnif_outcome_attribution.py — nightly outcome-attribution worker.

For every trade.open in the swarm with a corresponding closedPnl on Bybit,
attribute the outcome (R-multiple) back to the FEATURES that were active
at decision time (regime, IV regime, max_pain_align, rr_25d, gex_total,
imbalance, etc.). Write a structured report:

  • Total realized over window
  • Per-feature-bucket conditional expectancy
  • Top winning + top losing feature configurations
  • Recommended threshold tweaks for perp_runner

Output:
  - swarm topic agent.review.outcome_attribution (machine-readable meta)
  - ~/.sygnif/outcome-attribution-{YYYY-MM-DD}.json (full data dump)
  - stdout summary

Closes the learning loop (Tier 1.1 of the edge framework).

Env:
  SYGNIF_ATTRIBUTION_DAYS  default 7 (window to analyze)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import hmac
import hashlib
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/ubuntu/sygnif-agent-mirror")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DAYS         = int(os.environ.get("SYGNIF_ATTRIBUTION_DAYS", "7"))
SWARM_DB     = "/var/lib/sygnif/swarm.db"
LOG_PATH     = Path.home() / ".sygnif" / "outcome-attribution.log"
REPORT_DIR   = Path.home() / ".sygnif" / "attribution-reports"

# Bybit demo creds (loaded from env)
def _load_env():
    try:
        for ln in open("/etc/sygnif/trader.env"):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


REPORT_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler()])
log = logging.getLogger("attribution")


def signed_get(host: str, path: str, params: dict, key: str, sec: str) -> dict:
    qs = urllib.parse.urlencode(params)
    ts = str(int(time.time() * 1000))
    recv = "5000"
    sig = hmac.new(sec.encode(), (ts + key + recv + qs).encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{host}{path}?{qs}", headers={
        "X-BAPI-API-KEY": key, "X-BAPI-SIGN": sig,
        "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
    })
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def fetch_bybit_closed_pnl(days: int) -> list[dict]:
    """Pull all closed-pnl from Bybit demo for the window."""
    _load_env()
    key = os.environ.get("BYBIT_DEMO_API_KEY") or os.environ.get("BYBIT_API_KEY")
    sec = os.environ.get("BYBIT_DEMO_API_SECRET") or os.environ.get("BYBIT_API_SECRET")
    if not key or not sec:
        log.warning("no Bybit demo keys — skipping closed_pnl fetch")
        return []
    host = "https://api-demo.bybit.com"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    out: list[dict] = []
    for category in ("option", "linear"):
        cursor = ""
        for page in range(20):
            params = {"category": category, "limit": "100",
                      "startTime": start_ms, "endTime": end_ms}
            if cursor:
                params["cursor"] = cursor
            try:
                r = signed_get(host, "/v5/position/closed-pnl", params, key, sec)
            except Exception as e:
                log.warning("bybit fetch err (%s p%d): %s", category, page, e)
                break
            items = r.get("result", {}).get("list", []) or []
            for it in items:
                it["_category"] = category
                out.append(it)
            cursor = r.get("result", {}).get("nextPageCursor") or ""
            if not cursor or not items:
                break
    return out


def fetch_swarm_opens(days: int) -> list[dict]:
    """Pull trade.open events with their full meta context."""
    conn = sqlite3.connect(f"file:{SWARM_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT created, agent_id, content, meta, tags FROM swarm_entries "
        "WHERE topic='trade.open' AND created > strftime('%s','now',?) "
        "ORDER BY created ASC",
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    out = []
    for created, agent_id, content, meta_str, tags in rows:
        try:
            m = json.loads(meta_str) if meta_str else {}
            if isinstance(m, dict) and "_unstructured" in m:
                m = json.loads(m["_unstructured"])
        except Exception:
            m = {}
        out.append({
            "ts": float(created), "agent_id": agent_id,
            "content": content, "meta": m, "tags": tags or "",
        })
    return out


def link_opens_to_closes(opens: list[dict], closes: list[dict]) -> list[dict]:
    """Best-effort link each open to its closing realized-PnL by symbol+side+time.

    For options each leg can close independently. For perps, one position
    has one close. We aggregate per (symbol, opening_side) and group by
    nearest-time-after-open, summing closedPnl across leg closes.

    Returns one record per linked open: {open_meta + outcome + features}.
    """
    by_sym_side: dict[tuple, list[dict]] = defaultdict(list)
    for c in closes:
        sym = c.get("symbol")
        # close side is OPPOSITE of position side
        close_side = c.get("side")
        # original position side: closing Buy = was Sell, closing Sell = was Buy
        pos_side = "Sell" if close_side == "Buy" else "Buy"
        by_sym_side[(sym, pos_side)].append(c)

    linked = []
    for o in opens:
        meta = o.get("meta") or {}
        # Extract position symbol+side from various agent paths
        symbol = None; side = None
        # 1) sygnif-agent-trader (option iron_condor, multi-leg) — meta.plan
        plan = meta.get("plan") or {}
        if (plan.get("structure", "").startswith("short_iron_condor")
                or plan.get("structure", "").startswith("long_strangle")
                or plan.get("structure", "").startswith("iron_butterfly")
                or plan.get("structure", "").startswith("bull_call_spread")
                or plan.get("structure", "").startswith("bear_put_spread")):
            # 2026-05-10 V2 multi-leg attribution: sum closedPnl across all
            # leg-closes for THIS expiry within [open_ts, open_ts+7d).
            #
            # Heuristic: if the SAME expiry has another open later, restrict
            # the upper bound to that next open's ts (so each leg attributes
            # to exactly one structure).
            expiry = plan.get("expiry") or plan.get("strategy_expiry") or ""
            if not expiry:
                linked.append({"open": o, "outcome": None,
                                "skipped": "multi_leg_no_expiry"})
                continue
            # Tag in symbol format: "8MAY26", "11MAY26", etc.
            try:
                from datetime import datetime as _dt
                _e = _dt.strptime(expiry, "%Y-%m-%d")
                exp_tag = _e.strftime("%-d%b%y").upper()  # e.g. 11MAY26
            except Exception:
                exp_tag = expiry
            # Find next same-expiry open after this one (to bound the window)
            next_open_ts = o["ts"] + 7 * 86400  # default 7d window
            for other_o in opens:
                if other_o is o:
                    continue
                if other_o["ts"] <= o["ts"]:
                    continue
                other_plan = (other_o.get("meta") or {}).get("plan") or {}
                if other_plan.get("expiry") == expiry:
                    next_open_ts = min(next_open_ts, other_o["ts"])
                    break
            # Find all closes for this expiry within the window
            window_close_ms = (o["ts"] * 1000, next_open_ts * 1000)
            sum_pnl = 0.0
            n_legs = 0
            for c in closes:
                sym = c.get("symbol", "")
                if exp_tag not in sym:
                    continue
                ut = int(c.get("updatedTime") or 0)
                if window_close_ms[0] <= ut < window_close_ms[1]:
                    try:
                        sum_pnl += float(c.get("closedPnl") or 0)
                        n_legs += 1
                    except (ValueError, TypeError):
                        pass
            if n_legs == 0:
                linked.append({"open": o, "outcome": None,
                                "skipped": f"multi_leg_no_matching_closes(exp={exp_tag})"})
                continue
            linked.append({
                "open":     o,
                "outcome":  {"_aggregated_n_legs": n_legs,
                              "_window_open_to_next_open_s": next_open_ts - o["ts"],
                              "_expiry_tag": exp_tag},
                "pnl_usdc": round(sum_pnl, 4),
                "win":      sum_pnl > 0,
                "skipped":  None,
            })
            continue
        # 2) perp_runner — meta.signal
        sig = meta.get("signal") or {}
        if sig.get("symbol"):
            symbol = sig.get("symbol")
            side = sig.get("side")
        # 3) sygnif-agent-trader perp — meta.plan.symbol
        if not symbol and plan.get("symbol"):
            symbol = plan.get("symbol")
            side = plan.get("side")
        if not symbol or not side:
            linked.append({"open": o, "outcome": None, "skipped": "unparseable_open"})
            continue
        # find closes after this open
        candidates = sorted(
            [c for c in by_sym_side.get((symbol, side), [])
             if int(c.get("updatedTime", 0)) >= o["ts"] * 1000 - 60000],
            key=lambda c: int(c.get("updatedTime", 0))
        )
        if not candidates:
            linked.append({"open": o, "outcome": None, "skipped": "no_matching_close"})
            continue
        first = candidates[0]
        pnl = float(first.get("closedPnl") or 0)
        linked.append({"open": o, "outcome": first, "pnl_usdc": pnl,
                       "win": pnl > 0, "skipped": None})
    return linked


def extract_features(open_record: dict) -> dict:
    """Pull the features-at-decision-time from the open meta. Best-effort
    coverage across the known agent paths (sygnif-agent-trader + perp_runner)."""
    meta = open_record.get("meta") or {}
    feats = {"agent": open_record.get("agent_id")}
    plan = meta.get("plan") or {}
    sig = meta.get("signal") or {}
    opt = meta.get("option_enrichment") or {}
    micro = meta.get("microstructure") or {}
    conf = meta.get("confidence") or {}
    feats.update({
        "structure":      plan.get("structure") or sig.get("signal"),
        "regime":         (plan.get("context") or {}).get("regime") or sig.get("regime"),
        "atm_iv":         opt.get("atm_iv"),
        "rr_25d":         opt.get("rr_25d"),
        "max_pain_align": opt.get("max_pain_align"),
        "rr_25d_align":   opt.get("rr_25d_align"),
        "gex_trend_bias": opt.get("gex_trend_bias"),
        "imbalance":      micro.get("imbalance"),
        "spread_bps":     micro.get("spread_bps"),
        "scanner_conf":   sig.get("calibrated_setup_conf") or sig.get("raw_setup_conf"),
        "final_conf":     conf.get("final"),
    })
    return feats


def bucket_by(linked: list[dict], feature: str, bucket_fn) -> dict:
    """Group linked records by feature bucket. Compute per-bucket expectancy."""
    buckets: dict = defaultdict(lambda: {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for r in linked:
        if r.get("outcome") is None:
            continue
        feats = extract_features(r["open"])
        v = feats.get(feature)
        b = bucket_fn(v)
        if b is None:
            continue
        rec = buckets[b]
        rec["n"] += 1
        if r["pnl_usdc"] > 0: rec["wins"] += 1
        elif r["pnl_usdc"] < 0: rec["losses"] += 1
        rec["pnl"] += r["pnl_usdc"]
    out = {}
    for b, rec in buckets.items():
        decisive = rec["wins"] + rec["losses"]
        out[b] = {
            **rec,
            "win_rate": (rec["wins"] / decisive * 100) if decisive else 0.0,
            "avg_pnl": (rec["pnl"] / rec["n"]) if rec["n"] else 0.0,
        }
    return out


def main() -> int:
    log.info("outcome-attribution starting (window=%dd)", DAYS)
    closes = fetch_bybit_closed_pnl(DAYS)
    opens = fetch_swarm_opens(DAYS)
    log.info("fetched %d closes from Bybit, %d opens from swarm", len(closes), len(opens))

    linked = link_opens_to_closes(opens, closes)
    decisive = [r for r in linked if r.get("outcome") is not None]
    skipped = defaultdict(int)
    for r in linked:
        if r.get("skipped"):
            skipped[r["skipped"]] += 1

    total_pnl = sum(r["pnl_usdc"] for r in decisive)
    n_wins = sum(1 for r in decisive if r["pnl_usdc"] > 0)
    n_losses = sum(1 for r in decisive if r["pnl_usdc"] < 0)
    n_dec = n_wins + n_losses
    win_rate = (n_wins / n_dec * 100) if n_dec else 0.0
    expect = (total_pnl / len(decisive)) if decisive else 0.0

    print("\n=== ATTRIBUTION SUMMARY ===")
    print(f"window:        {DAYS}d (ending {datetime.now(timezone.utc).isoformat()})")
    print(f"opens:         {len(opens)}   (linked to closes: {len(decisive)}, "
          f"skipped: {dict(skipped)})")
    print(f"realized $:    ${total_pnl:+.2f}")
    print(f"win rate:      {win_rate:.0f}%   (W={n_wins} L={n_losses})")
    print(f"expectancy:    ${expect:+.3f} per linked-open")

    print("\n=== BY STRUCTURE ===")
    by_struct = bucket_by(decisive, "structure", lambda v: v or "unknown")
    for b, rec in sorted(by_struct.items(), key=lambda x: -x[1]["pnl"]):
        print(f"  {b:30s}  n={rec['n']:3d}  win={rec['win_rate']:.0f}%  "
              f"$total={rec['pnl']:+8.2f}  $avg={rec['avg_pnl']:+.3f}")

    print("\n=== BY REGIME (at decision) ===")
    by_regime = bucket_by(decisive, "regime", lambda v: v or "?")
    for b, rec in sorted(by_regime.items(), key=lambda x: -x[1]["pnl"]):
        print(f"  {b:18s}  n={rec['n']:3d}  win={rec['win_rate']:.0f}%  "
              f"$total={rec['pnl']:+8.2f}  $avg={rec['avg_pnl']:+.3f}")

    print("\n=== BY IV REGIME ===")
    def iv_bucket(v):
        if v is None: return None
        return "low" if v < 0.25 else ("high" if v > 0.45 else "normal")
    by_iv = bucket_by(decisive, "atm_iv", iv_bucket)
    for b, rec in sorted(by_iv.items(), key=lambda x: -x[1]["pnl"]):
        print(f"  {b:8s}  n={rec['n']:3d}  win={rec['win_rate']:.0f}%  "
              f"$total={rec['pnl']:+8.2f}  $avg={rec['avg_pnl']:+.3f}")

    print("\n=== BY RR_25D ALIGNMENT ===")
    def rr_bucket(v):
        if v is None: return None
        return "agree(>+0.1)" if v > 0.1 else ("disagree(<-0.1)" if v < -0.1 else "neutral")
    by_rr = bucket_by(decisive, "rr_25d_align", rr_bucket)
    for b, rec in sorted(by_rr.items(), key=lambda x: -x[1]["pnl"]):
        print(f"  {b:18s}  n={rec['n']:3d}  win={rec['win_rate']:.0f}%  "
              f"$total={rec['pnl']:+8.2f}  $avg={rec['avg_pnl']:+.3f}")

    # Save full data dump
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORT_DIR / f"attribution-{today}.json"
    dump = {
        "window_days": DAYS,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "n_opens": len(opens), "n_closes": len(closes),
        "n_linked": len(decisive),
        "skipped": dict(skipped),
        "total_pnl_usdc": total_pnl,
        "win_rate_pct": win_rate,
        "expectancy_per_linked_open": expect,
        "by_structure": by_struct, "by_regime": by_regime,
        "by_iv": by_iv, "by_rr_25d_align": by_rr,
    }
    report_path.write_text(json.dumps(dump, indent=2, default=str))
    print(f"\ndumped to {report_path}")

    # Write to swarm so the brain + dashboards see it
    try:
        import sygnif_neurons as N
        N.run("swarm.write", {
            "content": (f"OUTCOME ATTRIBUTION {DAYS}d: ${total_pnl:+.2f} "
                        f"win={win_rate:.0f}% n={len(decisive)} expect=${expect:+.3f}/trade"),
            "swarm_id": "trading",
            "agent_id": "outcome_attribution",
            "topic": "agent.review.outcome_attribution",
            "tags": ["attribution", "review", f"{DAYS}d"],
            "meta": dump,
        })
        print("emitted to swarm topic agent.review.outcome_attribution")
    except Exception as e:
        log.warning("swarm.write failed: %s", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
