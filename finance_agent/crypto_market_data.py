"""Daily BTC on-chain / derivatives context from ErcinDedeoglu/crypto-market-data (CC BY 4.0).

Not Sygnif TA, not Bybit OHLCV. Used by BTC specialist bundle, HTTP ``/briefing``, and
finance-agent KB references. Attribution required when surfacing to users — see
``ATTRIBUTION_MARKDOWN`` and https://github.com/ErcinDedeoglu/crypto-market-data
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

RAW_BASE = (
    "https://raw.githubusercontent.com/ErcinDedeoglu/crypto-market-data/main/data/daily"
)

# Curated: compact briefing pipe + 6h cache (fast).
DEFAULT_PATHS: tuple[str, ...] = (
    "btc_funding_rates.json",
    "btc_open_interest.json",
    "btc_taker_buy_sell_ratio.json",
    "btc_exchange_netflow.json",
    "btc_mvrv_ratio.json",
    "btc_coinbase_premium_index.json",
    "btc_long_liquidations_usd.json",
    "btc_short_liquidations_usd.json",
    "stablecoin_exchange_netflow.json",
)

# All `data/daily/*.json` datasets listed in upstream README (ErcinDedeoglu/crypto-market-data).
ALL_README_DAILY_PATHS: tuple[str, ...] = (
    "btc_exchange_netflow.json",
    "btc_exchange_reserve.json",
    "btc_exchange_reserve_usd.json",
    "btc_exchange_inflow_total.json",
    "btc_exchange_outflow_total.json",
    "btc_exchange_whale_ratio.json",
    "btc_exchange_stablecoins_ratio.json",
    "btc_exchange_stablecoins_ratio_usd.json",
    "stablecoin_exchange_netflow.json",
    "stablecoin_exchange_reserve.json",
    "stablecoin_exchange_inflow_total.json",
    "stablecoin_exchange_outflow_total.json",
    "btc_miners_position_index.json",
    "btc_miner_netflow_total.json",
    "btc_puell_multiple.json",
    "btc_funding_rates.json",
    "btc_open_interest.json",
    "btc_taker_buy_sell_ratio.json",
    "btc_long_liquidations.json",
    "btc_long_liquidations_usd.json",
    "btc_short_liquidations.json",
    "btc_short_liquidations_usd.json",
    "btc_mvrv_ratio.json",
    "btc_exchange_supply_ratio.json",
    "btc_fund_flow_ratio.json",
    "stablecoin_exchange_supply_ratio.json",
    "btc_coinbase_premium_index.json",
    "btc_coinbase_premium_gap.json",
    "btc_korea_premium_index.json",
)

ATTRIBUTION_MARKDOWN = (
    "_On-chain/derivatives daily:_ [Crypto Market Data](https://github.com/ErcinDedeoglu/crypto-market-data) "
    "(Ercin Dedeoglu, **CC BY 4.0**) — not Sygnif TA / not Bybit OHLC."
)

_BUNDLE_CACHE: tuple[float, dict] | None = None
_BUNDLE_TTL_SEC = 6 * 3600


def get_bundle_cached(*, ttl_sec: int | None = None) -> dict | None:
    """Single remote pull per TTL on success; failures are not cached (next call retries)."""
    global _BUNDLE_CACHE
    ttl = ttl_sec if ttl_sec is not None else _BUNDLE_TTL_SEC
    now = time.time()
    if _BUNDLE_CACHE is not None:
        ts, b = _BUNDLE_CACHE
        if now - ts < ttl:
            return b
    bundle = fetch_remote_bundle(paths=DEFAULT_PATHS, timeout_per=6.0)
    ds = bundle.get("datasets") if isinstance(bundle, dict) else None
    ok = isinstance(ds, dict) and any(v for v in ds.values() if v)
    if not ok:
        return None
    _BUNDLE_CACHE = (now, bundle)
    return bundle


def _http_json(url: str, *, timeout: float) -> dict | None:
    req = Request(url, headers={"User-Agent": "Sygnif-finance-agent/1"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (URLError, OSError, TimeoutError, ValueError) as e:
        logger.warning("crypto_market_data fetch failed %s: %s", url, e)
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("crypto_market_data JSON error %s: %s", url, e)
        return None


def _fmt_num(x: float | None) -> str:
    if x is None:
        return "?"
    ax = abs(x)
    if ax >= 1e9:
        return f"{x/1e9:.3g}B"
    if ax >= 1e6:
        return f"{x/1e6:.3g}M"
    if ax >= 1e3:
        return f"{x/1e3:.3g}k"
    if ax >= 1:
        return f"{x:.4g}"
    return f"{x:.4g}"


def _series_tail(
    doc: dict, *, lookback_points: int = 8
) -> tuple[str, float | None, float | None, str | None, int]:
    """Return (short name, latest value, older value for delta, data_type, bars_in_window)."""
    name = str(doc.get("name") or doc.get("description") or "?")[:72]
    dtype = doc.get("data_type")
    data = doc.get("data")
    if not isinstance(data, list) or not data:
        return name, None, None, str(dtype) if dtype else None, 0
    n = max(2, min(lookback_points, len(data)))
    tail = data[-n:]
    last = tail[-1]
    first = tail[0]
    try:
        v_last = float(last.get("value"))
    except (TypeError, ValueError):
        v_last = None
    try:
        v_old = float(first.get("value"))
    except (TypeError, ValueError):
        v_old = None
    return name, v_last, v_old, str(dtype) if dtype else None, len(tail)


def fetch_remote_bundle(
    *,
    paths: tuple[str, ...] = DEFAULT_PATHS,
    timeout_per: float = 7.0,
) -> dict:
    """Download selected daily JSON files; return serializable bundle for disk cache."""
    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict[str, dict | None] = {}
    for rel in paths:
        url = f"{RAW_BASE}/{rel}"
        out[rel] = _http_json(url, timeout=timeout_per)
    return {
        "generated_utc": utc,
        "source": "github.com/ErcinDedeoglu/crypto-market-data (CC BY 4.0)",
        "base_url": RAW_BASE,
        "datasets": out,
    }


def _section_title_for_file(rel: str) -> str:
    if rel.startswith("stablecoin_"):
        return "Stablecoin (CEX)"
    if "miner" in rel or "puell" in rel or "miners_" in rel:
        return "Miners"
    if any(
        x in rel
        for x in ("funding", "open_interest", "taker", "liquidation")
    ):
        return "Derivatives"
    if "mvrv" in rel:
        return "Valuation"
    if "coinbase" in rel or "korea" in rel:
        return "Institutional"
    if "supply_ratio" in rel or "fund_flow" in rel:
        return "Liquidity / context"
    return "BTC exchange / whales"


def format_bundle_text(
    bundle: dict | None,
    *,
    paths: tuple[str, ...] = DEFAULT_PATHS,
    max_chars: int = 1600,
    title: str = "*BTC macro (daily on-chain / derivatives)*",
) -> str:
    """Human-readable block from ``fetch_remote_bundle`` output or saved JSON."""
    if not bundle or not isinstance(bundle, dict):
        return ""
    lines: list[str] = [title, ""]
    ds = bundle.get("datasets")
    if not isinstance(ds, dict):
        return ""
    for rel in paths:
        doc = ds.get(rel)
        if not isinstance(doc, dict):
            lines.append(f"• `{rel}`: _unavailable_")
            continue
        name, v_last, v_old, dtype, nwin = _series_tail(doc)
        if v_last is None:
            lines.append(f"• {name}: _no values_")
            continue
        delta = ""
        if v_old is not None and v_old != 0 and nwin > 1:
            try:
                pct = (v_last - v_old) / abs(v_old) * 100.0
                delta = f" Δ~{pct:+.1f}% (last {nwin - 1} daily bars)"
            except ZeroDivisionError:
                delta = ""
        unit = f" ({dtype})" if dtype else ""
        lines.append(f"• **{name}**{unit}: `{_fmt_num(v_last)}`{delta}")
    lines.append("")
    lines.append(ATTRIBUTION_MARKDOWN)
    text = "\n".join(lines).strip()
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 24].rstrip() + "\n…_(truncated)_"
    return text


def build_daily_analysis_markdown(bundle: dict | None) -> str:
    """One-pass daily report for all README datasets (LLM / subagent friendly)."""
    utc = (
        (bundle or {}).get("generated_utc")
        if isinstance(bundle, dict)
        else None
    ) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = [
        "# Crypto Market Data — daily analysis snapshot",
        "",
        f"_Generated (UTC): {utc}_",
        "",
        "_Source: [ErcinDedeoglu/crypto-market-data](https://github.com/ErcinDedeoglu/crypto-market-data) (CC BY 4.0). "
        "Not Sygnif TA; not Bybit OHLC. Daily bars._",
        "",
    ]
    ds = (bundle or {}).get("datasets") if isinstance(bundle, dict) else None
    if not isinstance(ds, dict):
        lines.append("_No datasets._")
        return "\n".join(lines)

    by_section: dict[str, list[str]] = {}
    for rel in ALL_README_DAILY_PATHS:
        doc = ds.get(rel)
        sec = _section_title_for_file(rel)
        if sec not in by_section:
            by_section[sec] = []
        if not isinstance(doc, dict):
            by_section[sec].append(f"- `{rel}`: _fetch failed_")
            continue
        name, v_last, v_old, dtype, nwin = _series_tail(doc, lookback_points=8)
        sig = str(doc.get("trading_signal") or "")[:200].replace("\n", " ")
        if v_last is None:
            by_section[sec].append(f"- **{name}** (`{rel}`): _empty series_")
            continue
        delta = ""
        if v_old is not None and v_old != 0 and nwin > 1:
            try:
                pct = (v_last - v_old) / abs(v_old) * 100.0
                delta = f"; Δ≈{pct:+.1f}% vs {nwin - 1}d window"
            except ZeroDivisionError:
                pass
        dt = f" _({dtype})_" if dtype else ""
        sig_line = f" — _Signal hint:_ {sig}" if sig else ""
        by_section[sec].append(
            f"- **{name}**{dt} (`{rel}`): `{_fmt_num(v_last)}`{delta}{sig_line}"
        )

    order = (
        "BTC exchange / whales",
        "Stablecoin (CEX)",
        "Miners",
        "Derivatives",
        "Valuation",
        "Liquidity / context",
        "Institutional",
    )
    for sec in order:
        rows = by_section.pop(sec, None)
        if not rows:
            continue
        lines.append(f"## {sec}")
        lines.append("")
        lines.extend(rows)
        lines.append("")
    for sec, rows in sorted(by_section.items()):
        lines.append(f"## {sec}")
        lines.append("")
        lines.extend(rows)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(ATTRIBUTION_MARKDOWN)
    return "\n".join(lines).strip() + "\n"


def write_bundle_json(out_dir: Path, bundle: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "btc_crypto_market_data.json"
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_bundle_from_file(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_crypto_market_data_btc_summary(
    *,
    max_chars: int = 1600,
    timeout_per_file: float = 7.0,
    prefer_path: Path | None = None,
    use_remote_cache: bool = True,
) -> str:
    """Prefer on-disk bundle from ``pull_btc_context``; else in-memory remote cache / fetch."""
    if prefer_path is not None:
        disk = load_bundle_from_file(prefer_path)
        if disk:
            return format_bundle_text(disk, max_chars=max_chars)
    if use_remote_cache:
        b = get_bundle_cached()
        if b:
            return format_bundle_text(b, max_chars=max_chars)
    bundle = fetch_remote_bundle(timeout_per=timeout_per_file)
    if not any(bundle.get("datasets", {}).values()):
        return ""
    return format_bundle_text(bundle, max_chars=max_chars)


def briefing_lines_plain(*, max_chars: int = 900) -> str:
    """Compact pipe-friendly lines for ``_briefing`` (Plutus / overseer); cached via ``get_bundle_cached``."""
    bundle = get_bundle_cached()
    if not bundle:
        return ""
    ds = bundle.get("datasets")
    if not isinstance(ds, dict):
        return ""
    parts: list[str] = []
    for rel in DEFAULT_PATHS:
        doc = ds.get(rel)
        if not isinstance(doc, dict):
            continue
        key = rel.replace(".json", "").replace("btc_", "BTC_").replace("stablecoin_", "SC_")
        _name, v_last, _, _, _ = _series_tail(doc, lookback_points=2)
        if v_last is None:
            continue
        parts.append(f"{key}:{_fmt_num(v_last)}")
    line = "CRYPTO_MD|" + "|".join(parts[:12])
    if len(line) > max_chars:
        line = line[: max_chars - 3] + "..."
    attr = "SRC:github/ErcinDedeoglu/crypto-market-data|CC-BY-4.0|not-Sygnif-TA"
    return line + "\n" + attr
