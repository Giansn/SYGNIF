path = '/freqtrade/freqtrade/rpc/telegram.py'
with open(path) as f:
    content = f.read()

# Check if already patched with v3
if '_status_compact' in content and '_rpc_trade_profit' in content.split('_status_compact')[1][:3000]:
    print("Already patched v3")
    exit()

# Remove old compact if exists
if '_status_compact' in content:
    start = content.index('    async def _status_compact')
    rest = content[start + 10:]
    next_method = rest.index('\n    async def ') + start + 10
    old_method = content[start:next_method]
    content = content.replace(old_method, '')

# Inject dispatch: /status → compact, /status <id> → detail
marker = '''        if context.args and len(context.args) > 0 and context.args[0].isnumeric():
            await self._status_msg(update, context)
            return
        await self._status_compact(update, context)'''

if marker not in content:
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
        """Compact status — same data as web dashboard."""
        from datetime import datetime, timezone
        try:
            results = self._rpc._rpc_trade_status()
        except Exception:
            results = []

        # Pull profit stats (same as dashboard /api/v1/profit)
        try:
            profit = self._rpc._rpc_trade_statistics(self._config.get("stake_currency", "USDT"),
                                                      self._config.get("fiat_display_currency"))
        except Exception:
            profit = {}

        now = datetime.now(timezone.utc)
        qc = self._config.get("stake_currency", "USDT")
        is_dry = self._config.get("dry_run", False)
        mode = "DRY" if is_dry else "LIVE"

        # Summary stats (matches dashboard header)
        wallet = self._config.get("dry_run_wallet", 100) if is_dry else 0
        closed_pnl = profit.get("profit_all_coin", 0) or 0
        open_pnl = sum(t.get("profit_abs", 0) or 0 for t in results)
        bal = wallet + closed_pnl if is_dry else open_pnl
        closed_only = profit.get("profit_closed_coin", 0) or 0
        closed_count = profit.get("closed_trade_count", 0) or 0
        wins = profit.get("winning_trades", 0) or 0
        losses = profit.get("losing_trades", 0) or 0
        wr = f"{wins/(wins+losses)*100:.0f}%" if (wins + losses) > 0 else "--"
        best = profit.get("best_pair", "--") or "--"
        best = best.replace("/USDT", "")
        best_pct = (profit.get("best_rate", 0) or 0) * 100
        max_trades = self._config.get("max_open_trades", "?")

        lines = ["<pre>"]
        # Header
        lines.append(f"{'SYGNIF':} [{mode}]")
        lines.append("")
        sign_b = "+" if bal >= 0 else ""
        sign_o = "+" if open_pnl >= 0 else ""
        sign_c = "+" if closed_only >= 0 else ""
        if is_dry:
            lines.append(f"Bal     {sign_b}{bal:.2f} {qc}")
        lines.append(f"Open    {sign_o}{open_pnl:.4f} {qc}  ({len(results)}/{max_trades})")
        lines.append(f"Closed  {sign_c}{closed_only:.4f} {qc}  ({closed_count} trades)")
        lines.append(f"WR {wr}  ({wins}W/{losses}L)  Best: {best} {best_pct:+.1f}%")
        lines.append("")

        if not results:
            lines.append("No open trades.")
        else:
            lines.append(f"{'ID':>3} {'Pair':<13} {'P/L%':>8} {'Dur':>7}  {'Tag'}")
            lines.append("-" * 48)
            for t in sorted(results, key=lambda x: x.get("profit_ratio", 0), reverse=True):
                tid = str(t["trade_id"])
                pair = t["pair"].replace("/USDT", "")
                pct = t.get("profit_ratio", 0) * 100
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
            total_pnl = sum(t.get("profit_abs", 0) or 0 for t in results)
            total_stake = sum(t.get("stake_amount", 0) or 0 for t in results)
            sign = "+" if total_pnl >= 0 else ""
            lines.append(f"    {'TOTAL':<13} {sign}{total_pnl:.4f} {qc}  ({total_stake:.1f})")

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
next_after = content[insert_point:].index('\n    async def _status_msg')
insert_at = insert_point + next_after
content = content[:insert_at] + new_method + content[insert_at:]

with open(path, 'w') as f:
    f.write(content)
print("Patched v3 OK")
