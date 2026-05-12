#!/usr/bin/env python3
"""sygnif_onchain_report.py — Print leaderboard from the on-chain registry.

Usage:
  python3 sygnif_onchain_report.py           # full report
  python3 sygnif_onchain_report.py --top 20  # top 20 wallets only
  python3 sygnif_onchain_report.py --events  # recent whale events only
  python3 sygnif_onchain_report.py --json    # dump raw JSON
"""
import json, pathlib, sys, time
from collections import Counter

STATE = pathlib.Path("/var/lib/sygnif/onchain_state.json")

def main():
    if not STATE.exists():
        print(f"state file not found: {STATE}", file=sys.stderr)
        return 1
    s = json.loads(STATE.read_text())
    args = sys.argv[1:]
    if "--json" in args:
        print(json.dumps(s, indent=2, default=str)); return 0

    top_n = 25
    for i, a in enumerate(args):
        if a == "--top" and i+1 < len(args):
            try: top_n = int(args[i+1])
            except: pass

    show_events_only = "--events" in args
    show_wallets_only = "--wallets" in args

    print(f"=== SYGNIF on-chain oversight @ {s.get('updated_at_utc','?')} ===")
    m = s.get("metrics", {})
    print(f"  scans={m.get('blocks_scanned',0)}  whale_txs={m.get('whale_txs_seen',0)}  "
          f"swarm_emits={m.get('swarm_emits',0)}  lookups={m.get('lookups',0)} "
          f"(fails={m.get('lookup_failures',0)})")
    print(f"  last_block_height: {s.get('last_block_height')}")
    print(f"  wallets tracked:   {len(s.get('wallets', {}))}")
    print(f"  recent events:     {len(s.get('recent_events', []))}")

    if not show_events_only:
        print(f"\n— WALLET LEADERBOARD (top {top_n} by watchlist score × balance) —")
        wallets = s.get("wallets", {})
        ranked = sorted(wallets.values(),
                          key=lambda w: (-w.get("watchlist_score", 0),
                                          -w.get("balance_btc", 0)))
        print(f"  {'tier':<18s} {'balance':>12s} {'n_tx':>8s}  score  addr  label")
        for w in ranked[:top_n]:
            print(f"  {w.get('tier','?'):<18s} {w.get('balance_btc',0):>10,.1f} BTC "
                  f"{w.get('n_tx',0):>8,d}  {w.get('watchlist_score',0):>3d}    "
                  f"{w.get('addr','?')[:18]}...  {w.get('label','')[:50]}")

        # tier counts
        ctr = Counter(w.get("tier") for w in wallets.values())
        print(f"\n  tier distribution:")
        for t, n in ctr.most_common():
            print(f"    {t}: {n}")

    if not show_wallets_only:
        print(f"\n— RECENT WHALE EVENTS (last 15) —")
        events = (s.get("recent_events") or [])[-15:][::-1]
        for e in events:
            ts = e.get("ts_utc","?")[:19]
            cat = e.get("category","?")
            v = e.get("value_btc",0); usd = e.get("value_usd",0)
            print(f"  {ts}  {cat:<22s} {v:>8,.1f} BTC  ${usd/1e6:>6,.1f}M  blk={e.get('block')}  score={e.get('score')}")
            for x in (e.get("from") or [])[:2]:
                print(f"    FROM  [{x.get('tier','?'):<14s}] {x.get('v',0):>10,.2f}  {x.get('addr','?')[:18]}...  {x.get('label','')[:40]}")
            for x in (e.get("to") or [])[:2]:
                print(f"    TO    [{x.get('tier','?'):<14s}] {x.get('v',0):>10,.2f}  {x.get('addr','?')[:18]}...  {x.get('label','')[:40]}")

        # Category counts
        ctr = Counter(e.get("category") for e in (s.get("recent_events") or []))
        print(f"\n  category counts (lifetime):")
        for c, n in ctr.most_common():
            print(f"    {c}: {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
