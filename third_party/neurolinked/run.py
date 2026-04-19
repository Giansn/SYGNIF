"""
NeuroLinked Brain - Entry Point

Starts the neuromorphic brain simulation and web dashboard.
Usage: python run.py [--neurons N] [--port P]
"""

import argparse
import sys
import os

# Force unbuffered output so logs appear immediately
os.environ["PYTHONUNBUFFERED"] = "1"

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description="NeuroLinked Neuromorphic Brain")
    parser.add_argument("--neurons", type=int, default=None,
                       help="Total neuron count (default: auto-detect from saved state, or 100000 if fresh)")
    parser.add_argument("--port", type=int, default=8000,
                       help="Server port (default: 8000)")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                       help="Server host (default: 0.0.0.0)")
    parser.add_argument("--fresh", action="store_true",
                       help="Ignore saved state and start a fresh brain")
    args = parser.parse_args()

    # Auto-detect saved neuron count to preserve existing brain state
    # This is the key safety feature: if you have a saved brain, we match its
    # neuron count so the state loads correctly instead of getting rejected.
    neuron_count = args.neurons
    if neuron_count is None and not args.fresh:
        try:
            from brain.persistence import get_save_info
            saved = get_save_info()
            if saved and "total_neurons" in saved:
                neuron_count = saved["total_neurons"]
                print(f"[RUN] Found saved brain: {saved.get('development_stage', '?')} "
                      f"stage, {saved.get('step_count', 0):,} steps, "
                      f"{neuron_count:,} neurons")
                print(f"[RUN] Matching saved neuron count to preserve state")
        except Exception:
            pass

    if neuron_count is None:
        # Fresh-download default: 10k neurons = ~3M synapses
        # Heavy enough to show real dynamics, light enough to run on any laptop.
        # Users can scale up with --neurons 50000 or higher once brain is growing.
        neuron_count = 10_000

    # Override config
    from brain.config import BrainConfig
    BrainConfig.TOTAL_NEURONS = min(neuron_count, 1_000_000)
    BrainConfig.PORT = args.port
    BrainConfig.HOST = args.host

    print("""
    ================================================
              N E U R O L I N K E D
         Neuromorphic Brain Simulation
    ================================================
      Neurons:    {:>10,}
      Regions:    11
      Dashboard:  http://localhost:{}
    ================================================
      Your brain state is preserved in brain_state/
      Updates: replace code files, keep brain_state
    ================================================
    """.format(BrainConfig.TOTAL_NEURONS, BrainConfig.PORT))

    import uvicorn
    from server import app

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        ws_max_size=16 * 1024 * 1024,  # 16MB websocket messages
        # httptools + heavy threaded sim occasionally left HTTP GET stalled on this host; h11 is slower but reliable.
        http="h11",
        # Default graceful shutdown can hang on open dashboards / WS; ops restarts must not block minutes.
        timeout_graceful_shutdown=8,
    )


if __name__ == "__main__":
    main()
