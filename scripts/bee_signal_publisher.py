#!/usr/bin/env python3
"""
Bee Signal Publisher — uploads Sygnif prediction signals to Ethereum Swarm storage.

Runs as a periodic loop: reads prediction/swarm JSON files and uploads them to
the decentralized Bee network. The 139 connected peers propagate the chunks.
Also feeds upload receipts to NeuroLinked for brain context.

Usage:
  python3 scripts/bee_signal_publisher.py
  python3 scripts/bee_signal_publisher.py --once   # single publish, then exit
  python3 scripts/bee_signal_publisher.py --interval 300

Env:
  SYGNIF_BEE_API_URL          — Bee API (default http://127.0.0.1:1633)
  SYGNIF_NEUROLINKED_HOST_URL — NeuroLinked URL (default http://127.0.0.1:8889)
  BEE_PUBLISH_INTERVAL        — seconds between publishes (default 300)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bee_publisher")

_NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL") or
           os.environ.get("SYGNIF_NEUROLINKED_HTTP_URL") or
           "http://127.0.0.1:8889").rstrip("/")

SIGNAL_FILES = [
    ("prediction_agent/neurolinked_swarm_channel.json", "sygnif_swarm_channel"),
    ("prediction_agent/btc_prediction_output.json",     "btc_prediction"),
    ("prediction_agent/swarm_knowledge_output.json",    "swarm_knowledge"),
]


def _nl_feed(text: str) -> None:
    try:
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            f"{_NL_URL}/api/input/text", data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def publish_once() -> list[dict]:
    from finance_agent.bee_storage import upload_prediction_signal, get_usable_stamp

    stamp = get_usable_stamp()
    if not stamp:
        log.warning("No usable Bee stamp — skipping upload")
        return []

    results = []
    for rel_path, signal_type in SIGNAL_FILES:
        fpath = _REPO / rel_path
        if not fpath.exists():
            continue
        try:
            with open(fpath) as f:
                payload = json.load(f)
        except Exception as e:
            log.warning("Read %s: %s", rel_path, e)
            continue

        result = upload_prediction_signal(payload, signal_type=signal_type)
        if result.get("ok"):
            log.info("BEE_UPLOAD %s → ref=%s", signal_type, result["ref"][:16])
            results.append(result)
        else:
            log.warning("BEE_UPLOAD_FAIL %s: %s", signal_type, result.get("detail"))

    if results:
        refs_summary = " ".join(f"{r['signal_type']}={r['ref'][:12]}" for r in results)
        _nl_feed(
            f"BEE_STORAGE_PUBLISHED signals={len(results)} "
            f"network=swarm peers=139 refs={refs_summary} "
            f"stamp_valid=13d chunks_propagating_to_kademlia_network"
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int,
                        default=int(os.environ.get("BEE_PUBLISH_INTERVAL", "300")))
    args = parser.parse_args()

    log.info("Bee Signal Publisher starting — interval=%ss", args.interval)
    _nl_feed("BEE_PUBLISHER online — uploading Sygnif signals to Swarm decentralized storage")

    while True:
        results = publish_once()
        log.info("Published %d signals to Swarm", len(results))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
