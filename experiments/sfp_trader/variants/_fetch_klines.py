"""Fetch + cache 30 days of BTCUSDT 1m klines from Bybit public API.

Runs once. All variants read from the cache. Avoids redundant API calls.

Usage:
  python _fetch_klines.py
"""
import gzip
import json
import pathlib
import time
import urllib.request

HERE = pathlib.Path(__file__).parent
OUT  = HERE / "_data" / "btc_1m_30d.jsonl.gz"
OUT.parent.mkdir(parents=True, exist_ok=True)

TARGET_BARS = 30 * 24 * 60  # 43,200 bars = 30 days × 1m


def fetch(end_ms: int) -> list:
    url = (f"https://api.bybit.com/v5/market/kline?"
           f"category=linear&symbol=BTCUSDT&interval=1&limit=1000&end={end_ms}")
    req = urllib.request.Request(url, headers={"User-Agent": "sygnif-backtest/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def main():
    if OUT.exists():
        size_kb = OUT.stat().st_size / 1024
        print(f"  cached file already present: {OUT} ({size_kb:.1f} kB) — skipping fetch")
        print(f"  to refresh, delete and re-run")
        return

    print(f"Fetching ~{TARGET_BARS} bars (target ~30d) into {OUT}...", flush=True)
    all_bars = []
    end_ms = int(time.time() * 1000)
    batches = 0
    while len(all_bars) < TARGET_BARS:
        r = fetch(end_ms)
        if r.get("retCode") != 0:
            print(f"  ! retCode={r.get('retCode')} msg={r.get('retMsg')}")
            break
        chunk = r["result"]["list"]
        if not chunk:
            break
        all_bars.extend(chunk)
        batches += 1
        end_ms = int(chunk[-1][0]) - 60_000
        if len(chunk) < 1000:
            break
        if batches % 5 == 0:
            oldest = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(chunk[-1][0]) / 1000))
            print(f"  pulled {len(all_bars)}/{TARGET_BARS}  oldest now {oldest}", flush=True)

    all_bars.sort(key=lambda b: int(b[0]))
    print(f"  collected {len(all_bars)} bars")

    bars = [{
        "ts_ms_open": int(b[0]),
        "open":   float(b[1]),
        "high":   float(b[2]),
        "low":    float(b[3]),
        "close":  float(b[4]),
        "volume": float(b[5]),
    } for b in all_bars]

    with gzip.open(OUT, "wt") as f:
        for b in bars:
            f.write(json.dumps(b) + "\n")
    print(f"  wrote {OUT}  ({OUT.stat().st_size / 1024:.1f} kB)")


if __name__ == "__main__":
    main()
