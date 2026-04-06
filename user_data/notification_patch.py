"""
Notification Patch v3 — Custom Telegram trade notifications.
Applied at container startup via docker-compose entrypoint.

SPOT:    Order Placed (entry, amount, reason)
         Order Filled (TP, SL, expected win, possible loss)
         Exit (P/L, trade review)

FUTURES: Order Placed (entry, amount, long/short, reason)
         Order Filled (TP, SL, expected win, possible loss)
         Exit (P/L, trade review)
"""
import re
import sys

path = '/freqtrade/freqtrade/rpc/telegram.py'
with open(path) as f:
    content = f.read()

if 'NOTIFICATION_PATCH_V3' in content:
    print("Notification patch v3 already applied.")
    sys.exit()


def _method_bounds(src, name):
    """Return (start, end) indices of a class-level method."""
    m = re.search(rf'    def {re.escape(name)}\(', src)
    if not m:
        return None, None
    s = m.start()
    nxt = re.search(r'\n    (?:async\s+)?def ', src[s + 1:])
    e = (s + 1 + nxt.start()) if nxt else len(src)
    return s, e


# =====================================================================
#  ENTRY MESSAGE — full method replacement
# =====================================================================
es, ee = _method_bounds(content, '_format_entry_msg')
if es is None:
    print("ERROR: _format_entry_msg not found")
    sys.exit(1)

content = content[:es] + r'''    def _format_entry_msg(self, msg: RPCEntryMsg) -> str:  # NOTIFICATION_PATCH_V3
        is_fill = msg["type"] in [RPCMessageType.ENTRY_FILL]
        is_futures = self._config.get("trading_mode", "") == "futures"
        rate = msg["open_rate"]
        stake = msg["stake_amount"]
        qc = msg["quote_currency"]
        tag = msg.get("enter_tag", "") or ""
        leverage = msg.get("leverage") or 1.0
        direction = msg.get("direction", "Long") or "Long"
        is_short = msg.get("is_short", False)
        pair = msg["pair"]
        tid = msg["trade_id"]
        # Human-readable reason
        if tag == "strong_ta":
            reason = "Strong TA signal (score \u2265 75)"
        elif tag == "strong_ta_short":
            reason = "Strong bearish TA (score \u2264 25)"
        elif tag == "mover_gainer":
            reason = "Top gainer \u2014 momentum pullback"
        elif tag == "mover_loser":
            reason = "Top loser \u2014 mean-reversion bounce"
        elif tag.startswith("claude_short_s"):
            reason = f"AI bearish sentiment ({tag.replace('claude_short_s', '+')})"
        elif tag.startswith("claude_s"):
            reason = f"AI bullish sentiment ({tag.replace('claude_s', '+')})"
        elif tag == "claude_swing":
            reason = "Failure Swing \u2014 TA confluence"
        elif tag == "swing_failure":
            reason = "Failure Swing \u2014 standalone"
        elif tag == "claude_swing_short":
            reason = "Bearish Swing \u2014 TA confluence"
        elif tag == "swing_failure_short":
            reason = "Bearish Swing \u2014 standalone"
        else:
            reason = tag if tag else "Standard signal"
        # ── ORDER PLACED ──
        if not is_fill:
            lines = [f"\N{CLIPBOARD} *Order Placed* #{tid} `{pair}`"]
            lines.append(f"*Entry:* `{fmt_coin2(rate, qc)}`")
            if is_futures:
                exposure = stake * leverage
                lines.append(f"*Amount:* `{fmt_coin(stake, qc)}` ({leverage:.0f}x \u2192 `{fmt_coin(exposure, qc)}`)")
                de = "\N{CHART WITH UPWARDS TREND}" if not is_short else "\N{CHART WITH DOWNWARDS TREND}"
                lines.append(f"*Direction:* {de} {direction.upper()}")
            else:
                lines.append(f"*Amount:* `{fmt_coin(stake, qc)}`")
            lines.append(f"*Reason:* {reason}")
            if tag in ("swing_failure", "swing_failure_short"):
                lines.append(f"*Exit plan:* Swing TP/SL")
            elif tag in ("claude_swing", "claude_swing_short"):
                lines.append(f"*Exit plan:* Swing \u2192 General")
            else:
                lines.append(f"*Exit plan:* RSI/WillR/SL")
            return "\n".join(lines)
        # ── ORDER FILLED ──
        if tag == "mover_gainer":
            tp_pcts = [0.015, 0.03, 0.05, 0.10]
            sl_pct = 0.07
        elif tag == "mover_loser":
            tp_pcts = [0.01, 0.02, 0.03, 0.05]
            sl_pct = 0.05
        else:
            tp_pcts = [0.01, 0.02, 0.05, 0.10]
            sl_pct = 0.10
        position = stake * leverage
        tp_lines = ""
        for p in tp_pcts:
            tp_price = rate * (1 - p) if is_short else rate * (1 + p)
            tp_usd = position * p
            tp_lines += f"  +{p*100:g}% \u2192 `{fmt_coin2(tp_price, qc)}` (+`{tp_usd:.2f}` {qc})\n"
        sl_price = rate * (1 + sl_pct) if is_short else rate * (1 - sl_pct)
        sl_usd = position * sl_pct
        min_win = position * tp_pcts[0]
        max_win = position * tp_pcts[-1]
        hdr = f"\N{CHECK MARK} *Filled* #{tid} `{pair}`"
        if is_futures:
            de = "\N{CHART WITH UPWARDS TREND}" if not is_short else "\N{CHART WITH DOWNWARDS TREND}"
            hdr += f" \u00b7 {de} {direction.upper()} {leverage:.0f}x"
        return (
            f"{hdr}\n"
            f"*Rate:* `{fmt_coin2(rate, qc)}` \u00b7 *Stake:* `{fmt_coin(stake, qc)}`\n"
            f"\n"
            f"*TP targets:*\n"
            f"{tp_lines}"
            f"*SL:* -{sl_pct*100:g}% \u2192 `{fmt_coin2(sl_price, qc)}` (-`{sl_usd:.2f}` {qc})\n"
            f"\n"
            f"*Expected win:* `+{min_win:.2f}` to `+{max_win:.2f}` {qc}\n"
            f"*Possible loss:* `-{sl_usd:.2f}` {qc}"
        )
''' + '\n' + content[ee:]

print("Entry patch applied.")

# =====================================================================
#  EXIT MESSAGE — inject at top, fall through for non-fill
# =====================================================================
exit_re = re.search(
    r'(    def _format_exit_msg\(self, msg: RPCExitMsg\) -> str:[^\n]*)\n',
    content,
)
if not exit_re:
    print("ERROR: _format_exit_msg not found")
    sys.exit(1)

content = content.replace(exit_re.group(0), r'''    def _format_exit_msg(self, msg: RPCExitMsg) -> str:  # NOTIFICATION_PATCH_V3
        is_fill = msg["type"] == RPCMessageType.EXIT_FILL
        if is_fill:
            duration = msg["close_date"].replace(microsecond=0) - msg["open_date"].replace(microsecond=0)
            dur_s = int(duration.total_seconds())
            if dur_s >= 3600:
                dur_str = f"{dur_s // 3600}h{(dur_s % 3600) // 60:02d}m"
            elif dur_s >= 60:
                dur_str = f"{dur_s // 60}m"
            else:
                dur_str = f"{dur_s}s"
            dur_min = dur_s / 60.0
            qc = msg["quote_currency"]
            try:
                profit_fiat = self.__format_profit_fiat(msg, "profit_amount")
            except Exception:
                profit_fiat = ""
            pnl = fmt_coin(msg["profit_amount"], qc)
            profit_ratio = msg["profit_ratio"]
            pct = f"{profit_ratio:+.2%}"
            emoji = self._get_exit_emoji(msg)
            exit_reason = msg.get("exit_reason", "unknown")
            open_rate = msg.get("open_rate", 0)
            close_rate = msg.get("close_rate", 0) or msg.get("current_rate", 0)
            leverage = msg.get("leverage") or 1.0
            direction = msg.get("direction", "Long") or "Long"
            is_short = msg.get("is_short", False)
            is_futures = self._config.get("trading_mode", "") == "futures"
            # Header
            hdr = f"{emoji} *Closed* #{msg['trade_id']} `{msg['pair']}`"
            if is_futures:
                de = "\N{CHART WITH UPWARDS TREND}" if not is_short else "\N{CHART WITH DOWNWARDS TREND}"
                hdr += f" \u00b7 {de} {direction.upper()} {leverage:.0f}x"
            # Exit reason mapping
            er = exit_reason
            if "momentum_fade" in er: desc = "Momentum fade detected"
            elif "overbought" in er: desc = "Overbought signal"
            elif "oversold" in er: desc = "Oversold signal"
            elif "trail" in er: desc = "Trailing exit"
            elif "bounce" in er: desc = "Bounce captured"
            elif "secure" in er: desc = "Profit secured"
            elif "rsi3_spike" in er: desc = "RSI momentum spike"
            elif "extreme_rsi" in er: desc = "Extreme RSI"
            elif "multi_tf" in er: desc = "Multi-TF alignment"
            elif "bb_stretch" in er: desc = "Bollinger stretch"
            elif "willr" in er: desc = "Williams %R signal"
            elif "profit_rsi" in er or "rsi_profit" in er: desc = "RSI profit lock"
            elif "sf_ema_tp" in er: desc = "Swing TP \u2014 EMA target"
            elif "sf_vol_sl" in er: desc = "Swing SL \u2014 volatility stop"
            elif "doom" in er: desc = "Max loss threshold"
            elif "conditional" in er: desc = "Conditional stoploss"
            elif "_sl" in er: desc = "Hard stoploss"
            else: desc = er
            is_sl = "stoploss" in er or "_sl" in er
            is_doom = "doom" in er
            # Review: what went well / what went wrong
            review = []
            if profit_ratio > 0:
                review.append(f"\N{WHITE HEAVY CHECK MARK} {desc}")
                if dur_min < 60:
                    review.append(f"\N{WHITE HEAVY CHECK MARK} Quick trade \u2014 in and out in {dur_str}")
                elif is_futures and leverage > 1:
                    review.append(f"\N{WHITE HEAVY CHECK MARK} {leverage:.0f}x leverage amplified the gain")
                else:
                    review.append(f"\N{WHITE HEAVY CHECK MARK} Clean exit after {dur_str}")
            else:
                review.append(f"\N{CROSS MARK} {desc}")
                if is_doom:
                    review.append(f"\N{CROSS MARK} Position hit max loss limit")
                elif is_sl:
                    review.append(f"\N{ELECTRIC LIGHT BULB} SL limited loss to `{pnl}`")
                elif dur_min > 240:
                    review.append(f"\N{CROSS MARK} Extended {dur_str} hold didn't recover")
                else:
                    review.append(f"\N{ELECTRIC LIGHT BULB} Position went against thesis")
            review_text = "\n".join(review)
            return (
                f"{hdr}\n"
                f"*P/L:* `{pct}` (`{pnl}{profit_fiat}`)\n"
                f"*Entry:* `{fmt_coin2(open_rate, qc)}` \u2192 *Exit:* `{fmt_coin2(close_rate, qc)}`\n"
                f"*Duration:* `{dur_str}`\n"
                f"\n"
                f"\N{BAR CHART} *Review:*\n"
                f"{review_text}"
            )
        # non-fill exit: fall through to original code below
''', 1)

print("Exit patch applied.")

# =====================================================================
#  STATUS / WARNING / STARTUP — replace with "System up/down"
# =====================================================================

# Replace STATUS handler (line 693-694 area)
old_status = '            message = f"*Status:* `{msg[\'status\']}`"'
new_status = r'''            _st = msg['status']
            if _st == 'running':
                try:
                    _trades = len(self._rpc._rpc_trade_status())
                except Exception:
                    _trades = 0
                _mode = 'DRY' if self._config.get('dry_run') else 'LIVE'
                message = f"\N{WHITE HEAVY CHECK MARK} *System up.* {_trades} trades monitored. [{_mode}]"
            elif 'died' in _st or 'stop' in _st:
                try:
                    _trades = len(self._rpc._rpc_trade_status())
                except Exception:
                    _trades = 0
                _mode = 'DRY' if self._config.get('dry_run') else 'LIVE'
                message = f"\N{NO ENTRY} *System down.* {_trades} open trades. [{_mode}]"
            else:
                message = f"*Status:* `{_st}`"'''

if old_status in content:
    content = content.replace(old_status, new_status, 1)
    print("Status patch applied.")
else:
    print("WARNING: Status pattern not found.")

# Suppress WARNING messages (dry run, open trades, etc.)
old_warning = r'            message = f"\N{WARNING SIGN} *Warning:* `{msg[' + "'status']}`" + '"'
new_warning = '            message = None  # suppress warnings (dry run, etc.)'

if old_warning in content:
    content = content.replace(old_warning, new_warning, 1)
    print("Warning suppressed.")
else:
    # Try alternate pattern
    old_w2 = "message = f\"\\N{WARNING SIGN} *Warning:* `{msg['status']}`\""
    if old_w2 in content:
        content = content.replace(old_w2, 'message = None  # suppress warnings', 1)
        print("Warning suppressed (alt).")
    else:
        print("WARNING: Warning pattern not found.")

# Suppress STARTUP info block
old_startup = '        elif msg["type"] == RPCMessageType.STARTUP:\n            message = f"{msg[\'status\']}"'
new_startup = '        elif msg["type"] == RPCMessageType.STARTUP:\n            message = None  # suppress verbose startup block'
if old_startup in content:
    content = content.replace(old_startup, new_startup, 1)
    print("Startup suppressed.")
else:
    print("WARNING: Startup pattern not found.")

# =====================================================================
#  LABEL REPLACEMENTS
# =====================================================================
content = content.replace('"1_enter": "New Trade"', '"1_enter": "Order Placed"')
content = content.replace('"x_enter": "Increasing position"', '"x_enter": "Order Placed (DCA)"')
content = content.replace('LARGE BLUE CIRCLE', 'OUTBOX TRAY')

with open(path, 'w') as f:
    f.write(content)

print("Notification patch v3 applied successfully.")
