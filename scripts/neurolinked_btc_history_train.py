#!/usr/bin/env python3
"""
NeuroLinked BTC History Trainer

Fetches all available BTC/USDT daily OHLCV data (Binance, 2017→now)
and feeds it into the NeuroLinked brain as structured text stimuli.

Each candle is converted to a rich text description including:
- Price action (open/high/low/close, range, body)
- Volume context
- Market regime labels (bull/bear/accumulation/distribution)
- Key psychological levels
- Notable events (halvings, crashes, ATHs)

Usage:
  python3 scripts/neurolinked_btc_history_train.py
  python3 scripts/neurolinked_btc_history_train.py --fast   # no delay between candles
  python3 scripts/neurolinked_btc_history_train.py --from 2020-01-01
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _load_env_repo() -> None:
    for path in (_REPO / ".env", Path.home() / "xrp_claude_bot" / ".env"):
        if path.exists():
            for line in path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    if k.strip() and k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")


_load_env_repo()

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    console = Console()
except ImportError:
    class Console:
        def print(self, *a, **k): print(*a)
    console = Console()

NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL") or "http://127.0.0.1:8889").strip().rstrip("/")

# ── notable BTC events ──────────────────────────────────────────────────────
EVENTS: dict[str, str] = {
    "2017-12-17": "ATH $19,783 — first major bubble peak",
    "2018-02-06": "Crash -65% from ATH — crypto winter begins",
    "2018-12-15": "Cycle bottom $3,122 — full bear market low",
    "2019-06-26": "Recovery peak $13,800 — pre-halving rally",
    "2020-03-12": "COVID crash -50% in 24h — Black Thursday",
    "2020-05-11": "Third halving — block reward 12.5→6.25 BTC",
    "2020-10-21": "PayPal enables BTC buying — institutional signal",
    "2020-12-16": "Breaks 2017 ATH $19,783 for first time",
    "2021-01-08": "Rapid rally to $40,000",
    "2021-02-08": "Tesla buys $1.5B BTC — corporate treasury adoption",
    "2021-04-14": "Coinbase IPO — $65K ATH nearby",
    "2021-05-19": "China mining ban crash -30% — Elon tweets",
    "2021-11-10": "All-time high $68,789",
    "2022-01-24": "Fed hawkish pivot — macro correlation begins",
    "2022-05-09": "LUNA/UST collapse — $40B wiped in 48h",
    "2022-06-18": "3AC bankruptcy — contagion to Celsius BlockFi",
    "2022-11-08": "FTX collapse begins — $32B exchange fails",
    "2022-11-21": "Cycle bottom $15,476 — bear market low",
    "2023-03-10": "SVB bank run — BTC +20% on banking fear",
    "2024-01-11": "Spot BTC ETF approved — BlackRock iShares",
    "2024-04-20": "Fourth halving — block reward 6.25→3.125 BTC",
    "2024-10-29": "Pre-election rally begins",
    "2024-11-05": "Trump election — BTC breaks $75K",
    "2024-12-17": "BTC hits $107,000 ATH",
}

REGIME_LABELS = [
    # (from_date, to_date, label)
    ("2017-08-17", "2017-12-17", "BULL_MANIA"),
    ("2017-12-18", "2018-12-15", "BEAR_WINTER"),
    ("2019-01-01", "2019-06-26", "RECOVERY"),
    ("2019-06-27", "2020-03-11", "CONSOLIDATION"),
    ("2020-03-12", "2020-03-13", "CRASH_COVID"),
    ("2020-03-14", "2020-11-30", "BULL_ACCUMULATION"),
    ("2020-12-01", "2021-04-14", "BULL_INSTITUTIONAL"),
    ("2021-04-15", "2021-07-20", "CORRECTION"),
    ("2021-07-21", "2021-11-10", "BULL_RETAIL"),
    ("2021-11-11", "2022-05-08", "BEAR_MACRO"),
    ("2022-05-09", "2022-05-12", "CRASH_LUNA"),
    ("2022-05-13", "2022-11-07", "BEAR_DEEP"),
    ("2022-11-08", "2022-11-10", "CRASH_FTX"),
    ("2022-11-11", "2023-01-01", "CAPITULATION"),
    ("2023-01-01", "2024-01-10", "RECOVERY_ETF_WAIT"),
    ("2024-01-11", "2024-04-19", "BULL_ETF"),
    ("2024-04-20", "2024-10-28", "POST_HALVING"),
    ("2024-10-29", "2025-12-31", "BULL_CYCLE_4"),
]


def _get_regime(date_str: str) -> str:
    for start, end, label in REGIME_LABELS:
        if start <= date_str <= end:
            return label
    return "UNKNOWN"


def _candle_to_text(candle: dict, prev_close: float | None = None) -> str:
    ts = candle["ts"]
    o, h, l, c, v = candle["o"], candle["h"], candle["l"], candle["c"], candle["v"]
    date_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    pct_change = ((c - o) / o * 100) if o else 0
    range_pct = ((h - l) / l * 100) if l else 0
    body_pct = ((c - o) / (h - l) * 100) if (h - l) > 0 else 0
    is_bull = c > o
    direction = "BULLISH" if is_bull else "BEARISH"

    # Momentum vs previous close
    momentum = ""
    if prev_close:
        day_change = (c - prev_close) / prev_close * 100
        if abs(day_change) > 5:
            momentum = f"EXTREME {'UP' if day_change > 0 else 'DOWN'} {day_change:+.1f}%"
        elif abs(day_change) > 2:
            momentum = f"STRONG {'UP' if day_change > 0 else 'DOWN'} {day_change:+.1f}%"

    regime = _get_regime(date_str)
    event = EVENTS.get(date_str, "")

    # Key psychological levels
    levels = []
    for level in [1000, 5000, 10000, 20000, 30000, 40000, 50000, 60000, 69000, 100000]:
        if l <= level <= h:
            levels.append(f"TOUCHED ${level:,}")
        elif abs(c - level) / level < 0.02:
            levels.append(f"NEAR ${level:,}")

    parts = [
        f"BTC {date_str}",
        f"{direction} o={o:.0f} h={h:.0f} l={l:.0f} c={c:.0f}",
        f"change={pct_change:+.1f}% range={range_pct:.1f}% body={body_pct:.0f}%",
        f"vol={v:.1f} regime={regime}",
    ]
    if momentum:
        parts.append(momentum)
    if levels:
        parts.append(" ".join(levels))
    if event:
        parts.append(f"EVENT: {event}")

    return " | ".join(parts)


def fetch_binance_history(start_ms: int) -> list[dict]:
    candles = []
    url_base = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000"
    current = start_ms
    now_ms = int(time.time() * 1000)

    console.print("[dim]Fetching BTC history from Binance...[/]")
    while current < now_ms:
        url = f"{url_base}&startTime={current}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sygnif-trainer/1.0"})
            batch = json.loads(urllib.request.urlopen(req, timeout=10).read())
        except Exception as e:
            console.print(f"[red]Fetch error: {e}[/]")
            break

        if not batch:
            break

        for k in batch:
            candles.append({
                "ts": k[0], "o": float(k[1]), "h": float(k[2]),
                "l": float(k[3]), "c": float(k[4]), "v": float(k[5])
            })

        last_ts = batch[-1][0]
        if last_ts <= current:
            break
        current = last_ts + 86400000  # next day
        time.sleep(0.2)

    console.print(f"[green]Fetched {len(candles)} daily candles[/]")
    return candles


def nl_feed(text: str) -> bool:
    try:
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            f"{NL_URL}/api/input/text", data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Train NeuroLinked brain on BTC history")
    parser.add_argument("--fast", action="store_true", help="No delay between candles")
    parser.add_argument("--from", dest="from_date", default="2017-08-17",
                        help="Start date YYYY-MM-DD (default: 2017-08-17)")
    parser.add_argument("--delay", type=float, default=0.05,
                        help="Seconds between candles (default: 0.05)")
    args = parser.parse_args()

    # Parse start date
    start_dt = datetime.strptime(args.from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)

    console.print(f"\n[bold cyan]NeuroLinked BTC History Trainer[/]")
    console.print(f"[dim]Training from {args.from_date} → today[/]")
    console.print(f"[dim]Target: {NL_URL}[/]\n")

    # Check neurolinked is up
    try:
        urllib.request.urlopen(f"{NL_URL}/api/sygnif/summary", timeout=3)
    except Exception:
        console.print(f"[red]NeuroLinked not reachable at {NL_URL}[/]")
        sys.exit(1)

    # Fetch data
    candles = fetch_binance_history(start_ms)
    if not candles:
        console.print("[red]No candles fetched[/]")
        sys.exit(1)

    delay = 0.0 if args.fast else args.delay

    # Send intro
    nl_feed(
        f"TRAINING SESSION BEGIN: Loading BTC/USDT price history "
        f"from {args.from_date} to present. "
        f"{len(candles)} daily candles. Learning market cycles, regimes, crashes, and rallies."
    )
    time.sleep(0.5)

    prev_close = None
    events_fed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TextColumn("[dim]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Feeding brain...", total=len(candles))

        for i, candle in enumerate(candles):
            text = _candle_to_text(candle, prev_close)
            nl_feed(text)
            prev_close = candle["c"]

            date_str = datetime.fromtimestamp(candle["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            if date_str in EVENTS:
                events_fed += 1

            progress.update(task, advance=1,
                            description=f"[bold cyan]{date_str} ${candle['c']:.0f}[/]")

            if delay:
                time.sleep(delay)

    # Send summary
    first = datetime.fromtimestamp(candles[0]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_c = candles[-1]["c"]
    nl_feed(
        f"TRAINING COMPLETE. Processed {len(candles)} BTC daily candles from {first}. "
        f"Fed {events_fed} major market events. Current BTC price: ${last_c:.0f}. "
        f"Brain now has full crypto market cycle context from bear winters to bull manias, "
        f"halvings, institutional adoption, exchange collapses, ETF approval, and macro correlations."
    )

    console.print(f"\n[bold green]Training complete![/]")
    console.print(f"  Candles fed: [cyan]{len(candles)}[/]")
    console.print(f"  Events fed:  [cyan]{events_fed}[/]")
    console.print(f"  Date range:  [cyan]{first} → {date_str}[/]")
    console.print(f"\n[dim]Brain now has BTC market cycle memory. Ask it anything.[/]")


if __name__ == "__main__":
    main()
