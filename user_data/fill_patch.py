"""
Patch: Replace verbose entry_fill / exit_fill messages with compact confirmations.
Run inside container: docker exec freqtrade python3 /freqtrade/user_data/fill_patch.py
"""

path = '/freqtrade/freqtrade/rpc/telegram.py'
with open(path) as f:
    content = f.read()

# Remove previous patch if present, re-apply clean
if 'FILL_PATCH_APPLIED' in content:
    # Revert by re-reading from the container's original (we'll just re-patch)
    # Remove the injected fill block from entry
    pass

# ---------- entry fill patch ----------
# Match the current state (may or may not already be patched)
old_entry_patched = '''    def _format_entry_msg(self, msg: RPCEntryMsg) -> str:  # FILL_PATCH_APPLIED
        is_fill = msg["type"] in [RPCMessageType.ENTRY_FILL]
        emoji = "\\N{CHECK MARK}" if is_fill else "\\N{LARGE BLUE CIRCLE}"

        if is_fill:
            rate = msg["open_rate"]
            qc = msg["quote_currency"]
            sl_normal = rate * 0.90
            sl_doom   = rate * 0.80
            tp_min    = rate * 1.01
            return (
                f"\\N{CHECK MARK} *{self._exchange_from_msg(msg)} Filled* #{msg['trade_id']} `{msg['pair']}`\\n"
                f"*Rate:* `{fmt_coin2(rate, qc)}`\\n"
                f"*Stake:* `{fmt_coin(msg['stake_amount'], qc)}`\\n"
                f"*SL:* `-10%` (`{fmt_coin2(sl_normal, qc)}`) · "
                f"`-20%` doom (`{fmt_coin2(sl_doom, qc)}`)\\n"
                f"*TP:* RSI-based · min `+1%` (`{fmt_coin2(tp_min, qc)}`)"
            )'''

old_entry_orig = '''    def _format_entry_msg(self, msg: RPCEntryMsg) -> str:
        is_fill = msg["type"] in [RPCMessageType.ENTRY_FILL]
        emoji = "\\N{CHECK MARK}" if is_fill else "\\N{OUTBOX TRAY}"'''

new_entry = '''    def _format_entry_msg(self, msg: RPCEntryMsg) -> str:  # FILL_PATCH_APPLIED
        is_fill = msg["type"] in [RPCMessageType.ENTRY_FILL]
        emoji = "\\N{CHECK MARK}" if is_fill else "\\N{OUTBOX TRAY}"

        if is_fill:
            rate = msg["open_rate"]
            stake = msg["stake_amount"]
            qc = msg["quote_currency"]
            tag = msg.get("enter_tag", "")
            # Tag-aware TP/SL levels
            if tag == "mover_gainer":
                tp_pcts = [0.015, 0.03, 0.05, 0.10]
                sl_pct = 0.07
                sl_label = "-7%"
                mode_label = " \\N{ROCKET} GAINER"
            elif tag == "mover_loser":
                tp_pcts = [0.01, 0.02, 0.03, 0.05]
                sl_pct = 0.05
                sl_label = "-5%"
                mode_label = " \\N{CHART WITH DOWNWARDS TREND} LOSER"
            else:
                tp_pcts = [0.01, 0.02, 0.05, 0.10]
                sl_pct = 0.10
                sl_label = "-10%"
                mode_label = ""
            tp_lines = ""
            for p in tp_pcts:
                tp_lines += f"  +{p:.0%} → `{fmt_coin2(rate * (1 + p), qc)}` (+`{stake * p:.2f}` {qc})\\n"
            sl_price = rate * (1 - sl_pct)
            sl_loss = stake * sl_pct
            doom_price = rate * 0.80
            doom_loss = stake * 0.20
            sl_block = f"  {sl_label} → `{fmt_coin2(sl_price, qc)}` (-`{sl_loss:.2f}` {qc})"
            if tag not in ("mover_gainer", "mover_loser"):
                sl_block += f"\\n  -20% doom ��� `{fmt_coin2(doom_price, qc)}` (-`{doom_loss:.2f}` {qc})"
            return (
                f"\\N{CHECK MARK} *{self._exchange_from_msg(msg)} Filled{mode_label}* #{msg['trade_id']} `{msg['pair']}`\\n"
                f"*Rate:* `{fmt_coin2(rate, qc)}` · *Stake:* `{fmt_coin(stake, qc)}`\\n"
                f"\\n"
                f"*TP targets:*\\n"
                f"{tp_lines}"
                f"\\n"
                f"*SL:*\\n"
                f"{sl_block}"
            )'''

# Try patched version first, then original
if old_entry_patched in content:
    content = content.replace(old_entry_patched, new_entry)
elif old_entry_orig in content:
    content = content.replace(old_entry_orig, new_entry)
else:
    print("ERROR: Could not find entry_fill target.")
    exit(1)

# ---------- exit fill patch ----------
# Check if already has the exit fill compact block
old_exit_patched = '''    def _format_exit_msg(self, msg: RPCExitMsg) -> str:
        is_fill = msg["type"] == RPCMessageType.EXIT_FILL
        if is_fill:
            duration = msg["close_date"].replace(microsecond=0) - msg["open_date"].replace(microsecond=0)
            dur_min = duration.total_seconds() / 60
            dur_str = f"{int(dur_min//60)}h{int(dur_min%60):02d}m" if dur_min >= 60 else f"{dur_min:.0f}m"
            qc = msg["quote_currency"]
            profit_fiat = self.__format_profit_fiat(msg, "profit_amount")
            pnl = fmt_coin(msg["profit_amount"], qc)
            pct = format_pct(msg["profit_ratio"])
            emoji = self._get_exit_emoji(msg)
            return (
                f"{emoji} *{self._exchange_from_msg(msg)} Closed* #{msg['trade_id']} `{msg['pair']}`\\n"
                f"*P/L:* `{pct}` (`{pnl}{profit_fiat}`)\\n"
                f"*Exit:* `{msg['exit_reason']}` · *Duration:* `{dur_str}`\\n"
                f"*Rate:* `{fmt_coin2(msg['close_rate'], qc)}`"
            )
        # --- original code below ---'''

old_exit_orig = '    def _format_exit_msg(self, msg: RPCExitMsg) -> str:'

new_exit = '''    def _format_exit_msg(self, msg: RPCExitMsg) -> str:
        is_fill = msg["type"] == RPCMessageType.EXIT_FILL
        if is_fill:
            duration = msg["close_date"].replace(microsecond=0) - msg["open_date"].replace(microsecond=0)
            dur_min = duration.total_seconds() / 60
            dur_str = f"{int(dur_min//60)}h{int(dur_min%60):02d}m" if dur_min >= 60 else f"{dur_min:.0f}m"
            qc = msg["quote_currency"]
            profit_fiat = self.__format_profit_fiat(msg, "profit_amount")
            pnl = fmt_coin(msg["profit_amount"], qc)
            pct = format_pct(msg["profit_ratio"])
            emoji = self._get_exit_emoji(msg)
            return (
                f"{emoji} *{self._exchange_from_msg(msg)} Closed* #{msg['trade_id']} `{msg['pair']}`\\n"
                f"*P/L:* `{pct}` (`{pnl}{profit_fiat}`)\\n"
                f"*Exit:* `{msg['exit_reason']}` · *Duration:* `{dur_str}`\\n"
                f"*Rate:* `{fmt_coin2(msg['close_rate'], qc)}`"
            )
        # --- original code below ---'''

if old_exit_patched in content:
    pass  # already good
elif old_exit_orig in content:
    content = content.replace(old_exit_orig, new_exit, 1)
else:
    print("ERROR: Could not find exit_msg target.")
    exit(1)

# ---------- emoji + wording for order placed ----------
content = content.replace('"1_enter": "New Trade"', '"1_enter": "Order Placed"')
content = content.replace('"x_enter": "Increasing position"', '"x_enter": "Order Placed (DCA)"')
content = content.replace('LARGE BLUE CIRCLE', 'OUTBOX TRAY')

with open(path, 'w') as f:
    f.write(content)

print("Fill patch v2 applied successfully.")
