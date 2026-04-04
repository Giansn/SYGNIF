path = '/freqtrade/freqtrade/rpc/telegram.py'
with open(path) as f:
    content = f.read()

old = '''        if context.args and "table" in context.args:
            await self._status_table(update, context)
            return
        else:
            await self._status_msg(update, context)'''

new = '''        if context.args and len(context.args) > 0 and context.args[0].isnumeric():
            await self._status_msg(update, context)
            return
        await self._status_compact(update, context)

    async def _status_compact(self, update: Update, context: CallbackContext) -> None:
        """Compact status view: ID Pair P/L% Duration Tag"""
        try:
            results = self._rpc._rpc_trade_status()
        except Exception:
            await self._send_msg("No open trades.")
            return
        if not results:
            await self._send_msg("No open trades.")
            return

        lines = ["<pre>"]
        lines.append(f"{'ID':>3} {'Pair':<14}{'P/L%':>8} {'Dur':>7}  {'Tag':<12}")
        lines.append("-" * 50)
        total_pnl = 0.0
        total_stake = 0.0
        for t in sorted(results, key=lambda x: x.get("profit_ratio", 0), reverse=True):
            tid = str(t["trade_id"])
            pair = t["pair"].replace("/USDT", "")
            pct = t.get("profit_ratio", 0) * 100
            pnl = t.get("profit_abs", 0) or 0
            total_pnl += pnl
            total_stake += t.get("stake_amount", 0) or 0
            dur_s = t.get("trade_duration", 0) or 0
            h, m = divmod(int(dur_s), 3600)
            m = m // 60
            dur = f"{h}h{m:02d}m" if h else f"{m}m" if dur_s else "--"
            tag = (t.get("enter_tag") or "--")[:12]
            sign = "+" if pct >= 0 else ""
            lines.append(f"{tid:>3} {pair:<14}{sign}{pct:.2f}%{dur:>7}  {tag}")
        lines.append("-" * 50)
        sign = "+" if total_pnl >= 0 else ""
        lines.append(f"    {'TOTAL':<14}{sign}{total_pnl:.4f} USDT  (stk: {total_stake:.1f})")
        lines.append("</pre>")

        await self._send_msg(
            "\\n".join(lines),
            parse_mode=ParseMode.HTML,
            reload_able=True,
            callback_path="update_status_table",
            query=update.callback_query,
        )'''

if old in content:
    content = content.replace(old, new)
    with open(path, 'w') as f:
        f.write(content)
    print("Patched OK")
else:
    print("Pattern not found - may already be patched")
