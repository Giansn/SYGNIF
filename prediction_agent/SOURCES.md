# Extracted upstream prediction code

Third-party **prediction-related** files copied for offline study. **Not** wired into Sygnif; paths match upstream layout where possible.

| Upstream | URL | Commit (shallow clone) |
|----------|-----|------------------------|
| BitVision | https://github.com/shobrook/BitVision | `6345fca` |
| CryptoPredictions | https://github.com/alimohammadiamirhossein/CryptoPredictions | `6f6ee3d` |

## Layout

- `bitvision/` — MIT license included (`LICENSE`). Core: `services/engine/{model,data_bus,transformers}.py`, `services/trader.py` (`make_prediction`), `services/__main__.py` (autotrade cron).
- `cryptopredictions/` — `train.py`, `data_loader/` (dataset + `creator.py` windowing), `factory/` (train/evaluate/profit), `models/` (all registered backends), `metrics/`, `configs/hydra/`, `backtest/strategies.py`.

Re-fetch upstream with `git clone` if you need full history or non-shallow commits.
