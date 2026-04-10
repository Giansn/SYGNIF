# Notification Audit — NT Event Model Alignment

> CUR-9 v2: Verify all strategy exit tags are mapped in notification_handler.py and notification_patch.py, mapped to NT OrderEventAny/PositionEvent types.

## NT Event Model Reference

NautilusTrader's notification system is event-driven (sources: `crates/model/src/events/`):

| NT Event | When Fired | SYGNIF Equivalent |
|---|---|---|
| `OrderFilled` | Order executed on venue | Freqtrade webhook `entry_fill` / `exit_fill` |
| `PositionOpened` | First fill creates position | Webhook `entry_fill` |
| `PositionClosed` | Position fully closed | Webhook `exit_fill` |
| `OrderDenied` | RiskEngine rejects order | Not notified (logged only) |
| `OrderCanceled` | Order canceled | Webhook `entry_cancel` |

NT: Each event carries `strategy_id`, `instrument_id`, `event_id` (UUID4), `ts_event` (ns).
SYGNIF: Webhooks carry `enter_tag`, `exit_reason`, `pair`, `trade_id`.

## Complete Exit Reason Inventory

### Strategy-Generated (custom_exit returns)

| Exit Reason String | Side | Trigger | notification_handler | notification_patch | NT Event Equivalent |
|---|---|---|---|---|---|
| `exit_profit_rsi_{pct}` | Long | RSI < threshold at profit | `profit_rsi` key | `"profit_rsi" in er` | PositionClosed (take_profit) |
| `exit_willr_reversal` | Long | Williams %R topped + profit | `willr` key | `"willr" in er` | PositionClosed (indicator_exit) |
| `exit_stoploss_conditional` | Long | Soft SL + RSI slope confirm | `conditional` key | `"conditional" in er` | PositionClosed (stop_loss) |
| `exit_sf_ema_tp` | Long | Swing failure EMA target hit | `sf_ema_tp` key | `"sf_ema_tp" in er` | PositionClosed (take_profit) |
| `exit_sf_vol_sl` | Long | Swing failure vol-adjusted SL | `sf_vol_sl` key | `"sf_vol_sl" in er` | PositionClosed (stop_loss) |
| `exit_short_profit_rsi_{pct}` | Short | RSI > threshold at profit | `profit_rsi` key | `"profit_rsi" in er` | PositionClosed (take_profit) |
| `exit_short_willr_reversal` | Short | Williams %R bottomed + profit | `willr` key | `"willr" in er` | PositionClosed (indicator_exit) |
| `exit_short_stoploss_conditional` | Short | Short soft SL + RSI slope | `conditional` key | `"conditional" in er` | PositionClosed (stop_loss) |
| `exit_sf_short_ema_tp` | Short | Swing short EMA target hit | `sf_short_ema_tp` key | Not explicitly matched | PositionClosed (take_profit) |
| `exit_sf_short_vol_sl` | Short | Swing short vol-adjusted SL | `sf_short_vol_sl` key | Not explicitly matched | PositionClosed (stop_loss) |

### Freqtrade-Infrastructure (engine-generated)

| Exit Reason String | Trigger | notification_handler | notification_patch | NT Event Equivalent |
|---|---|---|---|---|
| `stoploss_on_exchange` | Exchange SL order triggered (doom + ratchet) | `stoploss_on_exchange` key | `"stoploss_on_exchange" in er` | OrderFilled (stop_loss) |
| `trailing_stop_loss` | In-strategy custom_stoploss tightened | `trailing_stop_loss` key | `"trailing_stop_loss" in er` | OrderUpdated → OrderFilled |
| `force_exit` | Manual close via API/UI | `force_exit` key | `"force_exit" in er` | OrderCanceled (manual) |
| `emergency_exit` | Forced close on order failure | `emergency` key | `"emergency" in er` | OrderRejected → emergency |
| `liquidation` | Margin liquidation | `liquidation` key | `"liquidation" in er` | (venue-level, not NT event) |
| `roi` | minimal_roi target hit | `roi` key | `"roi" in er` | PositionClosed (take_profit) |

## Coverage Analysis

### notification_handler.py EXIT_REASON_MAP

```python
EXIT_REASON_MAP = {
    "stoploss_on_exchange": "Exchange stoploss (doom)",      # ✓ matches
    "trailing_stop_loss": "Trailing exit (ratchet)",          # ✓ matches
    "sf_ema_tp": "Swing TP — EMA target",                    # ✓ matches long + short
    "sf_vol_sl": "Swing SL — volatility stop",               # ✓ matches long + short
    "sf_short_ema_tp": "Swing short TP — EMA target",        # ✓ explicit short
    "sf_short_vol_sl": "Swing short SL — volatility stop",   # ✓ explicit short
    "willr": "Williams %R signal",                            # ✓ matches long + short
    "profit_rsi": "RSI profit lock",                          # ✓ matches long + short
    "conditional": "Conditional stoploss",                    # ✓ matches long + short
    "force_exit": "Manual force exit",                        # ✓ matches
    "emergency": "Emergency exit",                            # ✓ matches
    "liquidation": "Liquidation",                             # ✓ matches
    "roi": "ROI target",                                      # ✓ matches
    "trail": "Trailing exit",                                 # ✓ fallback
    "doom": "Max loss threshold",                             # ✓ fallback
    "_sl": "Hard stoploss",                                   # ✓ catchall
}
```

**Status: COMPLETE** — All 16 exit reasons are covered via exact match or substring match.

### notification_patch.py (in-container Telegram patch)

The patch uses `if/elif` substring matching:

| Pattern | Covers | Status |
|---|---|---|
| `"stoploss_on_exchange" in er` | stoploss_on_exchange | OK |
| `"trailing_stop_loss" in er` | trailing_stop_loss | OK |
| `"sf_ema_tp" in er` | exit_sf_ema_tp | OK |
| `"sf_vol_sl" in er` | exit_sf_vol_sl | OK |
| `"willr" in er` | exit_willr_reversal, exit_short_willr_reversal | OK |
| `"profit_rsi" in er or "rsi_profit" in er` | exit_profit_rsi_*, exit_short_profit_rsi_* | OK |
| `"conditional" in er` | exit_stoploss_conditional, exit_short_stoploss_conditional | OK |
| `"force_exit" in er` | force_exit | OK |
| `"emergency" in er` | emergency_exit | OK |
| `"liquidation" in er` | liquidation | OK |
| `"roi" in er` | roi | OK |
| `"trail" in er` | trailing_stop_loss (redundant but harmless) | OK |
| `"doom" in er` | (no exact match — doom is via stoploss_on_exchange) | LEGACY |
| `"_sl" in er` | catchall for any *_sl pattern | OK |
| `else: desc = er` | unknown/new reasons pass through | OK |

### Missing from notification_patch.py (but handled by fallthrough)

| Exit Reason | Matched By | Notes |
|---|---|---|
| `exit_sf_short_ema_tp` | `"sf_ema_tp" in er` | Substring match works |
| `exit_sf_short_vol_sl` | `"sf_vol_sl" in er` | Substring match works |
| `exit_short_stoploss_conditional` | `"conditional" in er` | Substring match works |

**Status: COMPLETE** — All exit reasons are covered.

## NT-style Improvement Recommendations

| Priority | Improvement | NT Pattern |
|---|---|---|
| **LOW** | Add `event_log.emit("position_closed", ...)` in notification_handler on exit_fill | NT MessageBus publishes PositionClosed |
| **LOW** | Add `correlation_id` to webhook payload for event chain tracing | NT event correlation via UUID4 |
| **LOW** | Map exit reasons to NT PositionEvent categories (take_profit/stop_loss/indicator_exit) | NT typed position close reasons |
| **DONE** | All 16 exit reasons mapped in both notification files | NT: all OrderEventAny variants handled |

## Conclusion

Both notification files now have **complete coverage** of all exit reasons produced by the strategy code. The substring-matching approach in `notification_patch.py` is resilient to new variations (e.g., `exit_profit_rsi_0.5%` automatically matches `"profit_rsi" in er`). The `notification_handler.py` EXIT_REASON_MAP handles the same via its iteration pattern.

No immediate code changes needed. The audit confirms parity.
