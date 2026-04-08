# Futures Shorts Module

This module hardens short-side decision quality for futures and reduces squeeze-driven losses.

## Objective

- Improve short expectancy and consistency during risk-off conditions.
- Block low-quality shorts in squeeze-prone environments.
- Standardize short entries, exits, and invalidation logic.

## Market Regime Gate (required)

Allow new shorts only when at least one regime condition is true:

- BTC regime is `BTC_TREND_DOWN`, or
- BTC regime is `BTC_RANGE` with failed breakout and bearish reclaim failure, or
- Symbol-specific trend is bearish (price below session VWAP and below EMA50/EMA200 context).

Block new shorts when:

- BTC regime is `BTC_HIGH_VOL_SHOCK` with upward impulse, or
- High-probability squeeze conditions are active.

## Short Setup Types

Use one of these setup labels on every short:

1. `breakdown_continuation`
2. `failed_breakout_reversal`
3. `lower_high_reject`
4. `swing_failure_short`

If setup label is missing, return `NO_TRADE`.

## Confirmation Stack (all required)

- Session filter: London/NY window preferred
- Directional bias: below session VWAP
- Participation: RVOL >= 1.5 on trigger candle
- Momentum/order flow proxy: bearish delta alignment
- Structure: clear rejection/breakdown level
- BTC dependency: aligned or explicitly low-correlation exception

If any required confirmation fails, return `NO_TRADE`.

## Squeeze Risk Filter (hard block)

Block shorts when 2+ squeeze conditions are true:

- Funding strongly negative (crowded short)
- Open interest rising while price holds/reclaims key resistance
- Fast upside impulse candles against short direction
- Liquidation cluster overhead likely to be swept

Status output should be:
- `permission = BLOCKED`
- `action = NO_TRADE`
- blocker reason includes `squeeze_risk`

## Entry/Stop/Target Rules

- Entry: breakdown close or lower-high rejection confirmation
- Initial stop: above invalidation swing / reclaimed resistance
- TP1: 1R
- TP2: 2R or session extension level
- Time stop: close if thesis not progressing within defined window

Do not place stops inside obvious sweep zones.

## Invalidation Rules (required)

Invalidate short if any occurs:

- Price reclaims session VWAP and holds
- Bullish structure break against short thesis
- BTC flips to strong uptrend regime during trade
- RVOL/momentum fails after trigger (no follow-through)

## Risk Controls

- Max leverage in recommendations: 2x-3x
- Max concurrent short setups per session: 1-2
- Reduce size when BTC volatility expands
- No new shorts during major event spikes unless explicitly enabled

## Output Add-on Fields

For each short decision, include:

- `short_setup_type`
- `squeeze_risk_score` (`low`/`medium`/`high`)
- `invalidation_trigger`
- `follow_through_window`

## Short-Side KPIs (weekly)

- Short expectancy
- Short win rate
- Average MAE and MFE on shorts
- Squeeze-fail rate
- Time-to-target vs time-to-stop
- % shorts blocked by squeeze filter
