#!/usr/bin/env python3
"""sygnif_onchain_watcher.py — Long-running BTC on-chain oversight daemon.

Continuously monitors the Bitcoin blockchain for whale movements that our
exchange-level whale watcher misses. Emits structured events to swarm and
maintains a persistent registry of "wallets of interest."

Architecture:
  Block poll loop  (60s)  →  detect new tip
       ↓
  Fetch full block       →  blockchain.info /rawblock (single call per block)
       ↓
  Find whale txs         →  output >= THRESHOLD_BTC
       ↓
  Classify addresses     →  mempool.space /api/address/{addr}  (rate-limited)
       ↓
  Update registry        →  /var/lib/sygnif/onchain_state.json
       ↓
  Emit swarm events      →  topic = "onchain.whale"
       ↓
  Track:
    - cold-storage accumulators (received >> sent, low tx count, growing balance)
    - distributors            (sending large amounts, balance falling)
    - exchange operators      (high tx count, large turnover)

Sources:
  - mempool.space  block-tip + address detail
  - blockchain.info  full block (rawblock endpoint)

No paid APIs. Respects free-tier rate limits.

Run: python3 sygnif_onchain_watcher.py
Systemd unit installed at /etc/systemd/system/sygnif-onchain-watcher.service
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import signal
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import uuid

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
THRESHOLD_BTC      = float(os.environ.get("SYGNIF_ONCHAIN_THRESHOLD_BTC", "50"))
COLD_TX_LIMIT      = int(os.environ.get("SYGNIF_ONCHAIN_COLD_TX_MAX", "50"))   # n_tx ≤ this and balance growing = cold-accumulator
HOT_TX_FLOOR       = int(os.environ.get("SYGNIF_ONCHAIN_HOT_TX_MIN", "1000"))  # n_tx ≥ this = exchange-like
POLL_INTERVAL_S    = int(os.environ.get("SYGNIF_ONCHAIN_POLL_S", "60"))
ADDR_LOOKUP_DELAY  = float(os.environ.get("SYGNIF_ONCHAIN_ADDR_DELAY_S", "1.5"))
DB_PATH            = "/var/lib/sygnif/swarm.db"
STATE_FILE         = pathlib.Path("/var/lib/sygnif/onchain_state.json")
EVENT_LIMIT        = 200   # keep last N events in state
WALLET_LIMIT       = 500   # keep top N wallets

MEMPOOL_BASE = "https://mempool.space/api"
BCI_BASE     = "https://blockchain.info"

USER_AGENT = "sygnif-onchain-watcher/1.0"
HEADERS    = {"User-Agent": USER_AGENT}

# Hardcoded exchange labels — best-effort; addresses rotate.
KNOWN = {
    "3Mvtgmu8s8FjpdABqdmKaTYhDjnRu7eERN": ("EXCHANGE", "Coinbase"),
    "1FzWLkAahHooV3kzTgyx6qsswXJ6sCXkSR": ("EXCHANGE", "Coinbase"),
    "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo": ("EXCHANGE", "Binance Cold"),
    "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s": ("EXCHANGE", "Binance"),
    "3JZq4atUahhuA9rLhXLMhhTo133J9rF97j": ("EXCHANGE", "Binance"),
    "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h": ("EXCHANGE", "Binance Hot"),
    "1Kr6QSydW9bFQG1mXiPNNu6WpJGmUa9i1g": ("EXCHANGE", "Bitfinex Hot"),
    "bc1qrh99vw0ujsy9plhpf95dcyzj0jvc5lfvxsq3qm": ("EXCHANGE", "Bybit"),
    "bc1qrsuxwwzwzy9rt0xytsen8w8t4puzeuwq7p83ar": ("EXCHANGE", "OKX"),
    "37XuVSEpWW4trkfmvWzegTHQt7BdktSKUs": ("EXCHANGE", "Kraken"),
    "3FupZp77ySr7jwoLYEJ9mwzJpvoNBXsBnE": ("EXCHANGE", "Kraken"),
}

_running = True


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _jget(url: str, timeout: int = 15) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception as e:
        print(f"  ! GET {url[:80]} — {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return None


def fetch_tip_height() -> int | None:
    r = _jget(f"{MEMPOOL_BASE}/blocks/tip/height")
    if isinstance(r, int):
        return r
    return None


def fetch_tip_hash() -> str | None:
    """tip/hash returns a bare hash string, not JSON — fetch as raw text."""
    try:
        req = urllib.request.Request(f"{MEMPOOL_BASE}/blocks/tip/hash", headers=HEADERS)
        h = urllib.request.urlopen(req, timeout=10).read().decode("utf-8").strip()
        return h if len(h) == 64 else None
    except Exception as e:
        print(f"  ! tip/hash fetch failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return None


def fetch_block_full(block_hash: str) -> dict | None:
    """Pull the entire block including all txs in one call."""
    return _jget(f"{BCI_BASE}/rawblock/{block_hash}")


def fetch_address(addr: str) -> dict | None:
    """Pull address stats via mempool.space (different rate window than blockchain.info)."""
    return _jget(f"{MEMPOOL_BASE}/address/{addr}")


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if not STATE_FILE.exists():
        return _new_state()
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return _new_state()


def _new_state() -> dict:
    return {
        "schema":          "sygnif.onchain_oversight.v1",
        "created_at_utc":  dt.datetime.now(dt.timezone.utc).isoformat(),
        "last_block_height": 0,
        "wallets":         {},          # addr -> dict
        "recent_events":   [],          # list of whale events
        "metrics":         {
            "blocks_scanned":   0,
            "whale_txs_seen":   0,
            "lookups":          0,
            "lookup_failures":  0,
            "swarm_emits":      0,
        },
    }


def save_state(state: dict) -> None:
    state["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    # Trim
    state["recent_events"] = state["recent_events"][-EVENT_LIMIT:]
    if len(state["wallets"]) > WALLET_LIMIT:
        # Keep the wallets with highest balance + recent activity
        sorted_w = sorted(state["wallets"].items(),
                          key=lambda kv: (kv[1].get("watchlist_score", 0),
                                          kv[1].get("balance_btc", 0)),
                          reverse=True)
        state["wallets"] = dict(sorted_w[:WALLET_LIMIT])

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, default=str, indent=2))
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# Address classification
# ---------------------------------------------------------------------------
def classify(addr: str, info: dict | None) -> tuple[str, str, int]:
    """Returns (tier, label, watchlist_score)."""
    if addr in KNOWN:
        tier, label = KNOWN[addr]
        return (tier, label, 5)
    if info is None:
        return ("?", "lookup-failed", 0)
    cs = info.get("chain_stats", {}) or {}
    ms = info.get("mempool_stats", {}) or {}
    funded = (cs.get("funded_txo_sum", 0) + ms.get("funded_txo_sum", 0)) / 1e8
    spent  = (cs.get("spent_txo_sum",  0) + ms.get("spent_txo_sum",  0)) / 1e8
    bal    = funded - spent
    n_tx   = cs.get("tx_count", 0) + ms.get("tx_count", 0)

    # Classifiers (in priority order)
    if n_tx >= HOT_TX_FLOOR:
        return ("EXCHANGE-LIKE", f"hot ({n_tx:,} txs, {bal:,.0f} BTC bal)", 2)
    if n_tx <= COLD_TX_LIMIT and spent < 0.01 * funded and bal >= 100:
        return ("COLD_ACCUMULATOR", f"cold ({n_tx} txs, {bal:,.1f} BTC, 0% spent)", 9)
    if n_tx <= COLD_TX_LIMIT and spent < 0.01 * funded and bal >= 10:
        return ("FRESH_COLD", f"new cold ({n_tx} txs, {bal:,.1f} BTC, 0% spent)", 7)
    if bal >= 1000:
        return ("WHALE_COLD", f"large dormant ({n_tx} txs, {bal:,.0f} BTC)", 6)
    if bal >= 100:
        return ("WHALE", f"holder ({n_tx} txs, {bal:,.1f} BTC)", 4)
    if n_tx >= 100:
        return ("ACTIVE", f"operator ({n_tx} txs, {bal:,.1f} BTC)", 3)
    if n_tx == 1:
        return ("FRESH", f"first tx ({bal:,.2f} BTC)", 5)
    return ("WALLET", f"normal ({n_tx} txs, {bal:,.3f} BTC)", 1)


def address_summary(info: dict | None) -> dict:
    """Extract a compact summary from mempool.space address info."""
    if not info:
        return {"balance_btc": 0, "n_tx": 0, "total_received_btc": 0, "total_sent_btc": 0}
    cs = info.get("chain_stats", {}) or {}
    ms = info.get("mempool_stats", {}) or {}
    funded = (cs.get("funded_txo_sum", 0) + ms.get("funded_txo_sum", 0)) / 1e8
    spent  = (cs.get("spent_txo_sum",  0) + ms.get("spent_txo_sum",  0)) / 1e8
    n_tx   = cs.get("tx_count", 0) + ms.get("tx_count", 0)
    return {
        "balance_btc":        round(funded - spent, 4),
        "n_tx":               n_tx,
        "total_received_btc": round(funded, 4),
        "total_sent_btc":     round(spent, 4),
    }


# ---------------------------------------------------------------------------
# Swarm emission
# ---------------------------------------------------------------------------
def emit_swarm_event(topic: str, content: str, meta: dict, tags: list) -> None:
    if not os.path.exists(DB_PATH):
        return
    try:
        c = sqlite3.connect(DB_PATH, timeout=10)
        rid = str(uuid.uuid4())
        c.execute(
            "INSERT OR IGNORE INTO swarm_entries "
            "(id, created, swarm_id, agent_id, topic, content, meta, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, int(time.time()), "trading",
             "sygnif-onchain-watcher", topic, content,
             json.dumps(meta, default=str), json.dumps(tags)))
        c.commit()
        c.close()
    except Exception as e:
        print(f"  ! swarm emit failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Main scan logic
# ---------------------------------------------------------------------------
def scan_block(state: dict, block: dict) -> int:
    """Walk one block, return number of whale events emitted."""
    txs = block.get("tx", []) or []
    height = block.get("height")
    block_ts = block.get("time")
    n_whales = 0
    looked_up = 0

    # First pass: find whale txs and collect unique addresses
    whale_txs = []
    for t in txs:
        outs = t.get("out") or []
        total_out = sum(o.get("value", 0) for o in outs) / 1e8
        if total_out >= THRESHOLD_BTC:
            whale_txs.append((t, total_out))

    if not whale_txs:
        return 0

    # Collect addresses (cap at first 5 inputs and 5 outputs per tx to limit lookups)
    addrs_to_lookup: set[str] = set()
    for t, _ in whale_txs:
        for inp in (t.get("inputs") or [])[:5]:
            a = (inp.get("prev_out") or {}).get("addr")
            if a and a not in state["wallets"]:
                addrs_to_lookup.add(a)
        for o in (t.get("out") or [])[:5]:
            a = o.get("addr")
            if a and a not in state["wallets"]:
                addrs_to_lookup.add(a)

    print(f"  block {height}: {len(whale_txs)} whale txs, {len(addrs_to_lookup)} new addrs to look up",
          flush=True)

    # Lookup new addresses (rate-limited)
    for addr in addrs_to_lookup:
        if not _running:
            break
        info = fetch_address(addr)
        state["metrics"]["lookups"] += 1
        looked_up += 1
        if info is None:
            state["metrics"]["lookup_failures"] += 1
        tier, label, score = classify(addr, info)
        summary = address_summary(info)
        wallet = {
            "addr":            addr,
            "tier":            tier,
            "label":           label,
            "watchlist_score": score,
            "first_seen_at":   dt.datetime.now(dt.timezone.utc).isoformat(),
            "last_seen_at":    dt.datetime.now(dt.timezone.utc).isoformat(),
            "last_block":      height,
            **summary,
        }
        state["wallets"][addr] = wallet
        time.sleep(ADDR_LOOKUP_DELAY)

    # Second pass: emit events for each whale tx
    for t, total_out in whale_txs:
        h = t.get("hash")
        in_addrs = []
        out_addrs = []
        for inp in (t.get("inputs") or [])[:5]:
            a = (inp.get("prev_out") or {}).get("addr")
            v = (inp.get("prev_out") or {}).get("value", 0) / 1e8
            if a:
                in_addrs.append({"addr": a, "v": v, "w": state["wallets"].get(a)})
        for o in (t.get("out") or [])[:5]:
            a = o.get("addr")
            v = o.get("value", 0) / 1e8
            if a:
                out_addrs.append({"addr": a, "v": v, "w": state["wallets"].get(a)})

        # Categorize flow
        def has_tier(addrs, tier_set):
            return any((x.get("w") or {}).get("tier") in tier_set for x in addrs)

        EXCH = {"EXCHANGE", "EXCHANGE-LIKE"}
        COLD = {"COLD_ACCUMULATOR", "FRESH_COLD", "WHALE_COLD"}

        from_exch = has_tier(in_addrs, EXCH)
        to_exch   = has_tier(out_addrs, EXCH)
        to_cold   = has_tier(out_addrs, COLD)

        if to_cold and not from_exch:
            category = "ACCUMULATION_TO_COLD"
            score = 9
        elif to_exch and not from_exch:
            category = "DEPOSIT_TO_EXCHANGE"
            score = 7
        elif from_exch and not to_exch:
            category = "WITHDRAWAL_FROM_EXCHANGE"
            score = 6
        elif from_exch and to_exch:
            category = "EXCHANGE_INTERNAL"
            score = 2
        else:
            category = "WHALE_OFF_EXCHANGE"
            score = 4

        event = {
            "ts":         block_ts,
            "ts_utc":     dt.datetime.fromtimestamp(block_ts, dt.timezone.utc).isoformat() if block_ts else None,
            "block":      height,
            "tx_hash":    h,
            "value_btc":  round(total_out, 2),
            "value_usd":  round(total_out * 81850, 0),  # approx, refreshed by downstream
            "category":   category,
            "score":      score,
            "from":       [{"addr": x["addr"], "v": round(x["v"], 2),
                             "tier": (x.get("w") or {}).get("tier"),
                             "label": (x.get("w") or {}).get("label")} for x in in_addrs],
            "to":         [{"addr": x["addr"], "v": round(x["v"], 2),
                             "tier": (x.get("w") or {}).get("tier"),
                             "label": (x.get("w") or {}).get("label")} for x in out_addrs],
        }
        state["recent_events"].append(event)
        n_whales += 1
        state["metrics"]["whale_txs_seen"] += 1

        # Update touched wallets' last_seen
        for x in (in_addrs + out_addrs):
            w = state["wallets"].get(x["addr"])
            if w:
                w["last_seen_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                w["last_block"]   = height

        # Emit to swarm if score >= 6 (skip noise)
        if score >= 6:
            head = (f"{category} {total_out:,.1f} BTC "
                    f"(~${total_out*81850/1e6:,.1f}M) "
                    f"block={height} tx={h[:10]}...")
            emit_swarm_event("onchain.whale", head, event,
                              ["onchain", "whale", category, f"score{score}"])
            state["metrics"]["swarm_emits"] += 1
            print(f"  [WHALE] {head}", flush=True)

    state["metrics"]["blocks_scanned"] += 1
    state["last_block_height"] = height
    return n_whales


def main() -> int:
    global _running
    print(f"=== sygnif_onchain_watcher started @ "
          f"{dt.datetime.now(dt.timezone.utc).isoformat()} ===", flush=True)
    print(f"  threshold:  {THRESHOLD_BTC} BTC", flush=True)
    print(f"  poll:       {POLL_INTERVAL_S}s", flush=True)
    print(f"  cold tier:  n_tx <= {COLD_TX_LIMIT}, no-spend", flush=True)
    print(f"  hot tier:   n_tx >= {HOT_TX_FLOOR}", flush=True)
    print(f"  state:      {STATE_FILE}", flush=True)

    state = load_state()
    print(f"  loaded state: {len(state['wallets'])} wallets, "
          f"{len(state['recent_events'])} events, "
          f"last_block={state['last_block_height']}", flush=True)

    def _sigterm(sig, frame):
        global _running
        print(f"  signal {sig}, shutting down", flush=True)
        _running = False
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    while _running:
        loop_t = time.time()
        try:
            tip_height = fetch_tip_height()
            if tip_height is None:
                print(f"  tip fetch failed, retry in {POLL_INTERVAL_S}s", flush=True)
                time.sleep(POLL_INTERVAL_S); continue

            if tip_height == state["last_block_height"]:
                # No new block
                time.sleep(POLL_INTERVAL_S); continue

            # Scan all new blocks since last seen (cap to 6 to avoid huge backfill)
            new_height = tip_height
            to_scan = min(6, new_height - state["last_block_height"]
                            if state["last_block_height"] > 0 else 1)

            tip_hash = fetch_tip_hash()
            if not tip_hash:
                time.sleep(POLL_INTERVAL_S); continue

            # Walk blocks from tip back
            print(f"\n  ↪ new block {tip_height} (scanning {to_scan} blocks)", flush=True)
            current_hash = tip_hash
            scanned = 0
            while current_hash and scanned < to_scan and _running:
                block = fetch_block_full(current_hash)
                if not block:
                    print(f"    block {current_hash[:16]} fetch failed", flush=True)
                    break
                events = scan_block(state, block)
                scanned += 1
                # Move to previous block
                current_hash = block.get("prev_block")
                if not current_hash:
                    break
                time.sleep(0.5)  # gentle on blockchain.info

            save_state(state)
            print(f"  ✓ saved state: {len(state['wallets'])} wallets, "
                  f"{len(state['recent_events'])} events, "
                  f"emits={state['metrics']['swarm_emits']}", flush=True)
        except Exception as e:
            print(f"  ! loop error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)

        elapsed = time.time() - loop_t
        sleep_for = max(POLL_INTERVAL_S - elapsed, 5)
        time.sleep(sleep_for)

    print(f"  shutting down, flushing state", flush=True)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
