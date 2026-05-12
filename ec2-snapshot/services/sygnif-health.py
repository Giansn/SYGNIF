#!/usr/bin/env python3
"""SYGNIF one-shot health check (runs on EC2).

Designed to fit in <2500 chars of stdout so the SSM aws.cli neuron's
3000-char truncation window keeps the entire output intact.

Sections: services / wallet / positions / orders / BTC / daemon stats /
cadence / log tails.
"""
import os, sys, json, time, hmac, hashlib, urllib.request, subprocess, re
from datetime import datetime

ENV = "/etc/sygnif/bybit-mcp.env"
LOG = "/var/log/sygnif"

if os.path.exists(ENV):
    for line in open(ENV):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

K = os.environ.get("BYBIT_DEMO_PERP_API_KEY") or os.environ.get("BYBIT_DEMO_OPTION_API_KEY")
S = os.environ.get("BYBIT_DEMO_PERP_API_SECRET") or os.environ.get("BYBIT_DEMO_OPTION_API_SECRET")


def sgn_get(path, params, timeout=6):
    q = "&".join(f"{a}={b}" for a, b in sorted(params.items()))
    ts = str(int(time.time() * 1000))
    sig = hmac.new(S.encode(), f"{ts}{K}5000{q}".encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"https://api-demo.bybit.com{path}?{q}",
        headers={"X-BAPI-API-KEY": K, "X-BAPI-TIMESTAMP": ts,
                 "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sig})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def pub_get(path, params, timeout=6):
    q = "&".join(f"{a}={b}" for a, b in sorted(params.items()))
    return json.loads(urllib.request.urlopen(f"https://api.bybit.com{path}?{q}", timeout=timeout).read())


def sh(args, timeout=3):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


# 1. services
print("== SERVICES ==")
for svc in ("sygnif-bybit-daemon", "sygnif-trader", "sygnif-trade-nl-publisher"):
    state = sh(["systemctl", "is-active", svc]).strip()
    info = sh(["systemctl", "show", svc, "--property=ActiveEnterTimestamp,MainPID"])
    pid = next((l.split("=", 1)[1] for l in info.splitlines() if l.startswith("MainPID=")), "?")
    ts = next((l.split("=", 1)[1] for l in info.splitlines() if l.startswith("ActiveEnterTimestamp=")), "?")
    print(f"  {svc:30s} {state:8s} pid={pid} since {ts.split(' UTC')[0]}")

# 2. wallet
print("\n== WALLET (demo) ==")
try:
    a = sgn_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})["result"]["list"][0]
    eq = float(a["totalEquity"]); av = float(a["totalAvailableBalance"])
    im = float(a["totalInitialMargin"]); mm = float(a["totalMaintenanceMargin"])
    print(f"  eq=${eq:.2f}  avail=${av:.2f} ({av/eq*100:.0f}%)  IM=${im:.2f} ({im/eq*100:.1f}%)  MM=${mm:.2f}")
except Exception as e:
    print(f"  ERR: {e}")

# 3. positions
print("\n== POSITIONS ==")
for cat, lab in (("linear", "perp"), ("option", "opt")):
    try:
        r = sgn_get("/v5/position/list", {"category": cat, "settleCoin": "USDT", "limit": 20})
        ps = [p for p in r["result"]["list"] if float(p.get("size", 0) or 0) != 0]
        print(f"  {lab}: {len(ps)}")
        for p in ps[:4]:
            print(f"    {p['symbol']} {p['side']} sz={p['size']} entry={p.get('avgPrice', '?')} uPnL={p.get('unrealisedPnl', '0')}")
    except Exception as e:
        print(f"  {lab} ERR: {e}")

# 4. orders
print("\n== ORDERS ==")
tot = 0
for cat, lab in (("linear", "perp"), ("option", "opt"), ("spot", "spot")):
    try:
        r = sgn_get("/v5/order/realtime", {"category": cat, "settleCoin": "USDT"})
        lst = r["result"]["list"]; tot += len(lst)
        if lst:
            print(f"  {lab}: {len(lst)}")
            for o in lst[:4]:
                stop = o.get("stopOrderType") or "-"
                print(f"    {o['symbol']} {o['side']} qty={o['qty']} px={o.get('price', '-')} {o.get('orderType', '?')} stop={stop} {o.get('orderStatus', '')}")
    except Exception as e:
        print(f"  {lab} ERR: {e}")
if tot == 0:
    print("  none")

# 5. BTC
print("\n== BTC ==")
try:
    t = pub_get("/v5/market/tickers", {"category": "linear", "symbol": "BTCUSDT"})["result"]["list"][0]
    print(f"  last={t['lastPrice']} bid={t['bid1Price']} ask={t['ask1Price']}")
    print(f"  24h: hi={t['highPrice24h']} lo={t['lowPrice24h']} vol={t['volume24h']}")
except Exception as e:
    print(f"  ERR: {e}")

# 6. daemon stats
print("\n== DAEMON STATS ==")
try:
    out = sh(["grep", "stats: up=", f"{LOG}/bybit-daemon.log"])
    lines = [l for l in out.splitlines() if "stats: up=" in l]
    if lines:
        m = re.search(r"up=(\d+)s msgs=(\d+) .*events=(\d+) executed=(\d+) failed=(\d+).*opt\[ticks=(\d+).*?\] perp\[ticks=(\d+).*?\] last_msg_age=(\d+)s", lines[-1])
        if m:
            up, msgs, ev, ex, fl, ot, pt, age = map(int, m.groups())
            print(f"  up={up//3600}h{(up%3600)//60}m  msgs={msgs} ({msgs*60/max(up,1):.1f}/min)  age={age}s")
            print(f"  events={ev} exe={ex} failed={fl}")
            print(f"  opt_ticks={ot} ({ot*60/max(up,1):.1f}/min)  perp_ticks={pt} ({pt*60/max(up,1):.1f}/min)")
except Exception as e:
    print(f"  ERR: {e}")

# 7. cadence
print("\n== CADENCE ==")
try:
    out = sh(["grep", "next full cycle in", f"{LOG}/trader.log"])
    secs = [int(s) for s in re.findall(r"next full cycle in (\d+)s", out)][-3:]
    if secs:
        print(f"  trader.cycle_interval: last3 = {secs}s")
except Exception:
    pass
try:
    out = sh(["grep", "stats: up=", f"{LOG}/bybit-daemon.log"])
    lines = [l for l in out.splitlines() if "stats: up=" in l]
    if len(lines) >= 2:
        ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
        a = datetime.strptime(ts_re.match(lines[-2]).group(1), "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(ts_re.match(lines[-1]).group(1), "%Y-%m-%d %H:%M:%S")
        print(f"  daemon.heartbeat: ~{(b-a).total_seconds():.0f}s")
except Exception:
    pass
try:
    starts = sh(["grep", "-c", "full cycle starting", f"{LOG}/trader.log"]).strip()
    print(f"  trader.cycles_logged: {starts}")
except Exception:
    pass

# 8. log tails (compact)
print("\n== LOG TAILS ==")
for path in (f"{LOG}/bybit-daemon.log", f"{LOG}/trader.log", f"{LOG}/trade-nl-publisher.log"):
    name = os.path.basename(path).replace(".log", "")
    print(f"  --{name}--")
    out = sh(["tail", "-2", path])
    for line in out.splitlines():
        print(f"  {line[:130]}")
