#!/usr/bin/env python3
"""
**Deep dive** — USDT-linear Bybit (demo or mainnet): positions, working orders, wallet, public ticker,
liquidation room, and a quick **SL vs liq** sanity check (long: SL must be **above** liq or you liquidate first).

Reuses the same credential routing as ``trade_overseer/bybit_linear_hedge.py``.

Examples::

  cd ~/SYGNIF && python3 scripts/bybit_linear_deep_dive.py
  python3 scripts/bybit_linear_deep_dive.py --json
  python3 scripts/bybit_linear_deep_dive.py --mainnet --json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def load_env(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        k, _, rest = s.partition("=")
        k = k.strip()
        if not k:
            continue
        v = rest.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if override or k not in os.environ:
            os.environ[k] = v


def _load_standard_env() -> None:
    repo = _repo()
    load_env(repo / ".env", override=False)
    load_env(Path.home() / "xrp_claude_bot" / ".env", override=True)
    extra = (os.environ.get("SYGNIF_SECRETS_ENV_FILE") or "").strip()
    if extra:
        load_env(Path(extra).expanduser(), override=True)


def _num(x: Any) -> float:
    try:
        return float(str(x).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct_room_mark(*, side: str, mark: float, liq: float) -> float | None:
    if mark <= 0 or liq <= 0:
        return None
    s = str(side or "").lower()
    if s == "buy":
        return (mark - liq) / mark * 100.0
    if s == "sell":
        return (liq - mark) / mark * 100.0
    return None


def _band(pct: float | None) -> str:
    if pct is None or math.isnan(pct):
        return "unknown"
    if pct >= 5.0:
        return "comfort"
    if pct >= 2.0:
        return "watch"
    if pct >= 0.5:
        return "stress"
    return "knife"


def _sl_liq_sanity(row: dict[str, Any], *, buffer_bps: float = 8.0) -> dict[str, Any]:
    """Long: SL must be > liq * (1+bps) or liquidation hits first."""
    side = str(row.get("side") or "")
    liq = _num(row.get("liqPrice"))
    sl = _num(row.get("stopLoss"))
    mark = _num(row.get("markPrice"))
    floor = liq * (1.0 + max(0.0, min(500.0, buffer_bps)) / 10000.0) if liq > 0 else 0.0
    ok = True
    note = ""
    if side.lower() == "buy" and liq > 0 and sl > 0:
        if sl <= liq:
            ok = False
            note = "long_sl_on_or_below_liq_liquidation_first"
        elif sl < floor:
            ok = False
            note = "long_sl_below_liq_plus_buffer"
    elif side.lower() == "sell" and liq > 0 and sl > 0:
        cap = liq * (1.0 - max(0.0, min(500.0, buffer_bps)) / 10000.0)
        if sl >= liq:
            ok = False
            note = "short_sl_on_or_above_liq_liquidation_first"
        elif sl > cap:
            ok = False
            note = "short_sl_above_liq_minus_buffer"
    return {
        "ok": ok,
        "note": note or "ok",
        "liq_floor_long_approx": round(floor, 4) if floor else None,
        "stop_loss": sl or None,
        "liq_price": liq or None,
        "mark_price": mark or None,
    }


def _public_ticker_linear(*, base: str, symbol: str) -> dict[str, Any] | None:
    import urllib.error
    import urllib.request

    sym = (symbol or "BTCUSDT").upper().strip()
    url = f"{base.rstrip('/')}/v5/market/tickers?category=linear&symbol={sym}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SYGNIF-bybit-linear-deep-dive/1"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("retCode") != 0:
        return None
    rows = (data.get("result") or {}).get("list") or []
    return rows[0] if rows and isinstance(rows[0], dict) else None


def run(*, as_json: bool) -> int:
    sys.path.insert(0, str(_repo() / "trade_overseer"))
    import bybit_linear_hedge as blh  # noqa: PLC0415

    mainnet = os.environ.get("OVERSEER_BYBIT_HEDGE_MAINNET", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    host = "api.bybit.com" if mainnet else "api-demo.bybit.com"
    pub_base = f"https://{host}"

    try:
        pos = blh._get(  # noqa: SLF001
            "/v5/position/list",
            {"category": "linear", "settleCoin": "USDT"},
        )
    except RuntimeError as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print("bybit_linear_deep_dive:", exc, file=sys.stderr)
        return 2

    if pos.get("retCode") != 0:
        if as_json:
            print(json.dumps({"ok": False, "response": pos}))
        return 1

    rows = (pos.get("result") or {}).get("list") or []
    open_pos = [r for r in rows if isinstance(r, dict) and abs(_num(r.get("size"))) > 1e-12]

    sym_set = {str(r.get("symbol", "")).upper() for r in open_pos if r.get("symbol")}
    sym_set.add("BTCUSDT")
    orders_by_symbol: dict[str, list[dict]] = {}
    for sym in sorted(sym_set):
        o = blh.get_open_orders_realtime_linear(sym)
        if o.get("retCode") != 0:
            orders_by_symbol[sym] = []
            continue
        ol = (o.get("result") or {}).get("list") or []
        orders_by_symbol[sym] = ol if isinstance(ol, list) else []

    wb = blh.wallet_balance_unified_coin("USDT")

    liq_analysis: list[dict[str, Any]] = []
    tickers: dict[str, Any] = {}
    for r in open_pos:
        if not isinstance(r, dict):
            continue
        mark = _num(r.get("markPrice"))
        liq = _num(r.get("liqPrice"))
        side = str(r.get("side") or "")
        pct = _pct_room_mark(side=side, mark=mark, liq=liq)
        sym = str(r.get("symbol") or "BTCUSDT").upper()
        if sym not in tickers:
            t = _public_ticker_linear(base=pub_base, symbol=sym)
            if t:
                tickers[sym] = t
        liq_analysis.append(
            {
                "symbol": sym,
                "side": side,
                "size": abs(_num(r.get("size"))),
                "leverage": _num(r.get("leverage")),
                "avg_price": _num(r.get("avgPrice")),
                "mark_price": mark,
                "liq_price": liq if liq > 0 else None,
                "unrealised_pnl": _num(r.get("unrealisedPnl")),
                "pct_mark_to_liq": round(pct, 4) if pct is not None else None,
                "liquidation_band": _band(pct),
                "position_tp_sl": {
                    "takeProfit": r.get("takeProfit"),
                    "stopLoss": r.get("stopLoss"),
                    "trailingStop": r.get("trailingStop"),
                },
                "sl_liq_sanity": _sl_liq_sanity(r),
            }
        )

    out: dict[str, Any] = {
        "ok": True,
        "host": host,
        "positions_open": open_pos,
        "liquidation_and_tpsl": liq_analysis,
        "open_orders_by_symbol": orders_by_symbol,
        "wallet_balance_usdt": wb,
        "public_tickers": tickers,
    }

    if as_json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"Bybit deep dive · {host}")
    print(f"Open positions: {len(open_pos)}")
    for block in liq_analysis:
        print(
            f"  {block['symbol']} {block['side']} size={block['size']} lev={block['leverage']}x "
            f"uPnL={block['unrealised_pnl']:.4f} band={block['liquidation_band']} "
            f"mark→liq={block['pct_mark_to_liq']}%"
        )
        sls = block.get("sl_liq_sanity") or {}
        if not sls.get("ok"):
            print(f"    ⚠ SL/liq: {sls.get('note')}")
        pt = block.get("position_tp_sl") or {}
        print(f"    TP={pt.get('takeProfit')} SL={pt.get('stopLoss')} trail={pt.get('trailingStop')}")
    print("Wallet USDT retCode:", wb.get("retCode"), wb.get("retMsg"))
    if wb.get("retCode") == 0:
        row = ((wb.get("result") or {}).get("list") or [{}])[0]
        coins = (row.get("coin") or [{}])[0] if isinstance(row, dict) else {}
        if isinstance(coins, dict):
            print(
                f"  equity≈{coins.get('equity')} wallet≈{coins.get('walletBalance')} "
                f"uPnL≈{coins.get('unrealisedPnl')}"
            )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Bybit USDT-linear deep dive (read-only)")
    ap.add_argument(
        "--mainnet",
        action="store_true",
        help="api.bybit.com + BYBIT_API_* (sets OVERSEER_BYBIT_HEDGE_MAINNET=1 and OVERSEER_HEDGE_LIVE_OK=YES)",
    )
    ap.add_argument("--json", action="store_true", help="Single JSON object to stdout")
    args = ap.parse_args()
    _load_standard_env()
    if args.mainnet:
        os.environ["OVERSEER_BYBIT_HEDGE_MAINNET"] = "1"
        os.environ["OVERSEER_HEDGE_LIVE_OK"] = "YES"
    return run(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
