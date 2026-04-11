"""Assemble BTC offline-bundle text for Telegram (checklist aligned with btc-specialist agent)."""

from __future__ import annotations

import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data"


def _read_json(name: str) -> dict | list | None:
    p = _DATA / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_btc_specialist_report(*, max_chars: int = 4500) -> str:
    """Summarize manifest + bundle JSONs; never claims live Bybit unless from ticker file."""
    lines: list[str] = [
        "*Offline bundle* (`finance_agent/btc_specialist/data/`)",
        "",
    ]
    man = _read_json("manifest.json")
    if not man:
        lines.append(
            "_No `manifest.json` — from repo root run:_\n"
            "`python3 finance_agent/btc_specialist/scripts/pull_btc_context.py`"
        )
        return "\n".join(lines)

    lines.append(f"_Generated (UTC):_ `{man.get('generated_utc', '?')}`")
    src = man.get("source", "")
    if src:
        lines.append(f"_Source note:_ {src}")
    lines.append("")

    tick = _read_json("bybit_btc_ticker.json")
    if isinstance(tick, dict) and tick:
        lp = tick.get("lastPrice")
        if lp is None and isinstance(tick.get("result"), dict):
            lp = tick["result"].get("lastPrice")
        if lp is not None:
            lines.append(f"*Snapshot ticker lastPrice:* `{lp}` _(file, not live)_")

    snap = _read_json("btc_sygnif_ta_snapshot.json")
    if isinstance(snap, dict) and snap:
        ta = snap.get("ta_score")
        tags = snap.get("entries") or snap.get("signals")
        lines.append("")
        lines.append("*`btc_sygnif_ta_snapshot.json`*")
        if ta is not None:
            lines.append(f"• TA score (snapshot): `{ta}`")
        if tags:
            lines.append(f"• Entries/signals: `{tags}`")
        raw_preview = json.dumps(snap, ensure_ascii=False)[:900]
        lines.append(f"```\n{raw_preview}\n```")

    daily = _read_json("btc_daily_90d.json")
    if isinstance(daily, list) and len(daily) >= 2:
        lines.append("")
        lines.append(f"*Daily candles in bundle:* `{len(daily)}` bars")

    fdn = _read_json("btc_fdn_fundamentals.json")
    if isinstance(fdn, dict) and fdn:
        lines.append("")
        lines.append("*FDN snapshot present* (`btc_fdn_fundamentals.json`) — _third-party, not Sygnif TA_.")

    nh = _read_json("btc_newhedge_altcoins_correlation.json")
    if nh:
        lines.append("")
        lines.append("*NewHedge correlation snapshot present* — _vendor metric, not Bybit OHLC_.")

    lines.append("")
    lines.append("_Live structure: use `/ta BTC` or `/btc` (includes live TA + bundle footer)._")

    out = "\n".join(lines).strip()
    if max_chars and len(out) > max_chars:
        return out[: max_chars - 20].rstrip() + "\n…_(truncated)_"
    return out
