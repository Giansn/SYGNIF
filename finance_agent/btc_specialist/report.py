"""Assemble BTC offline-bundle text for Telegram (checklist aligned with btc-specialist agent)."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data"


def _fmt_foundation_line(ca: dict[str, Any] | None) -> str:
    """Human line from Crypto APIs bundle (summary first, else minimal response hint)."""
    if not isinstance(ca, dict):
        return ""
    s = ca.get("summary")
    if isinstance(s, dict) and s:
        bits: list[str] = []
        if s.get("block_height") is not None:
            bits.append(f"Mainnet block {s['block_height']}")
        if s.get("transactions_count") is not None:
            bits.append(f"{s['transactions_count']} txs in block")
        if s.get("btc_usd_rate") is not None:
            bits.append(f"CA ref BTC/USD {s['btc_usd_rate']}")
        if s.get("marketCap") is not None:
            bits.append(f"mkt cap {s['marketCap']}")
        if s.get("block_hash_prefix"):
            bits.append(f"hash {s['block_hash_prefix']}")
        if bits:
            return " · ".join(bits)
    raw = ca.get("responses")
    if isinstance(raw, dict) and any(raw.values()):
        ok = [k for k, v in raw.items() if v]
        return "Crypto APIs responses present (summary empty): " + ", ".join(ok[:6])
    return ""


def _fmt_newhedge_line(nh: dict[str, Any] | None) -> str:
    """Interpret NewHedge altcoins correlation bundle (same semantics as dashboard JS)."""
    if not isinstance(nh, dict):
        return ""
    pl = nh.get("payload")
    if not isinstance(pl, list) or not pl:
        return ""
    last = pl[-1]
    if not isinstance(last, (list, tuple)) or len(last) < 2:
        return ""
    try:
        v = float(last[1])
    except (TypeError, ValueError):
        return f"Latest point: {last!r}"
    hi = v >= 0.65
    tail = (
        "Higher coupling — alts more likely to follow BTC; risk-on/off together."
        if hi
        else "Lower coupling — more idiosyncratic alt moves vs BTC lead."
    )
    return f"NewHedge altcoins ρ ≈ {v:.3f} (latest). {tail}"


def _truncate_md_at_sections(body: str, max_chars: int = 48000) -> str:
    """Cut at ``\\n## `` boundaries when possible so list items are not split mid-string."""
    if len(body) <= max_chars:
        return body
    parts = body.split("\n## ")
    if len(parts) <= 1:
        return body[: max_chars - 40].rstrip() + "\n\n… _(truncated)_"
    acc = parts[0]
    for i in range(1, len(parts)):
        sec = "## " + parts[i]
        trial = acc + "\n" + sec
        if len(trial) <= max_chars - 80:
            acc = trial
        else:
            return (
                acc.rstrip()
                + "\n\n_(… truncated — remainder in `crypto_market_data_daily_analysis.md` on server)_"
            )
    return acc


def _fmt_crypto_market_snippet(out_dir: Path, *, max_chars: int = 48000) -> str:
    """README daily analysis (API-pulled JSONs); truncated at section boundaries when long."""
    md = out_dir / "crypto_market_data_daily_analysis.md"
    if md.is_file():
        body = md.read_text(encoding="utf-8").strip()
        return _truncate_md_at_sections(body, max_chars=max_chars)
    return ""


def _parse_bullet_line(line: str) -> dict[str, str] | None:
    line = line.strip()
    if not line.startswith("- "):
        return None
    nm = re.search(r"\*\*([^*]+)\*\*", line)
    name = nm.group(1).strip() if nm else "Metric"
    vm = re.search(r"\):\s*`([^`]+)`", line)
    val = vm.group(1) if vm else ""
    hint = ""
    if "Signal hint:" in line:
        idx = line.find("Signal hint:")
        hint = line[idx + len("Signal hint:") :].strip()
        hint = re.sub(r"^_+|_+$", "", hint)
        hint = hint.replace("_", " ").strip()
    return {"name": name, "value": val, "hint": hint}


def _eval_section_tone(hints: list[str]) -> str:
    t = " ".join(hints).lower()
    bull = sum(
        1
        for w in (
            "bullish",
            "squeeze",
            "confidence",
            "accumul",
            "buying",
            "hodl",
            "tight",
            "cheap",
            "bottom",
        )
        if w in t
    )
    bear = sum(
        1
        for w in (
            "bearish",
            "dump",
            "panic",
            "overheat",
            "liquidat",
            "expensive",
            "top",
            "risk",
        )
        if w in t
    )
    if bull > bear + 1:
        return "Overall: vendor hints in this block skew constructive — use as context, not a standalone trigger."
    if bear > bull + 1:
        return "Overall: vendor hints in this block skew cautious — use as context, not a standalone trigger."
    return "Overall: mixed / balanced reads in this block — align with your TA and risk rules."


def _summarize_section_narrative(_title: str, bullets: list[dict[str, str]]) -> str:
    """Short prose + evaluation from parsed bullets (not a raw list dump)."""
    if not bullets:
        return ""
    hints = [b["hint"] for b in bullets if b.get("hint")]
    tone = _eval_section_tone(hints)
    b0 = bullets[0]
    s0 = (
        f"Lead: {b0['name']} is at {b0['value']}. "
        f"Per the bundled guide: {b0['hint'][:220]}{'…' if len(b0['hint']) > 220 else ''}"
    )
    if len(bullets) == 1:
        return f"{s0} {tone}"
    b1 = bullets[1]
    s1 = (
        f"Supporting: {b1['name']} ({b1['value']}) — {b1['hint'][:180]}"
        f"{'…' if len(b1['hint']) > 180 else ''}"
    )
    return f"{s0} {s1} {tone}"


def _build_crypto_market_sections(md: str) -> list[dict[str, str]]:
    """Split daily README into ``##`` sections; each gets a short narrative + tone eval."""
    out: list[dict[str, str]] = []
    if not md.strip():
        return out
    chunks = re.split(r"\n(?=##\s)", md.strip())
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk.startswith("##"):
            continue
        lines = chunk.split("\n")
        title = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:])
        bullets: list[dict[str, str]] = []
        for ln in body.split("\n"):
            b = _parse_bullet_line(ln)
            if b:
                bullets.append(b)
        analysis = _summarize_section_narrative(title, bullets)
        if analysis:
            out.append({"title": title, "analysis": analysis})
    return out


def _try_llm_crypto_sections(out_dir: Path) -> list[dict[str, str]] | None:
    """Finance-agent stack: ``crypto_context_llm`` + ``llm_analyze`` (see ``/finance-agent``)."""
    if (os.environ.get("CRYPTO_CONTEXT_LLM") or "1").strip().lower() in ("0", "false", "no"):
        return None
    md_path = out_dir / "crypto_market_data_daily_analysis.md"
    if not md_path.is_file():
        return None
    fa = Path(__file__).resolve().parent.parent
    if str(fa) not in sys.path:
        sys.path.insert(0, str(fa))
    try:
        from crypto_context_llm import generate_crypto_context_sections_llm  # noqa: PLC0415

        return generate_crypto_context_sections_llm(md_path)
    except Exception:
        return None


def build_btc_specialist_dashboard_doc(out_dir: Path, utc: str) -> dict[str, Any]:
    """
    Web dashboard JSON: crypto-market sections — finance-agent LLM analysis when available,
    else heuristic summaries from the same README.
    """
    md_path = out_dir / "crypto_market_data_daily_analysis.md"
    md = md_path.read_text(encoding="utf-8").strip() if md_path.is_file() else ""
    source: str = "none"
    sections: list[dict[str, str]] = []
    if md:
        llm_secs = _try_llm_crypto_sections(out_dir)
        if llm_secs:
            sections = llm_secs
            source = "llm"
        else:
            sections = _build_crypto_market_sections(md)
            source = "heuristic"
    return {
        "generated_utc": utc,
        "note": "crypto_market_sections: finance-agent (llm_analyze + KB) when CRYPTO_CONTEXT_LLM=1; else heuristic.",
        "crypto_market_sections": sections,
        "crypto_market_sections_source": source,
    }


def _read_json_from(out_dir: Path, name: str) -> dict[str, Any] | list[Any] | None:
    p = out_dir / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_btc_specialist_dashboard_json(out_dir: Path, utc: str) -> None:
    """Write btc_specialist_dashboard.json next to other pull_btc_context outputs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = build_btc_specialist_dashboard_doc(out_dir, utc)
    (out_dir / "btc_specialist_dashboard.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


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

    ca = _read_json("btc_cryptoapis_foundation.json")
    if isinstance(ca, dict) and isinstance(ca.get("summary"), dict) and ca["summary"]:
        lines.append("")
        lines.append(
            "*Crypto APIs foundation present* (`btc_cryptoapis_foundation.json`) — "
            "_on-chain / market ref, not Sygnif TA_."
        )

    nh = _read_json("btc_newhedge_altcoins_correlation.json")
    if nh:
        lines.append("")
        lines.append("*NewHedge correlation snapshot present* — _vendor metric, not Bybit OHLC_.")

    daily_md_path = _DATA / "crypto_market_data_daily_analysis.md"
    cmd_path = _DATA / "btc_crypto_market_data.json"
    try:
        from crypto_market_data import (
            build_crypto_market_data_btc_summary,
            format_bundle_text,
            load_bundle_from_file,
            paths_order_from_bundle,
        )

        if daily_md_path.is_file():
            body = daily_md_path.read_text(encoding="utf-8")
            used = len("\n".join(lines))
            budget = max(2800, (max_chars or 9000) - used - 400)
            budget = min(budget, 12000)
            lines.append("")
            lines.append(
                "*Crypto Market Data* — full README daily analysis "
                "(`crypto_market_data_daily_analysis.md`)"
            )
            lines.append("")
            snippet = body[:budget].rstrip()
            if len(body) > budget:
                snippet += "\n\n…_(truncated — run daily script for full file)_"
            lines.append(snippet)
        else:
            disk = load_bundle_from_file(cmd_path)
            ds = disk.get("datasets") if isinstance(disk, dict) else None
            full_bundle = isinstance(ds, dict) and len(ds) >= 20
            if full_bundle:
                lines.append("")
                lines.append(
                    format_bundle_text(
                        disk,
                        paths=paths_order_from_bundle(disk),
                        max_chars=min(3500, (max_chars or 4500) - 500),
                        title="*Crypto Market Data — all daily series (compact)*",
                    )
                )
            else:
                cmd_txt = build_crypto_market_data_btc_summary(
                    max_chars=1400,
                    prefer_path=cmd_path,
                    use_remote_cache=True,
                ).strip()
                if cmd_txt:
                    lines.append("")
                    lines.append(cmd_txt)
    except Exception:
        pass

    lines.append("")
    lines.append("_Live Sygnif TA + signals: `/ta BTC` — `/btc` is specialist bundle only._")

    out = "\n".join(lines).strip()
    if max_chars and len(out) > max_chars:
        return out[: max_chars - 20].rstrip() + "\n…_(truncated)_"
    return out
