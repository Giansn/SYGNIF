path = '/freqtrade/freqtrade/rpc/telegram.py'
with open(path) as f:
    content = f.read()

# Check if already patched with v2
if '_status_compact' in content and 'open_date' in content.split('_status_compact')[1][:2000]:
    print("Already patched v2")
    exit()

# Remove old compact if exists
if '_status_compact' in content:
    # Replace the whole _status_compact method
    start = content.index('    async def _status_compact')
    # Find next method
    rest = content[start + 10:]
    next_method = rest.index('\n    async def ') + start + 10
    old_method = content[start:next_method]
    content = content.replace(old_method, '')

# Now inject fresh _status_compact after _status dispatcher
marker = '''        if context.args and len(context.args) > 0 and context.args[0].isnumeric():
            await self._status_msg(update, context)
            return
        await self._status_compact(update, context)'''

if marker not in content:
    # First time - replace original dispatcher
    old_dispatch = '''        if context.args and "table" in context.args:
            await self._status_table(update, context)
            return
        else:
            await self._status_msg(update, context)'''
    if old_dispatch in content:
        content = content.replace(old_dispatch, marker)
    else:
        print("Cannot find dispatch pattern")
        exit()

new_method = '''
    async def _status_compact(self, update: Update, context: CallbackContext) -> None:
        """Compact status: ID Pair P/L% Duration Tag"""
        from datetime import datetime, timezone
        try:
            results = self._rpc._rpc_trade_status()
        except Exception:
            await self._send_msg("No open trades.")
            return
        if not results:
            await self._send_msg("No open trades.")
            return

        now = datetime.now(timezone.utc)
        lines = ["<pre>"]
        lines.append(f"{'ID':>3} {'Pair':<13} {'P/L%':>8} {'Dur':>7}  {'Tag'}")
        lines.append("-" * 48)
        total_pnl = 0.0
        total_stake = 0.0
        for t in sorted(results, key=lambda x: x.get("profit_ratio", 0), reverse=True):
            tid = str(t["trade_id"])
            pair = t["pair"].replace("/USDT", "")
            pct = t.get("profit_ratio", 0) * 100
            pnl = t.get("profit_abs", 0) or 0
            total_pnl += pnl
            total_stake += t.get("stake_amount", 0) or 0
            # Calculate duration from open_date
            od = t.get("open_date")
            dur = "--"
            if od:
                try:
                    if isinstance(od, str):
                        od = datetime.strptime(od, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    delta = now - od
                    secs = int(delta.total_seconds())
                    if secs >= 3600:
                        dur = f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
                    elif secs >= 60:
                        dur = f"{secs // 60}m"
                    else:
                        dur = f"{secs}s"
                except Exception:
                    dur = "--"
            tag = (t.get("enter_tag") or "--")[:10]
            sign = "+" if pct >= 0 else ""
            lines.append(f"{tid:>3} {pair:<13} {sign}{pct:.2f}%{dur:>7}  {tag}")
        lines.append("-" * 48)
        sign = "+" if total_pnl >= 0 else ""
        lines.append(f"    {'TOTAL':<13} {sign}{total_pnl:.4f} USDT  ({total_stake:.1f})")
        lines.append("</pre>")

        await self._send_msg(
            "\\n".join(lines),
            parse_mode=ParseMode.HTML,
            reload_able=True,
            callback_path="update_status_table",
            query=update.callback_query,
        )

'''

# Insert after the _status method
insert_point = content.index(marker) + len(marker)
# Find the next method after _status
next_after = content[insert_point:].index('\n    async def _status_msg')
insert_at = insert_point + next_after
content = content[:insert_at] + new_method + content[insert_at:]

with open(path, 'w') as f:
    f.write(content)
print("Patched v2 OK")
