# CPCV breakout demo (research)

**Purpose:** Reproduce the *educational flow* from Quant Beckman’s “Combinatorial Purged Cross Validation for optimization” article: toy **breakout** signals on a 1D series, **purged random splits**, **PSR** on test paths, and **robust N** from the **10th percentile PSR** distribution — **not** production trading logic.

**Source (external):** [WITH CODE: Combinatorial Purged Cross Validation for optimization](https://www.quantbeckman.com/p/with-code-combinatorial-purged-cross) (Sep 2025).

**Sygnif:** Research only; no Docker / strategy wiring. Optional second mode loads **`btc_*_ohlcv_long.json`** from `pull_btc_extended_history.py`.

## Run

```bash
cd ~/SYGNIF
# Synthetic path (default)
python3 research/cpcv_breakout_quantbeckman/breakout_cpcv_demo.py

# Bybit long closes (daily or 1h JSON)
python3 research/cpcv_breakout_quantbeckman/breakout_cpcv_demo.py \
  --json finance_agent/btc_specialist/data/btc_daily_ohlcv_long.json
```

## Caveats

- The splitter here is **random purged windows** (article-style demo), not the full **fixed combinatorial grid** from López de Prado’s AFML CPCV.
- **Purge size** should match your **maximum label horizon** in real work; the defaults are toy values.
