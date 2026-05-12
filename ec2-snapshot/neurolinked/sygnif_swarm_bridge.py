#!/usr/bin/env python3
"""
Run NeuroLinked **Brain** with a live **SYGNIF Swarm (BTC)** sensory feed.

Usage (from SYGNIF repo root)::

  cd ~/SYGNIF && python3 third_party/neurolinked/sygnif_swarm_bridge.py --steps 50 --swarm-every 5

Requires ``third_party/neurolinked`` (vendored NeuroLinked) and the usual Swarm env (``swarm_operator.env`` / ``.env``).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_NL = Path(__file__).resolve().parent
_REPO = _NL.parents[1]

sys.path.insert(0, str(_NL))
sys.path.insert(0, str(_REPO / "finance_agent"))


def main() -> int:
    ap = argparse.ArgumentParser(description="NeuroLinked + SYGNIF Swarm BTC bridge")
    ap.add_argument("--steps", type=int, default=30, help="Brain simulation steps")
    ap.add_argument("--swarm-every", type=int, default=1, help="Re-pull Swarm every N steps")
    ap.add_argument("--neurons", type=int, default=None, help="Override BrainConfig total neurons (small for smoke)")
    ap.add_argument("--no-channel-json", action="store_true", help="Skip writing neurolinked_swarm_channel.json")
    args = ap.parse_args()

    from brain.brain import Brain  # noqa: PLC0415
    from neurolinked_swarm_adapter import NeurolinkedSwarmBridge  # noqa: PLC0415

    if args.neurons is not None:
        from brain import config as brain_config  # noqa: PLC0415

        brain_config.BrainConfig.TOTAL_NEURONS = max(1000, int(args.neurons))

    brain = Brain(total_neurons=args.neurons)
    bridge = NeurolinkedSwarmBridge(_REPO)

    for i in range(1, args.steps + 1):
        if (i % max(1, args.swarm_every)) == 0:
            try:
                meta = bridge.inject_into_brain(brain, write_channel=not args.no_channel_json)
                print(f"[sygnif-swarm-bridge] step={i} swarm_inject {meta}", flush=True)
            except Exception as exc:
                print(f"[sygnif-swarm-bridge] step={i} swarm_inject FAILED {exc!r}", flush=True)
        brain.step()
        if i % 10 == 0:
            print(f"[sygnif-swarm-bridge] step={i} ok", flush=True)
        time.sleep(0.01)

    print("[sygnif-swarm-bridge] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
