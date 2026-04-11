# `SYGNIF_PROFILE=btc_trend` — backtest checklist

**Trend rule (v1):** `RSI_14_1h` & `RSI_14_4h` > 50, `close` > `EMA_200_1h`, `ADX_14` (5m) > 25. Entry tag: `btc_trend_long`. Implementation: `user_data/strategies/btc_trend_regime.py`.

## Before you run

1. **Pairlist:** BTC only (spot `BTC/USDT` or futures `BTC/USDT:USDT`). Other pairs get no entries in this profile.
2. **Env:** `SYGNIF_PROFILE=btc_trend` in the Freqtrade process (Docker `environment` or systemd `Environment=`). See `.env.example`.
3. **Example config:** `user_data/config_btc_trend_futures.example.json` (copy, set keys, rename if needed).
4. **Strategy:** `SygnifStrategy` or `MarketStrategy2` (same entry logic when not using `SYGNIF_STRATEGY_BACKEND=ms2` default stack — both files include the profile).
5. **Data:** Enough history for informative merges (1h EMA200 warmup).

## Dry-run / backtest steps

1. `freqtrade backtesting --config <cfg> --timerange <range>` with BTC whitelist and `SYGNIF_PROFILE=btc_trend`.
2. Confirm entries only use tag `btc_trend_long` (no `strong_ta`, `sygnif_s*`, ORB in this profile).
3. Check slot cap: at most `max_slots_btc_trend` (default 2) open `btc_trend_long` trades.
4. Compare **with vs without** profile on the same timerange (unset `SYGNIF_PROFILE`) to see delta from the rest of the stack.

## ML ablation (optional)

- `python3 scripts/train_ml_ensemble.py --limit 2000 --btc-trend-regime-only`
- `python3 scripts/train_btc_5m_direction.py train --limit 2000 --regime-filter`

These filter training rows to bars where `btc_trend_regime` is true (same definition as live).

## After the run

- Export trades; tag mix should be ~100% `btc_trend_long` for BTC.
- If zero trades: relax thresholds in `btc_trend_regime.py` only after noting the falsification (e.g. ADX bound, RSI bull min).
