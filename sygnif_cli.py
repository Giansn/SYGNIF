#!/usr/bin/env python3
"""
Sygnif Terminal CLI
Usage:
  python3 sygnif_cli.py            # live dashboard
  python3 sygnif_cli.py health     # parallel HTTP probes (NL / FT / overseer / bee)
  python3 sygnif_cli.py status     # one-shot status panels
  python3 sygnif_cli.py chat       # [sygnif]> REPL → NeuroLinked /api/chat (alias: network)
  python3 sygnif_cli.py ide        # launch ``cursor agent`` in repo (Cursor desktop)
  python3 sygnif_cli.py swarm      # live swarm feed
  python3 sygnif_cli.py trades     # open trades
  python3 sygnif_cli.py brain      # neurolinked stats
  python3 sygnif_cli.py market     # market data
  python3 sygnif_cli.py logs       # swarm log tail
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Repo root for data paths: full SYGNIF tree, or ``sygnif-swarm/BTC_Prediction`` bundle slice.
_ROOT = Path(__file__).resolve().parent
_BUNDLE = _ROOT / "BTC_Prediction"
if _BUNDLE.is_dir() and (_BUNDLE / "prediction_agent").is_dir():
    _REPO = _BUNDLE
else:
    _REPO = _ROOT

# ── rich imports ────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    from rich.columns import Columns
except ImportError:
    print(
        "Sygnif CLI needs the 'rich' package. Install with:\n"
        "  pip install 'rich>=13.9'\n"
        "or: pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

console = Console()

# ── helpers ─────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 4) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sygnif-cli/1.0"})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return {}


def _nl_timeout(default: float = 12.0) -> float:
    raw = (os.environ.get("SYGNIF_CLI_NL_TIMEOUT") or "").strip()
    if not raw:
        return default
    try:
        return max(2.0, min(120.0, float(raw)))
    except ValueError:
        return default


def _probe_http(url: str, *, timeout: float, json_body: bool = True) -> dict:
    """Return {ok, ms, detail, raw_len} for health table."""
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sygnif-cli/health"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        ms = (time.perf_counter() - t0) * 1000.0
        if not json_body and raw[:500].lstrip().lower().startswith((b"<!doctype", b"<html")):
            return {
                "ok": False,
                "ms": ms,
                "detail": "HTML response (wrong URL or UI instead of API /ping?)",
                "raw_len": len(raw),
            }
        if json_body and raw:
            try:
                data = json.loads(raw)
                hint = "keys=" + ",".join(list(data.keys())[:6])
                if len(data) > 6:
                    hint += "…"
            except json.JSONDecodeError:
                hint = f"non-json {len(raw)}B"
        elif raw:
            try:
                txt = raw.decode("utf-8", errors="replace").strip()[:48]
                hint = txt or f"{len(raw)}B"
            except Exception:
                hint = f"{len(raw)}B"
        else:
            hint = "empty"
        return {"ok": True, "ms": ms, "detail": hint, "raw_len": len(raw)}
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000.0
        err = str(e)
        if isinstance(e, urllib.error.HTTPError):
            err = f"HTTP {e.code}"
        return {"ok": False, "ms": ms, "detail": err[:120], "raw_len": 0}


def _jwt_token(base_url: str, user: str, password: str) -> str | None:
    try:
        data = json.dumps({"username": user, "password": password}).encode()
        req = urllib.request.Request(f"{base_url}/api/v1/token/login",
                                     data=data, headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=4).read())
        return r.get("access_token")
    except Exception:
        return None


def _ft_get(base_url: str, path: str, user: str = "freqtrader", pw: str = "") -> dict | list:
    if not pw:
        pw = os.environ.get("FREQTRADE_API_PASSWORD") or os.environ.get("API_PASSWORD") or ""
    token = _jwt_token(base_url, user, pw)
    if not token:
        return {}
    try:
        req = urllib.request.Request(f"{base_url}/api/v1{path}",
                                     headers={"Authorization": f"Bearer {token}"})
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except Exception:
        return {}


def _svc_status(name: str) -> str:
    try:
        r = subprocess.run(["systemctl", "is-active", name],
                           capture_output=True, text=True, timeout=2)
        s = r.stdout.strip()
        return "[green]●[/]" if s == "active" else "[red]●[/]"
    except Exception:
        return "[dim]?[/]"


def _docker_status(name: str) -> str:
    try:
        r = subprocess.run(["docker", "inspect", "--format", "{{.State.Status}}", name],
                           capture_output=True, text=True, timeout=3)
        s = r.stdout.strip()
        return "[green]●[/]" if s == "running" else "[red]●[/]"
    except Exception:
        return "[dim]?[/]"


def _load_env():
    for path in (_REPO / ".env", _REPO / "swarm_operator.env", Path.home() / "xrp_claude_bot" / ".env"):
        if path.exists():
            for line in path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    if k.strip() and k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")


_load_env()

# NeuroLinked HTTP (systemd ``sygnif-neurolinked`` defaults to :8889; :8888 is spot/BTC terminal).
NL_URL = (
    (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL") or os.environ.get("SYGNIF_NEUROLINKED_HTTP_URL") or "").strip().rstrip("/")
    or "http://127.0.0.1:8889"
)
FT_SPOT = "http://127.0.0.1:8080"
FT_FUT  = "http://127.0.0.1:8081"
OVERSEER = "http://127.0.0.1:8090"
BEE_URL = "http://127.0.0.1:1633"
WS_SNAP = _REPO / "user_data" / "bybit_ws_monitor_state.json"
SWARM_CHAN = _REPO / "prediction_agent" / "neurolinked_swarm_channel.json"
PREDICT_JSON = _REPO / "prediction_agent" / "btc_24h_movement_prediction.json"


def _venv_python() -> str:
    """Prefer ``.venv`` next to bundle (``BTC_Prediction``) or git root (``sygnif-swarm``)."""
    for base in (_REPO, _REPO.parent):
        cand = base / ".venv" / "bin" / "python3"
        if cand.is_file():
            return str(cand)
    return sys.executable


def _ide_cwd() -> str:
    """Cursor agent cwd: swarm git root when CLI lives one level above ``BTC_Prediction``."""
    if _REPO.name == "BTC_Prediction":
        return str(_REPO.parent)
    return str(_REPO)


# ── data fetchers ────────────────────────────────────────────────────────────

def get_nl(*, timeout: float | None = None) -> dict:
    """NeuroLinked ``GET /api/sygnif/summary`` (read-only). Timeout from ``SYGNIF_CLI_NL_TIMEOUT``.

    If summary is empty (timeout / connection error), falls back to ``GET /api/sygnif/ping``
    so ``sygnif network`` can still show reachability when the heavy summary path stalls.
    """
    t = int(_nl_timeout(12.0) if timeout is None else float(timeout))
    t = max(2, min(120, t))
    data = _get(f"{NL_URL}/api/sygnif/summary", timeout=t)
    if data:
        return data
    ping_to = max(2, min(8, t))
    ping = _get(f"{NL_URL}/api/sygnif/ping", timeout=ping_to)
    if ping.get("ok"):
        return {
            "step": ping.get("step", 0),
            "stage": ping.get("stage", "?"),
            "uptime": ping.get("uptime", 0.0),
            "performance": 0.0,
            "surprise": 0.0,
            "learning_rate": 0.0,
            "arousal": 0.0,
            "attention_level": 0.0,
            "memories_stored": 0,
            "top_active_regions": [],
            "partial_from_ping": True,
        }
    return {}


def get_ws_snap() -> dict:
    try:
        return json.loads(WS_SNAP.read_text()) if WS_SNAP.exists() else {}
    except Exception:
        return {}


def get_swarm_chan() -> dict:
    try:
        return json.loads(SWARM_CHAN.read_text()) if SWARM_CHAN.exists() else {}
    except Exception:
        return {}


def get_predict() -> dict:
    try:
        return json.loads(PREDICT_JSON.read_text()) if PREDICT_JSON.exists() else {}
    except Exception:
        return {}


def get_overseer() -> dict:
    return _get(f"{OVERSEER}/trades")


def get_bee() -> dict:
    h = _get(f"{BEE_URL}/health", timeout=3)
    t = _get(f"{BEE_URL}/topology", timeout=3)
    return {**h, "peers": t.get("connected", 0), "population": t.get("population", 0),
            "depth": t.get("depth", 0)}


def get_ft_trades(spot: bool = True) -> list:
    base = FT_SPOT if spot else FT_FUT
    r = _ft_get(base, "/status")
    return r if isinstance(r, list) else []


def get_ft_profit(spot: bool = True) -> dict:
    """Freqtrade lifetime / closed aggregates (same fields as dashboard /profit)."""
    base = FT_SPOT if spot else FT_FUT
    r = _ft_get(base, "/profit")
    return r if isinstance(r, dict) else {}


def _ft_trades_payload_rows(raw: object) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("trades") or raw.get("data") or []
    return []


def get_ft_recent_closed(spot: bool, max_closed: int = 20) -> list[dict]:
    """Recent closed trades from /trades (newest first, best-effort)."""
    base = FT_SPOT if spot else FT_FUT
    raw = _ft_get(base, "/trades?limit=500")
    rows = _ft_trades_payload_rows(raw)
    closed = [t for t in rows if not t.get("is_open", True)]
    if not closed:
        return []

    def _tid(t: dict) -> int:
        tid = t.get("trade_id")
        try:
            return int(tid)
        except (TypeError, ValueError):
            return 0

    closed.sort(key=_tid, reverse=True)
    return closed[:max_closed]


def _trade_close_abs_pnl(t: dict) -> float:
    for k in ("close_profit_abs", "realized_profit", "profit_abs"):
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _trade_pnl_ratio(t: dict) -> float:
    for k in ("profit_ratio", "close_profit"):
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _format_recent_closed_brief(closed: list[dict], label: str) -> str:
    if not closed:
        return ""
    n = len(closed)
    wins = sum(1 for t in closed if _trade_pnl_ratio(t) > 0)
    net = sum(_trade_close_abs_pnl(t) for t in closed)
    wr = 100.0 * wins / n if n else 0.0
    tags = [str(t.get("enter_tag") or "?")[:12] for t in closed[:5]]
    tag_s = ",".join(tags)
    return (
        f"{label}_recent{n}: net={net:.2f}USDT WR={wr:.0f}%({wins}W/{n - wins}L) "
        f"enter_tags[0:5]={tag_s}"
    )


def execution_behavior_brief() -> str:
    """
    Compact spot + futures execution stats for NeuroLinked chat context and /network-style replies.
    Uses Freqtrade /profit (lifetime) and /trades (last closed window).
    """
    parts: list[str] = []
    for label, spot in (("spot", True), ("fut", False)):
        p = get_ft_profit(spot=spot)
        open_n = len(get_ft_trades(spot=spot) or [])
        if p:
            wins = int(p.get("winning_trades") or 0)
            losses = int(p.get("losing_trades") or 0)
            denom = wins + losses
            closed_ct = int(p.get("closed_trade_count") or denom)
            pc = float(p.get("profit_closed_coin") or 0)
            pa = float(p.get("profit_all_coin") or 0)
            wr = (100.0 * wins / denom) if denom else 0.0
            parts.append(
                f"{label}:lifetime_closed_pnl={pc:.2f}USDT lifetime_all_pnl={pa:.2f}USDT "
                f"closed_n={closed_ct} WR={wr:.0f}%({wins}W/{losses}L) open_n={open_n}"
            )
        else:
            parts.append(f"{label}:FT_offline_or_auth_fail open_n={open_n}")
        recent = get_ft_recent_closed(spot, max_closed=20)
        rb = _format_recent_closed_brief(recent, label)
        if rb:
            parts.append(rb)
    return " || ".join(parts)


def get_bybit_ticker(symbol: str = "BTCUSDT") -> dict:
    r = _get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}", timeout=5)
    items = r.get("result", {}).get("list", [])
    return items[0] if items else {}


# ── panel builders ───────────────────────────────────────────────────────────

def panel_services() -> Panel:
    t = Table(box=None, padding=(0, 1), show_header=False)
    t.add_column(no_wrap=True)
    t.add_column(no_wrap=True)
    t.add_column(no_wrap=True)

    services = [
        ("freqtrade",        "spot",            _docker_status("freqtrade")),
        ("freqtrade-futures","futures",          _docker_status("freqtrade-futures")),
        ("notification",     "notif-handler",   _docker_status("notification-handler")),
        ("trade-overseer",   "overseer",        _docker_status("trade-overseer")),
        ("neurolinked",      f"brain {urlparse(NL_URL).netloc}", _svc_status("sygnif-neurolinked")),
        ("swarm-loop",       "predict-loop",    _svc_status("sygnif-swarm-predict-loop")),
        ("bybit-stream",     "WS monitor",      _svc_status("bybit-stream-monitor")),
        ("nl-market-feed",   "mkt→brain",       _svc_status("sygnif-bybit-nl-feed")),
        ("finance-agent",    "agent :8091",     _docker_status("finance-agent")),
        ("bee-light",        "swarm bee",       _svc_status("bee-light")),
    ]
    for svc, label, dot in services:
        t.add_row(dot, f"[cyan]{label}[/]", f"[dim]{svc}[/]")

    return Panel(t, title="[bold]Services[/]", border_style="blue", padding=(0, 1))


def panel_brain(cached_nl: dict | None = None) -> Panel:
    nl = cached_nl if cached_nl is not None else get_nl()
    if not nl:
        return Panel("[red]NeuroLinked offline[/]", title="[bold]Brain[/]", border_style="magenta")
    if nl.get("error"):
        return Panel(
            f"[yellow]NeuroLinked API:[/] {nl.get('error')!s}",
            title="[bold]Brain[/]",
            border_style="magenta",
        )

    stage = nl.get("stage", "?")
    hz = nl.get("performance", 0)
    step = nl.get("step", 0)
    surprise = nl.get("surprise", 0)
    learning = nl.get("learning_rate", 0)
    arousal = nl.get("arousal", 0)
    attention = nl.get("attention_level", 0)
    memories = nl.get("memories_stored", 0)

    stage_color = {"EMBRYONIC": "yellow", "JUVENILE": "cyan",
                   "ADOLESCENT": "green", "MATURE": "bold green"}.get(stage, "white")

    lines = Text()
    lines.append(f"Stage  ", style="dim")
    lines.append(f"{stage}\n", style=stage_color)
    lines.append(f"Hz     ", style="dim")
    lines.append(f"{hz:.0f}\n", style="green" if hz > 200 else "yellow" if hz > 100 else "red")
    lines.append(f"Steps  {step:,}\n", style="dim")
    lines.append(f"Surprise  {surprise:.3f}  ", style="dim")
    lines.append(f"Arousal {arousal:.3f}\n", style="dim")
    lines.append(f"Learn  {learning:.3f}  ", style="dim")
    lines.append(f"Attn   {attention:.3f}\n", style="dim")
    lines.append(f"Memories  {memories}\n", style="dim")

    top = nl.get("top_active_regions", [])
    if top:
        lines.append("Active  ", style="dim")
        lines.append(" ".join(r["name"][:6] for r in top[:3]), style="cyan")

    if nl.get("degraded") or nl.get("partial_from_ping"):
        lines.append("\n", style="dim")
        lines.append(
            "(partial: summary unavailable or timed out; ping/degraded snapshot)\n",
            style="dim",
        )

    return Panel(lines, title="[bold magenta]NeuroLinked[/]", border_style="magenta", padding=(0, 1))


def panel_market() -> Panel:
    ws = get_ws_snap()
    bid = ws.get("best_bid")
    ask = ws.get("best_ask")
    updated = ws.get("updated_utc", "?")

    t = get_bybit_ticker("BTCUSDT")
    pct = float(t.get("price24hPcnt", 0)) * 100
    funding = float(t.get("fundingRate", 0)) * 100
    oi = float(t.get("openInterestValue", 0)) / 1e6

    t2 = get_bybit_ticker("ETHUSDT")
    pct_eth = float(t2.get("price24hPcnt", 0)) * 100
    t3 = get_bybit_ticker("SOLUSDT")
    pct_sol = float(t3.get("price24hPcnt", 0)) * 100

    def pct_color(p: float) -> str:
        return "green" if p > 0 else "red"

    lines = Text()
    if bid is not None and ask is not None:
        try:
            mid = (float(bid) + float(ask)) / 2.0
        except (TypeError, ValueError):
            mid = None
        if mid is not None:
            lines.append(f"BTC  ", style="dim")
            lines.append(f"${mid:,.1f}  ", style="bold white")
            lines.append(f"{pct:+.2f}%\n", style=pct_color(pct))
    lines.append(f"ETH  {pct_eth:+.2f}%   SOL  {pct_sol:+.2f}%\n",
                 style="dim")
    lines.append(f"Funding  ", style="dim")
    lines.append(f"{funding:+.4f}%  ", style="green" if funding < 0 else "red")
    lines.append(f"OI  {oi:.0f}M\n", style="dim")
    lines.append(f"Updated  {updated}", style="dim")

    return Panel(lines, title="[bold]Market[/]", border_style="yellow", padding=(0, 1))


def panel_swarm() -> Panel:
    sc = get_swarm_chan()
    pred = get_predict()

    if not sc:
        return Panel("[dim]No swarm data yet[/]", title="[bold]Swarm[/]", border_style="cyan")

    label = sc.get("swarm_label", "?")
    mean = sc.get("swarm_mean", 0)
    conflict = sc.get("swarm_conflict", False)
    sources = sc.get("sources_n", 0)
    loop = sc.get("extra", {}).get("predict_loop", {})
    side = loop.get("target_side", "?")
    allow = loop.get("allow_buy", False)
    gate_ok = loop.get("swarm_gate_ok", False)
    edge = loop.get("move_pct", 0)
    reason = loop.get("swarm_reason", loop.get("target_reason", ""))[:50]
    ts = loop.get("ts_utc", "")

    label_style = "green" if mean > 0 else "red" if mean < 0 else "yellow"
    side_style = "green" if side == "long" else "red" if side == "short" else "dim"

    lines = Text()
    lines.append(f"Label   ", style="dim")
    lines.append(f"{label}\n", style=label_style)
    lines.append(f"Mean    {mean:+.3f}   Sources  {sources}\n", style="dim")
    lines.append(f"Target  ", style="dim")
    lines.append(f"{side}  ", style=side_style)
    lines.append(f"Edge  {edge:.3f}%\n", style="dim")
    lines.append(f"Gate    ", style="dim")
    lines.append(f"{'✓ OPEN' if gate_ok else '✗ BLOCKED'}  ", style="green" if gate_ok else "red")
    lines.append(f"Allow  ", style="dim")
    lines.append(f"{'✓' if allow else '✗'}\n", style="green" if allow else "red")
    if conflict:
        lines.append("⚠ Conflict\n", style="yellow")
    lines.append(f"Reason  {reason}\n", style="dim")
    if ts:
        lines.append(f"Updated {ts}", style="dim")

    return Panel(lines, title="[bold cyan]Swarm[/]", border_style="cyan", padding=(0, 1))


def panel_trades() -> Panel:
    trades = get_ft_trades(spot=False)  # futures

    if not trades:
        return Panel("[dim]No open trades[/]", title="[bold]Trades[/]", border_style="green")

    t = Table(box=box.SIMPLE, padding=(0, 1), show_header=True, header_style="bold dim")
    t.add_column("Pair", no_wrap=True)
    t.add_column("Side", no_wrap=True)
    t.add_column("P&L%", justify="right")
    t.add_column("Dur", justify="right")
    t.add_column("Tag", no_wrap=True)

    for tr in sorted(trades, key=lambda x: x.get("profit_pct", 0)):
        pnl = tr.get("profit_pct", 0) * 100
        pnl_style = "green" if pnl > 0 else "red"
        dur = tr.get("trade_duration", 0)
        h, m = divmod(int(dur) // 60, 60)
        side = "S" if tr.get("is_short") else "L"
        side_style = "red" if tr.get("is_short") else "green"
        tag = (tr.get("open_order_id") or tr.get("enter_tag") or "")[:12]
        t.add_row(
            tr.get("pair", "?")[:12],
            f"[{side_style}]{side}[/]",
            f"[{pnl_style}]{pnl:+.2f}%[/]",
            f"{h}h{m:02d}m",
            f"[dim]{tag}[/]",
        )

    return Panel(t, title=f"[bold]Trades ({len(trades)})[/]", border_style="green", padding=(0, 1))


def panel_bee() -> Panel:
    b = get_bee()
    status = b.get("status", "?")
    ok = status == "ok"
    peers = b.get("peers", 0)
    pop = b.get("population", 0)
    depth = b.get("depth", 0)
    ver = b.get("version", "?")

    lines = Text()
    lines.append("Status  ", style="dim")
    lines.append(f"{'ok' if ok else status}\n", style="green" if ok else "red")
    lines.append(f"Peers   {peers}   Pop  {pop}\n", style="dim")
    lines.append(f"Depth   {depth}   ver  {ver}", style="dim")

    return Panel(lines, title="[bold]Swarm Bee[/]", border_style="blue", padding=(0, 1))


# ── views ────────────────────────────────────────────────────────────────────

def view_dashboard():
    def build() -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="top"),
            Layout(name="mid"),
            Layout(name="bottom"),
        )
        layout["top"].split_row(Layout(name="services"), Layout(name="brain"))
        layout["mid"].split_row(Layout(name="market"), Layout(name="swarm"))
        layout["bottom"].split_row(Layout(name="trades"), Layout(name="bee"))

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        layout["header"].update(
            Panel(f"[bold cyan]SYGNIF[/] [dim]trading terminal[/]  [dim]{now}[/]  "
                  "[dim]q=quit  r=refresh[/]", border_style="dim")
        )
        layout["services"].update(panel_services())
        layout["brain"].update(panel_brain())
        layout["market"].update(panel_market())
        layout["swarm"].update(panel_swarm())
        layout["trades"].update(panel_trades())
        layout["bee"].update(panel_bee())
        return layout

    with Live(build(), refresh_per_second=0.5, screen=True) as live:
        try:
            while True:
                time.sleep(3)
                live.update(build())
        except KeyboardInterrupt:
            pass


def view_status():
    console.print(panel_services())
    # One summary fetch: avoids stacking NL timeout behind slow market HTTP during ``status``.
    _nl_snap = get_nl()
    console.print(Columns([panel_brain(_nl_snap), panel_market()]))
    console.print(Columns([panel_swarm(), panel_bee()]))
    console.print(panel_trades())


def view_swarm():
    console.print("[bold cyan]Swarm live feed[/] — [dim]Ctrl+C to stop[/]")
    seen = ""
    try:
        while True:
            sc = get_swarm_chan()
            loop = sc.get("extra", {}).get("predict_loop", {})
            ts = loop.get("ts_utc", "")
            if ts != seen:
                seen = ts
                side = loop.get("target_side", "?")
                gate = loop.get("swarm_gate_ok", False)
                allow = loop.get("allow_buy", False)
                edge = loop.get("move_pct", 0)
                reason = loop.get("target_reason", loop.get("swarm_reason", ""))[:60]
                label = sc.get("swarm_label", "?")
                mean = sc.get("swarm_mean", 0)
                color = "green" if mean > 0 else "red" if mean < 0 else "yellow"
                console.print(
                    f"[dim]{ts}[/]  [{color}]{label}[/]  "
                    f"side=[{'green' if side=='long' else 'red'}]{side}[/]  "
                    f"edge={edge:.3f}%  gate={'[green]✓[/]' if gate else '[red]✗[/]'}  "
                    f"allow={'[green]✓[/]' if allow else '[red]✗[/]'}\n"
                    f"  [dim]{reason}[/]"
                )
            time.sleep(5)
    except KeyboardInterrupt:
        pass


def view_trades():
    console.print(panel_trades())


def view_brain():
    console.print(panel_brain())
    nl = _get(f"{NL_URL}/api/claude/insights")
    if nl.get("insights"):
        console.print(Panel(
            "\n".join(f"[yellow]•[/] {i.get('message','')}" for i in nl["insights"][:8]),
            title="Brain Insights", border_style="magenta"
        ))


def view_market():
    console.print(panel_market())
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]:
        t = get_bybit_ticker(sym)
        if t:
            pct = float(t.get("price24hPcnt", 0)) * 100
            fund = float(t.get("fundingRate", 0)) * 100
            oi = float(t.get("openInterestValue", 0)) / 1e6
            price = t.get("lastPrice", "?")
            color = "green" if pct > 0 else "red"
            console.print(
                f"  [bold]{sym:<12}[/] ${price:>10}  [{color}]{pct:+.2f}%[/]  "
                f"fund=[{'green' if fund<0 else 'red'}]{fund:+.4f}%[/]  OI={oi:.0f}M"
            )


def view_logs():
    console.print("[bold]Swarm predict log[/] — [dim]Ctrl+C to stop[/]")
    proc = subprocess.Popen(
        ["journalctl", "-fu", "sygnif-swarm-predict-loop", "--no-pager"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    try:
        for line in proc.stdout:
            if "SYGNIF_LOOP_PREDICT" in line:
                try:
                    data = json.loads(line.split("SYGNIF_LOOP_PREDICT ")[1])
                    side = data.get("target_side", "?")
                    gate = data.get("swarm_gate_ok", False)
                    edge = data.get("move_pct", 0)
                    ts = data.get("ts_utc", "")
                    color = "green" if side == "long" else "red"
                    console.print(f"[dim]{ts}[/]  [{color}]{side:5}[/]  edge={edge:.3f}%  "
                                  f"gate={'[green]✓[/]' if gate else '[red]✗[/]'}")
                except Exception:
                    pass
            elif "SYGNIF_LOOP_SWARM_BLOCK" in line:
                try:
                    data = json.loads(line.split("SYGNIF_LOOP_SWARM_BLOCK ")[1])
                    console.print(f"  [red]BLOCK[/] {data.get('reason','')}")
                except Exception:
                    pass
    except KeyboardInterrupt:
        proc.terminate()


def view_health():
    """Parallel HTTP probes — faster than ``status`` when NeuroLinked is slow to answer."""
    raw_h = (os.environ.get("SYGNIF_CLI_HEALTH_NL_TIMEOUT") or "").strip()
    try:
        nl_health = max(3.0, min(30.0, float(raw_h))) if raw_h else min(8.0, _nl_timeout(12.0))
    except ValueError:
        nl_health = min(8.0, _nl_timeout(12.0))
    jobs = {
        # Ping must answer without ``to_thread``; allow >2s when the sim thread is GIL-heavy.
        "NeuroLinked ping": (f"{NL_URL}/api/sygnif/ping", min(5.0, nl_health), True),
        "NeuroLinked summary": (f"{NL_URL}/api/sygnif/summary", nl_health, True),
        "NeuroLinked /api/state": (f"{NL_URL}/api/state", min(5.0, nl_health), True),
        "Freqtrade spot /ping": (f"{FT_SPOT.rstrip('/')}/ping", 2.5, False),
        "Freqtrade fut /ping": (f"{FT_FUT.rstrip('/')}/ping", 2.5, False),
        "Trade overseer": (f"{OVERSEER.rstrip('/')}/overview", 3.0, True),
        "Swarm Bee /health": (f"{BEE_URL.rstrip('/')}/health", 2.5, True),
    }
    results: dict[str, dict] = {}

    def _run(name: str, spec: tuple) -> tuple[str, dict]:
        url, timeout, as_json = spec
        return name, _probe_http(url, timeout=timeout, json_body=as_json)

    with ThreadPoolExecutor(max_workers=min(12, len(jobs) + 3)) as ex:
        futs = [ex.submit(_run, n, s) for n, s in jobs.items()]
        for fut in as_completed(futs):
            name, res = fut.result()
            results[name] = res

    tab = Table(title="[bold]Sygnif health (HTTP)[/]", box=box.ROUNDED)
    tab.add_column("Service", style="cyan")
    tab.add_column("OK", justify="center")
    tab.add_column("ms", justify="right")
    tab.add_column("Detail", style="dim")

    order = list(jobs.keys())
    for name in order:
        r = results.get(name, {})
        ok = bool(r.get("ok"))
        tab.add_row(
            name,
            "[green]yes[/]" if ok else "[red]no[/]",
            f"{float(r.get('ms', 0)):.0f}",
            str(r.get("detail", ""))[:70],
        )
    console.print(tab)

    console.print(
        Panel(
            "[dim]NeuroLinked: ``/api/sygnif/ping`` is loop-only; summary/state use ``asyncio.to_thread``. "
            "If ping is ok but summary times out, lower ``SYGNIF_NEUROLINKED_SIM_TARGET_HZ`` or raise "
            "``SYGNIF_NEUROLINKED_POST_STEP_YIELD_SEC`` (server.py). "
            "Set SYGNIF_NEUROLINKED_HOST_URL if the brain listens elsewhere; "
            "SYGNIF_CLI_NL_TIMEOUT raises the summary client timeout (default 12s); "
            "SYGNIF_CLI_HEALTH_NL_TIMEOUT caps NeuroLinked probe time for sygnif health (default min(8s, NL timeout)).[/]",
            title="Hints",
            border_style="dim",
        )
    )
    console.print(panel_services())


def _ssh_like_session() -> bool:
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))


def view_ide():
    """Start Cursor Agent in this repo (desktop with Cursor installed — not headless servers)."""
    exe = shutil.which("cursor") or (os.environ.get("CURSOR_CLI") or "").strip()
    if not exe:
        console.print(
            "[yellow]cursor[/] CLI not on PATH. Install Cursor from [link=https://cursor.com/download]cursor.com/download[/] "
            "and ensure the shell command is registered."
        )
        return

    if _ssh_like_session():
        console.print(
            Panel(
                "[yellow]You are in an SSH session.[/] [bold]sygnif ide[/] does **not** open a chat in this shell — "
                "it only tries to spawn Cursor Agent in the background. On EC2 that usually does nothing useful "
                "without the Cursor **desktop** app on the same host.\n\n"
                "• Terminal chat on this machine: [bold cyan]sygnif chat[/]  (then type; prompt is [sygnif]>)\n"
                "• Cursor UI: open the repo on your laptop in Cursor, or use Cursor’s Remote-SSH to this host.\n\n"
                "[dim]Still launching as requested…[/]",
                title="SSH",
                border_style="yellow",
            )
        )

    starter = (
        "Sygnif: read CLAUDE.md at the repo root. You help with Freqtrade, swarm_knowledge, "
        "NeuroLinked, and trading ops. Start with a one-line health summary then ask what to dig into."
    )
    try:
        subprocess.Popen(
            [exe, "agent", starter],
            cwd=_ide_cwd(),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        console.print(f"[red]Could not launch Cursor:[/] {e}")
        return
    console.print(
        f"[green]Launched[/] [cyan]{exe} agent[/] in [bold]{_ide_cwd()}[/] (detached). "
        "You stay at the normal bash prompt — there is no interactive Cursor session here.\n"
        "[dim]If the agent exits immediately: install Cursor Desktop on this machine, or use [cyan]sygnif chat[/].[/]"
    )


# ── main ─────────────────────────────────────────────────────────────────────

def _nl_send(text: str) -> bool:
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


def _build_context() -> str:
    """Legacy compact context (unused by ``/api/chat`` — NeuroLinked builds context server-side)."""
    sc = get_swarm_chan() or {}
    ws = get_ws_snap()
    pred = get_predict()
    loop = sc.get("extra", {}).get("predict_loop", {}) if sc else {}
    lines = []

    bid, ask = ws.get("best_bid"), ws.get("best_ask")
    if bid and ask:
        try:
            lines.append(f"BTC={((float(bid)+float(ask))/2):,.0f}")
        except Exception:
            pass

    if sc:
        side = loop.get("target_side", "?")
        edge = loop.get("move_pct", 0)
        gate = "OPEN" if loop.get("swarm_gate_ok") else "BLOCKED"
        allow = loop.get("allow_buy", False)
        hm_vote = loop.get("predict_hivemind_vote", loop.get("hm_vote", "?"))
        hm_note = loop.get("predict_hivemind_note", loop.get("hm_detail", ""))
        hm_engine = loop.get("swarm_core_engine", "?")
        lines.append(
            f"swarm={sc.get('swarm_label','?')} side={side} edge={float(edge):.2f}% gate={gate} allow={allow} "
            f"hivemind_vote={hm_vote} hm_engine={hm_engine} hm_note={hm_note}"
        )
        reason = (loop.get("target_reason") or "")[:100]
        if reason:
            lines.append(f"reason={reason} enhanced={loop.get('enhanced','?')}")

    if pred:
        syn = pred.get("synthesis") or {}
        run = pred.get("runner_snapshot") or {}
        lines.append(
            f"prediction bias={syn.get('bias_24h','?')} p_up={float(syn.get('p_up_blended',0)):.2f} "
            f"runner={run.get('consensus','?')}/{run.get('direction_label','?')} conf={run.get('direction_confidence_pct','?')}%"
        )

    for spot_tf, tag in ((True, "spot"), (False, "fut")):
        trades = get_ft_trades(spot=spot_tf) or []
        if trades:
            parts = []
            for t in trades[:3]:
                pair = t.get("pair", "?")
                pr = t.get("profit_ratio", t.get("profit_pct", 0))
                try:
                    pct = float(pr) * 100.0 if abs(float(pr)) <= 1.0 else float(pr)
                except (TypeError, ValueError):
                    pct = 0.0
                side = "S" if t.get("is_short") else "L"
                parts.append(f"{pair}:{side}{pct:+.1f}%")
            lines.append(f"open_{tag}=[{', '.join(parts)}]")

    beh = execution_behavior_brief()
    if beh:
        lines.append(f"execution_behavior={beh}")

    return " | ".join(lines)


def _nl_chat(text: str) -> dict:
    """POST ``/api/chat`` — context is assembled server-side (``swarm_knowledge`` + Bybit + channel)."""
    try:
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            f"{NL_URL}/api/chat", data=data,
            headers={"Content-Type": "application/json"}
        )
        # ``/api/chat`` can wait on Bybit + Haiku; keep below typical reverse-proxy idle limits.
        resp = urllib.request.urlopen(req, timeout=75)
        return json.loads(resp.read())
    except Exception as e:
        err = str(e)
        if isinstance(e, urllib.error.HTTPError):
            try:
                err = f"HTTP {e.code}: {(e.read() or b'')[:200]!r}"
            except Exception:
                err = f"HTTP {e.code}"
        return {"error": err, "response": "", "state": {}}


def _network_reply(user_msg: str) -> str:
    """Synthesize a reply from live Sygnif services only (no cloud LLM API)."""
    nl = get_nl()
    sc = get_swarm_chan() or {}
    ws = get_ws_snap()
    bee = get_bee()
    loop = sc.get("extra", {}).get("predict_loop", {}) if sc else {}
    pred = get_predict()

    lines: list[str] = []
    q = user_msg.strip().lower()

    # --- NeuroLinked ---
    if not nl:
        lines.append(f"NeuroLinked: offline or unreachable (GET {NL_URL}/api/sygnif/summary).")
    else:
        top = ", ".join(
            f"{r.get('name', '?')} ({float(r.get('activity', 0)):.0f}%)"
            for r in nl.get("top_active_regions", [])[:4]
        )
        lines.append(
            f"NeuroLinked: stage={nl.get('stage', '?')} step={int(nl.get('step', 0)):,} "
            f"sim_Hz={float(nl.get('performance', 0)):.0f} surprise={float(nl.get('surprise', 0)):.3f} "
            f"attn={float(nl.get('attention_level', 0)):.3f} learn={float(nl.get('learning_rate', 0)):.3f} "
            f"memories={int(nl.get('memories_stored', 0))}"
        )
        if top:
            lines.append(f"  Active regions: {top}")

    # --- Swarm channel + predict loop ---
    if not sc:
        lines.append("Swarm channel: no neurolinked_swarm_channel.json data.")
    else:
        mean = float(sc.get("swarm_mean", 0))
        conflict = bool(sc.get("swarm_conflict"))
        ctag = "conflict " if conflict else ""
        lines.append(
            f"Swarm: label={sc.get('swarm_label', '?')} mean={mean:+.3f} {ctag}"
            f"sources_n={int(sc.get('sources_n', 0))}"
        )
        side = loop.get("target_side", "?")
        gate_ok = bool(loop.get("swarm_gate_ok"))
        allow = bool(loop.get("allow_buy"))
        try:
            edge = float(loop.get("move_pct", 0))
        except (TypeError, ValueError):
            edge = 0.0
        reason = (loop.get("target_reason") or loop.get("swarm_reason") or "")[:140]
        lines.append(
            f"  Loop: target={side} edge={edge:.3f}% gate={'OPEN' if gate_ok else 'BLOCKED'} "
            f"allow_buy={'yes' if allow else 'no'}"
        )
        if reason:
            lines.append(f"  Reason: {reason}")
        if loop.get("ts_utc"):
            lines.append(f"  Loop ts: {loop.get('ts_utc')}")

    # --- Market snapshot + prediction JSON ---
    bid, ask = ws.get("best_bid"), ws.get("best_ask")
    if bid is not None and ask is not None:
        try:
            mid = (float(bid) + float(ask)) / 2
            lines.append(
                f"Market (WS monitor): BTC mid ~ {mid:,.1f}  updated={ws.get('updated_utc', '?')}"
            )
        except (TypeError, ValueError):
            lines.append(f"Market (WS monitor): bid/ask present but not numeric; updated={ws.get('updated_utc', '?')}")
    else:
        lines.append("Market (WS monitor): no bid/ask in bybit_ws_monitor_state.json.")

    if pred:
        spot = pred.get("spot_usd", "?")
        syn = pred.get("synthesis") or {}
        run = pred.get("runner_snapshot") or {}
        try:
            pup = float(syn.get("p_up_blended", 0))
        except (TypeError, ValueError):
            pup = 0.0
        conf = run.get("direction_confidence_pct", "?")
        lines.append(
            f"24h prediction file: spot={spot} bias={syn.get('bias_24h', '?')} "
            f"p_up_blended={pup:.3f} runner={run.get('consensus', '?')}/"
            f"{run.get('direction_label', '?')} (conf {conf}%)"
        )
        if pred.get("generated_utc"):
            lines.append(f"  generated_utc: {pred.get('generated_utc')}")

    beh = execution_behavior_brief()
    if beh:
        lines.append(f"Freqtrade execution (spot+fut): {beh}")

    # --- Bee (Ethereum Swarm light node) ---
    bstatus = bee.get("status", "?")
    bok = bstatus == "ok"
    lines.append(
        f"Bee (Swarm): status={bstatus} peers={int(bee.get('peers', 0))} "
        f"population={int(bee.get('population', 0))} depth={bee.get('depth', '?')} "
        f"({'reachable' if bok else 'check health'})"
    )

    # Optional: emphasize sections if the user named them
    focus: list[str] = []
    if any(k in q for k in ("brain", "neuro", "linked", "hippocampus", "memory")):
        focus.append("focus=brain")
    if any(k in q for k in ("swarm", "gate", "edge", "signal", "loop", "blocked", "allow")):
        focus.append("focus=swarm")
    if any(k in q for k in ("market", "btc", "price", "funding", "oi")):
        focus.append("focus=market")
    if "bee" in q or "swarm node" in q:
        focus.append("focus=bee")
    if focus:
        lines.append("")
        lines.append("Note: " + ", ".join(focus) + " — full snapshot above.")

    lines.append("")
    lines.append("(No LLM: text assembled from NeuroLinked, swarm channel, WS snapshot, prediction JSON, Bee.)")
    return "\n".join(lines)


def view_chat():
    """Interactive chat with the SYGNIF network — real input + LLM responses."""
    import readline  # enables arrow keys + history in input()

    console.print(Panel(
        "[bold cyan]SYGNIF Network Chat[/]\n"
        "[dim]Autonomous third-party network interface. Swarm channel, NeuroLinked brain, WS snapshot, "
        "prediction JSON, Bee — operator and network are both external observers.\n"
        "Commands: /swarm  /brain  /market  /trades  /status  /quit[/]",
        border_style="cyan"
    ))

    history: list[str] = []

    def _handle(text: str):
        text = text.strip()
        if not text:
            return
        cmd = text.lower()

        if cmd in ("/quit", "/q", "quit", "exit"):
            raise KeyboardInterrupt
        elif cmd == "/swarm":
            console.print(panel_swarm())
        elif cmd == "/brain":
            console.print(panel_brain())
        elif cmd == "/market":
            console.print(panel_market())
        elif cmd == "/trades":
            console.print(panel_trades())
        elif cmd == "/bee":
            console.print(panel_bee())
        elif cmd == "/status":
            console.print(panel_services())
        elif cmd.startswith("/"):
            console.print(f"[red]Unknown: {cmd}[/]")
        else:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            console.print(f"\n[dim]{ts}[/] [bold green]you[/]  {text}")

            with console.status("[bold magenta]network…[/]", spinner="dots"):
                chat_result = _nl_chat(text)
            if chat_result.get("error"):
                console.print(
                    f"[dim]{ts}[/] [bold red]error[/]  [red]{chat_result.get('error')}[/]\n"
                    f"[dim]NeuroLinked URL: {NL_URL} — check systemd sygnif-neurolinked and "
                    f"SYGNIF_NEUROLINKED_HOST_URL[/]"
                )
                return
            decoded = chat_result.get("response", "")
            st = chat_result.get("state", {})
            surprise = st.get("surprise", 0)
            hz = 0.0

            nl = get_nl()
            if nl:
                hz = float(nl.get("performance", 0))
                if not surprise:
                    surprise = float(nl.get("surprise", 0))

            meta = f"[dim](surprise={surprise:.3f} hz={hz:.0f})[/]"
            console.print(
                f"[dim]{ts}[/] [bold magenta]network[/]  "
                f"[bold white]{decoded or '—'}[/]  {meta}"
            )
            history.append(text)

    try:
        while True:
            try:
                text = input("\n[sygnif]> ")
                _handle(text)
            except EOFError:
                break
    except KeyboardInterrupt:
        console.print("\n[dim]Network disconnected.[/]")


def view_train():
    import subprocess
    console.print("[bold cyan]BTC History Trainer[/] — feeding 2017→now into brain")
    subprocess.run([
        _venv_python(),
        str(_REPO / "scripts" / "neurolinked_btc_history_train.py"),
        "--fast"
    ])

COMMANDS = {
    "status":    view_status,
    "health":    view_health,
    "swarm":     view_swarm,
    "trades":    view_trades,
    "brain":     view_brain,
    "market":    view_market,
    "logs":      view_logs,
    "dashboard": view_dashboard,
    "chat":      view_chat,
    "network":   view_chat,
    "ide":       view_ide,
    "cursor":    view_ide,
    "train":     view_train,
}

HELP = """
[bold cyan]SYGNIF CLI[/]

  [bold]sygnif[/]              live dashboard (default)
  [bold]sygnif chat[/]         Interactive [cyan][sygnif]>[/] REPL → NeuroLinked [dim]/api/chat[/]
  [bold]sygnif network[/]      same as [bold]chat[/]
  [bold]sygnif ide[/]          [bold]cursor agent[/] in background (not a shell chat; on SSH use [cyan]sygnif chat[/])
  [bold]sygnif health[/]       parallel HTTP probes + service dots (quick NL timeout check)
  [bold]sygnif status[/]       one-shot system overview (rich panels)
  [bold]sygnif swarm[/]        live swarm signal feed
  [bold]sygnif trades[/]       open positions
  [bold]sygnif brain[/]        neurolinked stats + insights
  [bold]sygnif market[/]       bybit market data
  [bold]sygnif logs[/]         swarm predict log stream
"""


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dashboard"
    if cmd in ("-h", "--help", "help"):
        console.print(HELP)
    elif cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        console.print(f"[red]Unknown command: {cmd}[/]")
        console.print(HELP)


if __name__ == "__main__":
    main()
