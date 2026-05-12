#!/usr/bin/env python3
"""sygnif_market_brain_feed.py — Pipe the market-intelligence synthesis
into NeuroLinked so the brain can learn associations with outcomes.

Every BRAIN_FEED_INTERVAL_S seconds:
  1. Invoke sygnif_market_synth (captures full intelligence dump as text)
  2. POST text to NeuroLinked at NL_URL/api/input/text
     - source: "market_synth"
     - executive_boost scales with magnitude of signals (bigger flows = louder)

Cadence: 300s default (every 5 minutes — slow enough to let brain integrate,
fast enough to catch shifts). Lighter brain sensory load than tick-by-tick
trade events but richer in semantic content.
"""
import datetime as dt
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.request

NL_URL              = os.environ.get("SYGNIF_NL_URL", "http://localhost:8889")
SYNTH_SCRIPT        = os.environ.get("SYGNIF_SYNTH_SCRIPT",
                                       "/opt/sygnif-services/sygnif_market_synth.py")
PYTHON_BIN          = os.environ.get("SYGNIF_PYTHON_BIN",
                                       "/opt/sygnif/.venv/bin/python")
FEED_INTERVAL_S     = float(os.environ.get("SYGNIF_BRAIN_FEED_S", "300"))
STATE_FILE          = pathlib.Path("/var/lib/sygnif/market_brain_feed.json")

_running = True


def run_synth() -> str:
    """Invoke the synth script, capture stdout as text payload."""
    try:
        r = subprocess.run([PYTHON_BIN, SYNTH_SCRIPT],
                            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"  synth non-zero exit {r.returncode}: "
                  f"{r.stderr[:300]}", file=sys.stderr, flush=True)
        return r.stdout
    except subprocess.TimeoutExpired:
        print(f"  synth TIMEOUT after 120s", file=sys.stderr, flush=True)
        return ""
    except Exception as e:
        print(f"  synth err {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return ""


def derive_boost(text: str) -> float:
    """Scale executive_boost by signal magnitude observed.

    Looks for high-impact tokens in the synth output and lifts boost.
    Range: 0.3 baseline → 1.0 max.
    """
    boost = 0.3
    upper = text.upper()
    if "DORMANCY_BREAK" in upper:           boost = max(boost, 0.9)
    if "LTH_SPEND_HEAVY" in upper:          boost = max(boost, 0.8)
    if "ACCUMULATION_TO_COLD" in upper:     boost = max(boost, 0.7)
    if "MEMPOOL_PRE_CONFIRMED" in upper:    boost = max(boost, 0.6)
    if "LIQUIDATION_CLUSTER" in upper:      boost = max(boost, 0.8)
    if "DEPEG" in upper:                    boost = max(boost, 1.0)
    if "SANCTIONED_FLOW" in upper:          boost = max(boost, 1.0)
    if "WHIRLPOOL" in upper or "WASABI" in upper:  boost = max(boost, 0.7)
    # Rough magnitude triggers — large numbers in the output bump boost
    for marker, lvl in [("$500M", 0.85), ("$1,000M", 0.95), ("$2,000M", 1.0)]:
        if marker in text:
            boost = max(boost, lvl)
    return boost


def post_to_brain(text: str, source: str, boost: float) -> bool:
    payload = json.dumps({
        "text":             text,
        "source":           source,
        "executive_boost":  boost,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{NL_URL}/api/input/text",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json",
                     "User-Agent": "sygnif-market-brain-feed/1.0"})
        r = urllib.request.urlopen(req, timeout=120)
        return 200 <= r.status < 300
    except Exception as e:
        print(f"  brain POST err {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return False


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"posts": 0, "ok_posts": 0, "started_at": time.time()}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"posts": 0, "ok_posts": 0, "started_at": time.time()}


def save_state(state: dict) -> None:
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, default=str, indent=2))
    os.replace(tmp, STATE_FILE)


def main() -> int:
    global _running
    print(f"=== sygnif_market_brain_feed started @ "
          f"{dt.datetime.now(dt.timezone.utc).isoformat()} ===",
          flush=True)
    print(f"  NL URL:        {NL_URL}", flush=True)
    print(f"  synth script:  {SYNTH_SCRIPT}", flush=True)
    print(f"  interval:      {FEED_INTERVAL_S}s", flush=True)

    if not pathlib.Path(SYNTH_SCRIPT).exists():
        print(f"  ! synth script not found: {SYNTH_SCRIPT}",
              file=sys.stderr, flush=True)
        return 1

    state = load_state()

    def _sigterm(sig, frame):
        global _running
        print(f"  signal {sig}", flush=True)
        _running = False
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    last_feed = 0.0
    while _running:
        now = time.time()
        if now - last_feed >= FEED_INTERVAL_S:
            text = run_synth()
            if text and len(text) > 200:
                boost = derive_boost(text)
                ok = post_to_brain(text, source="market_synth", boost=boost)
                state["posts"] += 1
                if ok:
                    state["ok_posts"] += 1
                head_lines = text.splitlines()[:3]
                head = " | ".join(l.strip() for l in head_lines if l.strip())[:120]
                print(f"  [{int(now)}] POST {'ok' if ok else 'FAIL'} "
                      f"boost={boost:.2f} chars={len(text)} | {head}",
                      flush=True)
                save_state(state)
            else:
                print(f"  [{int(now)}] synth empty or too short, skipping post",
                      flush=True)
            last_feed = now
        time.sleep(2)

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
