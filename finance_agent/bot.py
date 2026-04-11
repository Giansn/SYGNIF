#!/usr/bin/env python3
"""
Sygnif Finance Agent — Telegram bot for crypto research & analysis.
Combines market scanning, technical analysis, and AI-powered insights.
Strategy-aware: computes the same TA score and detects entry/exit signals
as SygnifStrategy.py so research aligns with live bot behavior. Indicators include
MFI(14) and OBV (optional pandas_ta for exact parity with the strategy; pandas fallback otherwise).

Commands:
  /market          — Top 10 crypto overview
  /movers [1h|24h] — Top gainers & losers
  /ta <TICKER>     — Technical analysis with strategy signals
  /btc             — BTC specialist offline bundle + optional FDN + optional NewHedge; live TA: /ta BTC
  /research <TICK> — Full research (market + TA + news + AI)
  /finance-agent network [docs|nodes|nn] — Network submodule; nodes+NN topology + OpenVINO stack
  /finance-agent trades|check — Open positions + closed-trade aggregates (overseer)
  /plays           — AI investment opportunity scan
  /signals         — Quick scan: active entry signals across top pairs
  /news            — Latest crypto headlines
  /deduce <text>   — Deductive chain (premises → conclusion, Sygnif-aware)
  /ask <text>      — LLM via Cursor Cloud Agent API (fluent chat; optional Ollama if configured)
  Freitext         — Same as chat; context = Telegram-Verlauf (Session)
  /clear           — Chat-Verlauf löschen
  /fa_help         — Show commands

Env:
  TELEGRAM_SLASH_TOOL_FIRST — default `1`: preset slash commands use finance-agent `cmd_*`
  analysis output directly; set `0` to always summarize via Cursor/Ollama (legacy).
"""

import base64
import errno
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import numpy as np
import pandas as pd
import requests

try:
    import pandas_ta as pta  # type: ignore[import-untyped]
except ImportError:  # optional: aligns with SygnifStrategy when installed (freqtrade image)
    pta = None  # type: ignore[assignment]

from expert_sentiment import expert_sygnif_sentiment_score
from finance_agent_expert import (
    expert_evaluate_lines,
    expert_plays_from_scan,
    expert_research_markdown,
    expert_scan_ranking_rows,
    expert_tendency_insight,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("finance_agent")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Telegram: @sygnif_agent_bot — AGENT_* in .env; legacy fallbacks.
TG_TOKEN = (
    os.environ.get("AGENT_BOT_TOKEN", "").strip()
    or os.environ.get("SYGNIF_HEDGE_BOT_TOKEN", "").strip()
    or os.environ.get("FINANCE_BOT_TOKEN", "").strip()
)
TG_CHAT = (
    os.environ.get("AGENT_CHAT_ID", "").strip()
    or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
)
# LLM: primär Cursor Cloud Agents API (gleiches Modell wie Sygnif über cursor-agent-worker + Cursor).
# Optional: Ollama auf der Instanz wenn keine CURSOR_*-Keys gesetzt.
CURSOR_API_BASE = os.environ.get("CURSOR_API_BASE", "https://api.cursor.com").rstrip("/")
CURSOR_API_KEY = os.environ.get("CURSOR_API_KEY", "").strip()
CURSOR_AGENT_REPOSITORY = os.environ.get("CURSOR_AGENT_REPOSITORY", "").strip()
CURSOR_AGENT_REF = os.environ.get("CURSOR_AGENT_REF", "main").strip()
CURSOR_AGENT_MODEL = os.environ.get("CURSOR_AGENT_MODEL", "").strip()
CURSOR_AGENT_MAX_WAIT_SEC = int(os.environ.get("CURSOR_AGENT_MAX_WAIT_SEC", "900"))
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "").strip()
BYBIT = "https://api.bybit.com/v5"

# Trade overseer (Docker or host) + Cursor agent worker health
OVERSEER_URL = os.environ.get("OVERSEER_URL", "http://127.0.0.1:8090").rstrip("/")
CURSOR_WORKER_HEALTH_URL = os.environ.get(
    "CURSOR_WORKER_HEALTH_URL", "http://127.0.0.1:8093/healthz"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sygnif_repo() -> Path:
    """Freqtrade repo with `user_data/` (strategy_adaptation.json, advisor_state.json)."""
    default = Path.home() / "SYGNIF"
    return Path(os.environ.get("SYGNIF_REPO", str(default))).resolve()


_FINANCE_AGENT_KB_CACHE: tuple[str, float, str] | None = None  # resolved path, mtime, full text


def _strip_markdown_yaml_frontmatter(md: str) -> str:
    t = (md or "").lstrip("\ufeff")
    if not t.startswith("---"):
        return t
    end = t.find("\n---", 4)
    if end == -1:
        return t
    return t[end + 4 :].lstrip("\n")


def load_finance_agent_kb(*, max_chars: int = 26000) -> str:
    """Fused finance-agent KB: `FINANCE_AGENT_KB_FILE` (Docker), else `.cursor/agents/` in `SYGNIF_REPO`."""
    global _FINANCE_AGENT_KB_CACHE
    explicit = (os.environ.get("FINANCE_AGENT_KB_FILE") or "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    repo = _sygnif_repo()
    candidates.extend(
        [
            repo / ".cursor/agents/finance-agent.md",
            repo / ".cursor/skills/finance-agent/SKILL.md",
        ]
    )
    for p in candidates:
        try:
            if not p.is_file():
                continue
            st = p.stat()
            resolved = str(p.resolve())
        except OSError:
            continue
        if _FINANCE_AGENT_KB_CACHE and (
            _FINANCE_AGENT_KB_CACHE[0] == resolved and _FINANCE_AGENT_KB_CACHE[1] == st.st_mtime
        ):
            return _FINANCE_AGENT_KB_CACHE[2][:max_chars]
        raw = p.read_text(encoding="utf-8", errors="replace")
        text = _strip_markdown_yaml_frontmatter(raw)
        _FINANCE_AGENT_KB_CACHE = (resolved, st.st_mtime, text)
        return text[:max_chars]
    return ""


def _network_monorepo_root() -> Path:
    """Git submodule [Giansn/Network](https://github.com/Giansn/Network) at `SYGNIF/network/`."""
    return _sygnif_repo() / "network"


def _network_markdown_h2_titles(path: Path, *, limit: int = 15) -> list[str]:
    """First `##` headings from a markdown file (for agent context, no LLM)."""
    if not path.is_file():
        return []
    out: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        if line.startswith("## "):
            out.append(line[3:].strip())
            if len(out) >= limit:
                break
    return out


def _gather_network_nodes_and_nn(*, max_chars: int | None = None) -> str:
    """Logical nodes + NN stack from Network submodule (reads docs; optional HTTP status URL)."""
    root = _network_monorepo_root()
    draft = root / "docs" / "AGENT_NODE_NETWORK_DRAFT.md"
    setup = root / "docs" / "NEURAL_NETWORK_SETUP.md"
    lines: list[str] = [
        "=== NETWORK NODES + NN (Giansn/Network — non-LLM OpenVINO split) ===",
        "",
    ]
    if not root.is_dir():
        lines.append("Submodule missing: `git submodule update --init --recursive network` in SYGNIF.")
        s = "\n".join(lines)
        if max_chars and len(s) > max_chars:
            return s[:max_chars].rstrip() + "\n…(truncated)"
        return s

    h2 = _network_markdown_h2_titles(draft, limit=14)
    if h2:
        lines.append("Agent-node draft — section titles:")
        for t in h2:
            lines.append(f"  • {t}")
        lines.append("")

    lines.extend(
        [
            "Topologies (logical): SINGLE_NPU — full ov.Model on one device; "
            "SPLIT_EDGE_GATEWAY — stage A near edge, narrow activation over the wire, stage B gateway/cloud.",
            "NN modules (`network/edge_npu_infer/`):",
            "  • run_npu.py — device smoke, optional IR `--xml`, timing",
            "  • placement.py — SplitStage, run_split_pipeline, inject send_tensor/recv_tensor",
            "  • wire_tensor.py — pack_tensor / unpack_tensor / make_wire_pair (bytes between nodes)",
            "  • mcp_npu_server.py — Cursor MCP (devices, IR infer)",
            "",
            "Physical / ops: aws-node-network/ (VPC, Client VPN); "
            "Invoke-SsmRunEdgeInfer.ps1 — remote run_npu.py on EC2 via SSM.",
            "",
        ]
    )
    if setup.is_file():
        lines.append(f"Install & expand: `{setup.relative_to(root)}`")
    else:
        lines.append("Setup doc: docs/NEURAL_NETWORK_SETUP.md _(missing in checkout)_")

    status_url = os.environ.get("NETWORK_NN_STATUS_URL", "").strip()
    if status_url:
        lines.append("")
        try:
            r = requests.get(status_url, timeout=4)
            snippet = (r.text or "").strip()[:900]
            lines.append(f"NETWORK_NN_STATUS_URL GET {r.status_code}:")
            lines.append(snippet or "(empty body)")
        except Exception as e:
            lines.append(f"NETWORK_NN_STATUS_URL error: {e}")

    lines.append("")
    lines.append("Telegram: `/finance-agent network` (short) · `/finance-agent network nodes` (this block)")

    s = "\n".join(lines)
    if max_chars and len(s) > max_chars:
        return s[:max_chars].rstrip() + "\n…(truncated)"
    return s


def cmd_network_section(tail: str = "") -> str:
    """Telegram-facing summary of Network monorepo + edge OpenVINO layout (no OpenVINO import)."""
    root = _network_monorepo_root()
    edge = root / "network" / "edge_npu_infer"
    setup_doc = root / "docs" / "NEURAL_NETWORK_SETUP.md"
    draft_doc = root / "docs" / "AGENT_NODE_NETWORK_DRAFT.md"
    lines: list[str] = [
        "*Network monorepo* (`SYGNIF/network/` → [github.com/Giansn/Network](https://github.com/Giansn/Network))",
        f"Path: `{root}`",
    ]
    if not root.is_dir():
        return (
            "\n".join(lines)
            + f"\n\n_Not found._ Set `SYGNIF_REPO` to your clone, then:\n"
            "`git submodule update --init --recursive network`"
        )
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            text=True,
            timeout=8,
            stderr=subprocess.DEVNULL,
        ).strip()
        lines.append(f"Submodule HEAD: `{sha}`")
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        lines.append("Submodule HEAD: _(git not available)_")

    lines.append(f"Edge OpenVINO: `{'ok' if edge.is_dir() else 'missing'}` `{edge}`")
    lines.append(f"Setup doc: `{'ok' if setup_doc.is_file() else 'missing'}` `{setup_doc.name}`")
    lines.append(f"Agent-node draft: `{'ok' if draft_doc.is_file() else 'missing'}` `{draft_doc.name}`")

    hint = (
        "\n*Commands:*\n"
        "• `/finance-agent network nodes` (or `nn`) — topology + NN modules + optional `NETWORK_NN_STATUS_URL`\n"
        "• `/finance-agent network docs` — doc titles + SSM script hint\n"
        "• Local smoke (on a dev box with OpenVINO): "
        "`cd network/edge_npu_infer && pip install -r requirements.txt && python run_npu.py --device CPU`"
    )

    t = (tail or "").strip().lower()
    if t in ("nodes", "nn", "node", "topology", "split", "npu", "openvino"):
        return _gather_network_nodes_and_nn()
    if t in ("docs", "help", "?", "setup"):
        lines.append(
            "\n*Docs (in submodule):*\n"
            "• `docs/NEURAL_NETWORK_SETUP.md` — OpenVINO smoke, split `placement.py`, `wire_tensor.py`, EC2 SSM\n"
            "• `docs/AGENT_NODE_NETWORK_DRAFT.md` — agent-node topology, phased rollout\n"
            "• `aws-node-network/scripts/Invoke-SsmRunEdgeInfer.ps1` — run `run_npu.py` on instance\n"
            "• SYGNIF: `./scripts/update_network_submodule.sh` — pull latest `main`"
        )
        return "\n".join(lines)

    lines.append(
        "\n*Nodes + NN (summary):* `SINGLE_NPU` vs `SPLIT_EDGE_GATEWAY` — narrow tensors via `wire_tensor` "
        "between stages; see `placement.py` / `run_npu.py`. Full dump: `/finance-agent network nodes`."
    )
    return "\n".join(lines) + hint


def cmd_strategy_analytics() -> str:
    """Runtime strategy adaptation JSON + path (SygnifStrategy hot-reload)."""
    p = _sygnif_repo() / "user_data" / "strategy_adaptation.json"
    if not p.is_file():
        return f"*Strategy analytics*\n`{p}` — _not present_ (defaults from strategy class).\n"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return f"*Strategy adaptation* — invalid JSON structure\n"
        overrides = data.get("overrides")
        if isinstance(overrides, dict) and overrides:
            lines = [f"*Strategy adaptation* (`{p.name}`)\n"]
            for k in sorted(overrides.keys())[:40]:
                lines.append(f"• `{k}` → `{overrides[k]}`")
            meta = {k: data[k] for k in ("version", "updated", "source", "reason") if k in data}
            if meta:
                lines.append("\n_Meta:_ " + " ".join(f"{k}={v}" for k, v in meta.items()))
            return "\n".join(lines)
        return f"*Strategy adaptation*\n```json\n{json.dumps(data, indent=2)[:3500]}\n```"
    except Exception as e:
        return f"*Strategy analytics* — read error: `{e}`"


def cmd_sygnif_state() -> str:
    """Last advisor observer snapshot (JSON file, no LLM)."""
    p = _sygnif_repo() / "user_data" / "advisor_state.json"
    if not p.is_file():
        return (
            f"*Advisor state* — `{p.name}` not yet written. "
            f"Set `ADVISOR_BG_INTERVAL_SEC` (>0) in `.env` or run:\n"
            f"`python3 {_sygnif_repo() / 'scripts' / 'sygnif_advisor_observer.py'}`"
        )
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        return f"*Advisor state* — read error: `{e}`"
    if len(raw) > 3800:
        raw = raw[:3800] + "\n…(truncated)"
    return f"*Advisor state* (`{p.name}`)\n```json\n{raw}\n```"


def cmd_sygnif_pending() -> str:
    """Show queued proposals (apply with `/sygnif approve <id>`)."""
    p = _sygnif_repo() / "user_data" / "advisor_pending.json"
    if not p.is_file():
        return "*Advisor pending* — no queue file yet (observer may add heuristics when `ADVISOR_HEURISTICS=1`)."
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return f"*Advisor pending* — `{e}`"
    items = [x for x in data.get("items", []) if x.get("status") == "pending"]
    if not items:
        return "*Advisor pending* — no *pending* items (all applied or empty)."
    lines = [f"*Pending proposals* ({len(items)})\n"]
    for it in items[:15]:
        iid = it.get("id", "?")
        lines.append(f"• `{iid}` — {it.get('reason', '')[:200]}")
        lines.append(f"  overrides: `{it.get('proposed_overrides')}`")
    lines.append("\nApply: `/sygnif approve <id>` (merges into `strategy_adaptation.json`).")
    return "\n".join(lines)


def cmd_sygnif_approve(item_id: str) -> str:
    """Merge validated proposed_overrides into strategy_adaptation.json; mark item applied."""
    item_id = (item_id or "").strip()
    if not item_id:
        return "Usage: `/sygnif approve <id>` — see `/sygnif pending`"

    pend_path = _sygnif_repo() / "user_data" / "advisor_pending.json"
    adapt_path = _sygnif_repo() / "user_data" / "strategy_adaptation.json"
    mod_path = _sygnif_repo() / "user_data" / "strategy_adaptation.py"

    if not pend_path.is_file():
        return "*approve* — no `advisor_pending.json`"

    try:
        data = json.loads(pend_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return f"*approve* — pending read error: `{e}`"

    items = data.get("items", [])
    hit = None
    for it in items:
        if it.get("id") == item_id and it.get("status") == "pending":
            hit = it
            break
    if not hit:
        return f"*approve* — no pending item `{item_id}`"

    prop = hit.get("proposed_overrides") or {}
    if not isinstance(prop, dict) or not prop:
        return "*approve* — empty `proposed_overrides`"

    spec = importlib.util.spec_from_file_location("_sygnif_sa", mod_path)
    if spec is None or spec.loader is None:
        return "*approve* — cannot load `strategy_adaptation.py`"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    validated = mod.validate_overrides(prop)
    if not validated:
        return "*approve* — nothing valid after clamp (check BOUNDS)."

    base: dict = {}
    if adapt_path.is_file():
        try:
            base = json.loads(adapt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            base = {}
    if not isinstance(base, dict):
        base = {}
    ovr = base.get("overrides")
    if not isinstance(ovr, dict):
        ovr = {}
    ovr.update(validated)
    base["overrides"] = ovr
    base["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base["source"] = "advisor_approve"
    base["reason"] = f"approved {item_id}: {(hit.get('reason') or '')[:220]}"

    tmp = adapt_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(adapt_path)

    hit["status"] = "applied"
    hit["applied_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pend_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return (
        f"*Applied* `{item_id}` → `{validated}`\n"
        f"_Freqtrade picks up overrides in ~60s (no restart)._"
    )


def _try_sygnif_direct(text: str) -> str | None:
    """Non-LLM /sygnif subcommands for deterministic ops."""
    parts = text.strip().split()
    if len(parts) < 2:
        return None
    root = parts[0].lower().split("@")[0]
    if root != "/sygnif":
        return None
    sub = parts[1].lower().split("@")[0]
    if sub == "state":
        return cmd_sygnif_state()
    if sub == "pending":
        return cmd_sygnif_pending()
    if sub == "approve" and len(parts) >= 3:
        return cmd_sygnif_approve(parts[2])
    return None


def _start_advisor_background() -> None:
    """Periodic observer: writes advisor_state.json (+ optional heuristics → advisor_pending.json)."""
    interval = int(os.environ.get("ADVISOR_BG_INTERVAL_SEC", "3600"))
    if interval <= 0:
        logger.info("Advisor background: disabled (ADVISOR_BG_INTERVAL_SEC<=0)")
        return

    script = _sygnif_repo() / "scripts" / "sygnif_advisor_observer.py"
    every_n = int(os.environ.get("ADVISOR_TELEGRAM_EVERY_N", "0"))

    def _loop():
        n = 0
        while True:
            time.sleep(interval)
            n += 1
            try:
                env = {**os.environ, "SYGNIF_REPO": str(_sygnif_repo())}
                subprocess.run(
                    [sys.executable, str(script)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                logger.info("Advisor observer tick ok (interval=%ss)", interval)
            except Exception as e:
                logger.error("Advisor observer: %s", e)
            try:
                if os.environ.get("ADVISOR_BG_TELEGRAM", "").strip() not in ("1", "true", "yes"):
                    continue
                if every_n > 0 and (n % every_n != 0):
                    continue
                ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
                tg_send(
                    f"*Advisor* — tick {ts}\n"
                    f"State → `{_sygnif_repo()}/user_data/advisor_state.json`\n"
                    f"`/sygnif state` · `/sygnif pending`",
                    reply_markup=KEYBOARD,
                )
            except Exception as e:
                logger.error("Advisor telegram digest: %s", e)

    t = threading.Thread(target=_loop, name="sygnif-advisor", daemon=True)
    t.start()
    logger.info(
        "Advisor background: every %ss → %s (telegram digest=%s, every_n=%s)",
        interval,
        script,
        os.environ.get("ADVISOR_BG_TELEGRAM", "0"),
        every_n,
    )


def gather_sygnif_cycle() -> str:
    """Single bundle for Cursor agent: overseer + adaptation + signals + tendency + macro + worker health."""
    parts: list[str] = []
    parts.append("=== SYGNIF AGENT CYCLE (raw facts for synthesis) ===\n")

    # 1) Cursor worker (optional)
    try:
        h = requests.get(CURSOR_WORKER_HEALTH_URL, timeout=2)
        parts.append(f"cursor_agent_worker: HTTP {h.status_code}\n")
    except Exception as e:
        parts.append(f"cursor_agent_worker: unreachable ({e})\n")

    # 2) Overseer
    try:
        ov = requests.get(f"{OVERSEER_URL}/overview", timeout=5)
        parts.append(f"overseer /overview: HTTP {ov.status_code}\n{ov.text[:1200]}\n")
    except Exception as e:
        parts.append(f"overseer /overview: error `{e}`\n")
    try:
        tr = requests.get(f"{OVERSEER_URL}/trades", timeout=10)
        if tr.ok:
            data = tr.json()
            trades = data.get("trades") or []
            tlines = []
            for t in trades[:25]:
                pair = t.get("pair", "?")
                tid = t.get("trade_id", "?")
                pct = t.get("profit_pct", 0)
                tag = t.get("enter_tag") or ""
                inst = t.get("instance", "")
                tlines.append(f"  id={tid} {pair} [{inst}] {pct:+.2f}% tag={tag}")
            parts.append(
                f"overseer open trades: {len(trades)}\n" + "\n".join(tlines) + "\n"
            )
        else:
            parts.append(f"overseer /trades: HTTP {tr.status_code}\n")
    except Exception as e:
        parts.append(f"overseer /trades: error `{e}`\n")

    parts.append("=== STRATEGY RUNTIME ===\n")
    parts.append(cmd_strategy_analytics())
    parts.append("\n=== SIGNALS (top scan) ===\n")
    sig = cmd_signals()
    parts.append(sig[:4500] + ("…" if len(sig) > 4500 else ""))
    parts.append("\n=== TENDENCY ===\n")
    parts.append(cmd_tendency()[:2500])
    parts.append("\n=== MACRO (snippet) ===\n")
    parts.append(cmd_macro()[:1200])
    parts.append("\n=== NETWORK (submodule) ===\n")
    try:
        nr = _network_monorepo_root()
        if nr.is_dir():
            sha = subprocess.check_output(
                ["git", "-C", str(nr), "rev-parse", "--short", "HEAD"],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            ).strip()
            parts.append(f"path `{nr}`\nHEAD `{sha}`\n")
            parts.append(_gather_network_nodes_and_nn(max_chars=2600))
            parts.append("")
        else:
            parts.append(f"missing `{nr}` — run `git submodule update --init network` in SYGNIF repo\n")
    except Exception as e:
        parts.append(f"network: n/a (`{e}`)\n")
    parts.append(
        "\n---\n_Note:_ Slash-Befehle laufen über `agent_slash_dispatch` → Cursor Cloud (`llm_analyze`). "
        "Freitext nutzt denselben LLM-Pfad mit Chat-Verlauf.\n"
    )
    return "\n".join(parts)


# Telegram conversational memory (in-process; restart clears). Max messages (user+assistant turns*2).
TELEGRAM_CHAT_MAX_HISTORY = int(os.environ.get("TELEGRAM_CHAT_MAX_HISTORY", "40"))
TELEGRAM_CHAT_MAX_CHARS = int(os.environ.get("TELEGRAM_CHAT_MAX_CHARS", "24000"))
# Fluent chat: show typing indicator; plain text default avoids Telegram Markdown parse errors from models.
TELEGRAM_CHAT_TYPING = os.environ.get("TELEGRAM_CHAT_TYPING", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)


def _conversational_parse_mode() -> str | None:
    v = (os.environ.get("TELEGRAM_CONVERSATIONAL_PARSE_MODE") or "plain").strip().lower()
    if v in ("plain", "none", ""):
        return None
    if v in ("markdown", "md"):
        return "Markdown"
    if v == "html":
        return "HTML"
    return None


# System persona for free-text / multi-turn (Ollama messages API + Cursor single prompt).
CONVERSATIONAL_SYSTEM = """Du bist der Sygnif Finance Agent — ein kompetenter Gesprächspartner zu Crypto,
Bybit-Spot, Freqtrade und SygnifStrategy (TA-Score 0–100, Tags wie strong_ta, claude_s*, swing_failure).

Verhalten:
- Führe ein **natürliches, flüssiges Gespräch** wie in einem Chat; beziehe dich auf den bisherigen Verlauf.
- Antworte **direkt** auf die letzte Nutzernachricht; keine Meta-Erklärungen über „als KI“ oder deine Aufgabe.
- **Sprache** wie der Nutzer (Deutsch oder Englisch).
- **Länge**: typisch 2–8 kurze Absätze oder Aufzählungen; mobillesbar; nur bei expliziter Bitte länger.
- **Fakten**: keine erfundenen Kurse; wenn Live-Daten fehlen, sag es kurz und schlag vor, `/ta` (z. B. `/ta BTC`), `/btc` (Offline-Bundle) oder `/market` zu nutzen.
- **Format**: lieber klare Sätze; wenn TELEGRAM_MARKDOWN genutzt wird, keine kaputten Unterstriche oder ungeschlossene *."""

# Strategy constants (mirrors SygnifStrategy.py)
MAJOR_PAIRS = {"BTC", "ETH", "SOL", "XRP"}
LEVERAGE_MAJORS = 5.0
LEVERAGE_DEFAULT = 3.0


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def tg_send(text: str, parse_mode: str | None = "Markdown", reply_markup: dict | None = None):
    """Send a Telegram message, auto-split if too long. Omit parse_mode when None (plain text)."""
    MAX = 4000
    chunks = [text[i : i + MAX] for i in range(0, len(text), MAX)]
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": TG_CHAT,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        # Attach keyboard only to last chunk
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json=payload,
                timeout=15,
            )
        except Exception as e:
            logger.error(f"tg_send error: {e}")


def tg_chat_action(chat_id: str, action: str = "typing") -> None:
    """Telegram sendChatAction (e.g. typing) while the LLM is working."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": action},
            timeout=10,
        )
    except Exception as e:
        logger.debug("tg_chat_action: %s", e)


# Persistent reply keyboard — shown at bottom of chat
KEYBOARD = {
    "keyboard": [
        ["/sygnif", "/overview", "/tendency"],
        ["/signals", "/scan", "/ta BTC", "/btc"],
        ["/plays", "/market", "/movers"],
        ["/deduce", "/ask", "/fa_help"],
        ["/news", "/evaluate", "/btc-specialist"],
        ["/finance-agent network", "/finance-agent network nodes"],
        ["/clear"],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
}


def tg_poll(offset: int) -> tuple[list, int]:
    """Poll Telegram for new messages."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        updates = resp.json().get("result", [])
        for u in updates:
            offset = max(offset, u["update_id"] + 1)
        return updates, offset
    except Exception as e:
        logger.error(f"Poll error: {e}")
        return [], offset


# ---------------------------------------------------------------------------
# Telegram chat memory (context window = recent messages in this chat)
# ---------------------------------------------------------------------------
_chat_histories: dict[str, list[dict[str, str]]] = {}


def _history_trim(chat_id: str) -> None:
    k = str(chat_id)
    hist = _chat_histories.get(k)
    if not hist:
        return
    while len(hist) > TELEGRAM_CHAT_MAX_HISTORY:
        hist.pop(0)
    total = sum(len(m.get("text", "")) for m in hist)
    while hist and total > TELEGRAM_CHAT_MAX_CHARS:
        removed = hist.pop(0)
        total -= len(removed.get("text", ""))


def clear_chat_history(chat_id: str) -> str:
    k = str(chat_id)
    _chat_histories.pop(k, None)
    return "_Chat-Verlauf gelöscht._"


def _format_chat_history(chat_id: str) -> str:
    k = str(chat_id)
    lines: list[str] = []
    for m in _chat_histories.get(k, []):
        label = "Nutzer" if m.get("role") == "user" else "Assistent"
        lines.append(f"{label}: {m.get('text', '')}")
    return "\n".join(lines) if lines else "(noch kein Verlauf in dieser Session)"


def conversational_reply(user_text: str, chat_id: str) -> str:
    """Reply using LLM with prior Telegram messages as context (same chat).

    Uses ``llm_conversational`` → Cursor Cloud with a clean reply (no task UI) when configured;
    optional Ollama multi-turn only if ``LLM_BACKEND=ollama`` or Ollama is the only backend.
    """
    k = str(chat_id)
    reply = llm_conversational(user_text, chat_id, max_tokens=3200)
    if k not in _chat_histories:
        _chat_histories[k] = []
    _chat_histories[k].append({"role": "user", "text": user_text[:8000]})
    _chat_histories[k].append({"role": "assistant", "text": reply[:12000]})
    _history_trim(k)
    return reply


# ---------------------------------------------------------------------------
# Data: Bybit API
# ---------------------------------------------------------------------------
def bybit_tickers() -> list[dict]:
    """Fetch all spot tickers from Bybit."""
    try:
        resp = requests.get(f"{BYBIT}/market/tickers", params={"category": "spot"}, timeout=10)
        return resp.json().get("result", {}).get("list", [])
    except Exception as e:
        logger.error(f"Bybit tickers error: {e}")
        return []


def bybit_kline(symbol: str, interval: str = "60", limit: int = 200) -> pd.DataFrame:
    """Fetch OHLCV from Bybit. interval: 1,3,5,15,30,60,120,240,360,720,D,W."""
    try:
        resp = requests.get(
            f"{BYBIT}/market/kline",
            params={"category": "spot", "symbol": symbol, "interval": interval, "limit": limit},
            timeout=10,
        )
        rows = resp.json().get("result", {}).get("list", [])
        if not rows:
            return pd.DataFrame()
        # Bybit returns [ts, open, high, low, close, volume, turnover] newest first
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
        for c in ["open", "high", "low", "close", "volume", "turnover"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
        df = df.sort_values("ts").reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Bybit kline error: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Data: News (RSS)
# ---------------------------------------------------------------------------
def fetch_news(token: str = "", max_items: int = 7) -> list[str]:
    """Fetch crypto news headlines from RSS feeds."""
    feeds = [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]
    if token:
        feeds.insert(0, f"https://cryptopanic.com/news/{token.lower()}/rss/")

    headlines = []
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "").strip()
                source = feed.feed.get("title", url.split("/")[2])
                if title:
                    headlines.append(f"{title} — _{source}_")
        except Exception:
            continue
    # Deduplicate by title prefix
    seen = set()
    unique = []
    for h in headlines:
        key = h[:40].lower()
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique[:max_items]


# ---------------------------------------------------------------------------
# Pure-pandas indicator helpers
# ---------------------------------------------------------------------------
def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _aroon(high: pd.Series, low: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series]:
    aroonu = high.rolling(period + 1).apply(lambda x: x.argmax(), raw=True) / period * 100
    aroond = low.rolling(period + 1).apply(lambda x: x.argmin(), raw=True) / period * 100
    return aroonu, aroond


def _stochrsi(close: pd.Series, period: int = 14) -> pd.Series:
    rsi = _rsi(close, period)
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    rng = rsi_max - rsi_min
    return ((rsi - rsi_min) / rng.replace(0, np.nan) * 100).rolling(3).mean()


def _cmf(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series, period: int = 20) -> pd.Series:
    rng = high - low
    mfv = ((close - low) - (high - close)) / rng.replace(0, np.nan)
    return (mfv * volume).rolling(period).sum() / volume.rolling(period).sum()


def _willr(high: pd.Series, low: pd.Series, close: pd.Series,
           period: int = 14) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    rng = hh - ll
    return ((hh - close) / rng.replace(0, np.nan)) * -100


def _cci(high: pd.Series, low: pd.Series, close: pd.Series,
         period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma) / (0.015 * mad.replace(0, np.nan))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series,
         period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _mfi_pandas(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14
) -> pd.Series:
    """Money Flow Index (TA-Lib / pandas-ta semantics). Used when pandas_ta is not installed."""
    tp = (high + low + close) / 3.0
    rmf = tp * volume
    d = tp.diff()
    pos = np.where(d > 0, rmf, 0.0)
    neg = np.where(d < 0, rmf, 0.0)
    pos_sum = pd.Series(pos, index=tp.index).rolling(period, min_periods=period).sum()
    neg_sum = pd.Series(neg, index=tp.index).rolling(period, min_periods=period).sum()
    mfr = pos_sum / neg_sum.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + mfr))


def _obv_pandas(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume (cumulative)."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


# ---------------------------------------------------------------------------
# TA: Calculate indicators from OHLCV DataFrame
# ---------------------------------------------------------------------------
def calc_indicators(df: pd.DataFrame) -> dict:
    """Calculate technical indicators matching SygnifStrategy. Returns dict."""
    if len(df) < 50:
        return {}
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # EMAs (strategy set)
    ema9 = close.ewm(span=9).mean()
    ema12 = close.ewm(span=12).mean()
    ema21 = close.ewm(span=21).mean()
    ema26 = close.ewm(span=26).mean()
    ema50 = close.ewm(span=50).mean()
    ema120 = close.ewm(span=120).mean()
    ema200 = close.ewm(span=200).mean() if len(df) >= 200 else pd.Series(dtype=float)

    # RSI (strategy uses 3 + 14)
    rsi14 = _rsi(close, 14)
    rsi3 = _rsi(close, 3)

    # Bollinger Bands 20
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20

    # MACD
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9).mean()
    macd_hist = macd_line - macd_signal

    # Volume SMA (strategy uses 25)
    vol_sma25 = volume.rolling(25).mean()

    # --- Strategy indicators ---
    aroonu, aroond = _aroon(high, low, 14)
    stochrsi_k = _stochrsi(close, 14)
    cmf20 = _cmf(high, low, close, volume, 20)
    willr14 = _willr(high, low, close, 14)
    cci20 = _cci(high, low, close, 20)
    roc9 = close.pct_change(9) * 100
    atr14 = _atr(high, low, close, 14)

    # Volume / flow (SygnifStrategy populate_indicators — ML-dataset style, optional pta)
    if pta is not None:
        try:
            mfi14 = pta.mfi(high, low, close, volume, length=14)
            obv_s = pta.obv(close, volume)
        except Exception as e:
            logger.warning("pandas_ta MFI/OBV failed, using pandas fallback: %s", e)
            mfi14 = _mfi_pandas(high, low, close, volume, 14)
            obv_s = _obv_pandas(close, volume)
    else:
        mfi14 = _mfi_pandas(high, low, close, volume, 14)
        obv_s = _obv_pandas(close, volume)
    obv_chg = obv_s.pct_change() * 100.0

    # Swing Failure (SF) levels — 48-bar S/R
    sf_resistance = high.shift(1).rolling(48).max()
    sf_support = low.shift(1).rolling(48).min()
    sf_resistance_stable = sf_resistance == sf_resistance.shift(1)
    sf_support_stable = sf_support == sf_support.shift(1)
    sf_volatility = ((close - ema120).abs() / ema120)

    last = close.iloc[-1]
    prev = close.iloc[-2]

    result = {
        "price": last,
        "prev_close": prev,
        "change_pct": (last - prev) / prev * 100 if prev else 0,
        # EMAs
        "ema9": ema9.iloc[-1],
        "ema12": ema12.iloc[-1],
        "ema21": ema21.iloc[-1],
        "ema26": ema26.iloc[-1],
        "ema50": ema50.iloc[-1],
        "ema120": ema120.iloc[-1],
        "ema200": ema200.iloc[-1] if len(ema200) > 0 else None,
        # RSI
        "rsi": rsi14.iloc[-1],
        "rsi3": rsi3.iloc[-1],
        # Bollinger
        "bb_upper": bb_upper.iloc[-1],
        "bb_lower": bb_lower.iloc[-1],
        "bb_mid": sma20.iloc[-1],
        # MACD
        "macd": macd_line.iloc[-1],
        "macd_signal": macd_signal.iloc[-1],
        "macd_hist": macd_hist.iloc[-1],
        # Volume
        "volume": volume.iloc[-1],
        "vol_avg": vol_sma25.iloc[-1],
        "vol_ratio": volume.iloc[-1] / vol_sma25.iloc[-1] if vol_sma25.iloc[-1] > 0 else 1.0,
        # Strategy indicators
        "aroonu": aroonu.iloc[-1],
        "aroond": aroond.iloc[-1],
        "stochrsi_k": stochrsi_k.iloc[-1],
        "cmf": cmf20.iloc[-1],
        "willr": willr14.iloc[-1],
        "cci": cci20.iloc[-1],
        "roc9": roc9.iloc[-1],
        "atr": atr14.iloc[-1],
        "atr_pct": (atr14.iloc[-1] / last * 100) if last > 0 else 0,
        "mfi": float(mfi14.iloc[-1]) if pd.notna(mfi14.iloc[-1]) else float("nan"),
        "obv": float(obv_s.iloc[-1]) if pd.notna(obv_s.iloc[-1]) else float("nan"),
        "obv_change_pct": float(obv_chg.iloc[-1]) if pd.notna(obv_chg.iloc[-1]) else float("nan"),
        # Swing failure
        "sf_support": sf_support.iloc[-1],
        "sf_resistance": sf_resistance.iloc[-1],
        "sf_support_stable": bool(sf_support_stable.iloc[-1]),
        "sf_resistance_stable": bool(sf_resistance_stable.iloc[-1]),
        "sf_volatility": sf_volatility.iloc[-1],
        "sf_long": bool(
            low.iloc[-1] <= sf_support.iloc[-1]
            and close.iloc[-1] > sf_support.iloc[-1]
            and sf_support_stable.iloc[-1]
            and sf_volatility.iloc[-1] > 0.03
        ),
        "sf_short": bool(
            high.iloc[-1] >= sf_resistance.iloc[-1]
            and close.iloc[-1] < sf_resistance.iloc[-1]
            and sf_resistance_stable.iloc[-1]
            and sf_volatility.iloc[-1] > 0.03
        ),
        # Legacy keys
        "support": sf_support.iloc[-1],
        "resistance": sf_resistance.iloc[-1],
        "high_24": df.tail(48)["high"].max(),
        "low_24": df.tail(48)["low"].min(),
    }

    # EMA crossover state (9 vs 26 — matches strategy scoring)
    result["ema_bull"] = result["ema9"] > result["ema26"]
    prev_ema9 = ema9.iloc[-2] if len(ema9) >= 2 else result["ema9"]
    prev_ema26 = ema26.iloc[-2] if len(ema26) >= 2 else result["ema26"]
    result["ema_cross"] = result["ema_bull"] and prev_ema9 <= prev_ema26

    # Trend
    if last > ema9.iloc[-1] > ema21.iloc[-1] > ema50.iloc[-1]:
        result["trend"] = "Strong Uptrend"
    elif last > ema21.iloc[-1]:
        result["trend"] = "Uptrend"
    elif last < ema9.iloc[-1] < ema21.iloc[-1] < ema50.iloc[-1]:
        result["trend"] = "Strong Downtrend"
    elif last < ema21.iloc[-1]:
        result["trend"] = "Downtrend"
    else:
        result["trend"] = "Sideways"

    # RSI interpretation
    r = result["rsi"]
    if r > 70:
        result["rsi_signal"] = "Overbought"
    elif r < 30:
        result["rsi_signal"] = "Oversold"
    else:
        result["rsi_signal"] = "Neutral"

    # MACD signal
    if result["macd_hist"] > 0 and macd_hist.iloc[-2] <= 0:
        result["macd_signal_text"] = "Bullish Cross"
    elif result["macd_hist"] < 0 and macd_hist.iloc[-2] >= 0:
        result["macd_signal_text"] = "Bearish Cross"
    elif result["macd_hist"] > 0:
        result["macd_signal_text"] = "Bullish"
    else:
        result["macd_signal_text"] = "Bearish"

    # MFI zones (symmetric to RSI-style labels for /ta display)
    mfi = result.get("mfi", float("nan"))
    if not np.isnan(mfi):
        if mfi > 80:
            result["mfi_signal"] = "Overbought"
        elif mfi < 20:
            result["mfi_signal"] = "Oversold"
        else:
            result["mfi_signal"] = "Neutral"
    else:
        result["mfi_signal"] = "N/A"

    # BB position
    bb_range = result["bb_upper"] - result["bb_lower"]
    if bb_range > 0:
        bb_pct = (last - result["bb_lower"]) / bb_range
        result["bb_position"] = f"{bb_pct:.0%}"
    else:
        result["bb_position"] = "N/A"

    return result


# ---------------------------------------------------------------------------
# Strategy TA score — mirrors _calculate_ta_score_vectorized()
# ---------------------------------------------------------------------------
def calc_ta_score(ind: dict) -> dict:
    """Compute strategy TA score (0-100) from indicator dict.
    Returns {"score": int, "components": {name: int, ...}}."""
    if not ind:
        return {"score": 50, "components": {}}

    components = {}
    score = 50.0

    # RSI_14 component (-15 to +15)
    rsi = ind.get("rsi", 50)
    if rsi < 30:
        c = 15
    elif rsi < 40:
        c = 8
    elif rsi > 70:
        c = -15
    elif rsi > 60:
        c = -8
    else:
        c = 0
    components["rsi14"] = c
    score += c

    # RSI_3 momentum (-10 to +10)
    rsi3 = ind.get("rsi3", 50)
    if rsi3 < 10:
        c = 10
    elif rsi3 < 20:
        c = 5
    elif rsi3 > 90:
        c = -10
    elif rsi3 > 80:
        c = -5
    else:
        c = 0
    components["rsi3"] = c
    score += c

    # EMA crossover (-10 to +10)
    if ind.get("ema_cross"):
        c = 10
    elif ind.get("ema_bull"):
        c = 7
    else:
        c = -7
    components["ema"] = c
    score += c

    # Bollinger (-8 to +8)
    bb_lower = ind.get("bb_lower", 0)
    bb_upper = ind.get("bb_upper", 0)
    price = ind.get("price", 0)
    if bb_lower and price <= bb_lower:
        c = 8
    elif bb_upper and price >= bb_upper:
        c = -8
    else:
        c = 0
    components["bb"] = c
    score += c

    # Aroon (-8 to +8)
    aroonu = ind.get("aroonu", 50)
    aroond = ind.get("aroond", 50)
    if not np.isnan(aroonu) and not np.isnan(aroond):
        if aroonu > 80 and aroond < 30:
            c = 8
        elif aroond > 80 and aroonu < 30:
            c = -8
        else:
            c = 0
    else:
        c = 0
    components["aroon"] = c
    score += c

    # StochRSI (-5 to +5)
    stoch = ind.get("stochrsi_k", 50)
    if not np.isnan(stoch):
        if stoch < 20:
            c = 5
        elif stoch > 80:
            c = -5
        else:
            c = 0
    else:
        c = 0
    components["stochrsi"] = c
    score += c

    # CMF (-5 to +5)
    cmf = ind.get("cmf", 0)
    if not np.isnan(cmf):
        if cmf > 0.15:
            c = 5
        elif cmf < -0.15:
            c = -5
        else:
            c = 0
    else:
        c = 0
    components["cmf"] = c
    score += c

    # Volume ratio (-3 to +3)
    vol_ratio = ind.get("vol_ratio", 1.0)
    if vol_ratio > 1.5 and score > 50:
        c = 3
    elif vol_ratio > 1.5 and score < 50:
        c = -3
    else:
        c = 0
    components["volume"] = c
    score += c

    return {"score": max(0, min(100, int(score))), "components": components}


# ---------------------------------------------------------------------------
# Signal detection — mirrors SygnifStrategy entry/exit conditions
# ---------------------------------------------------------------------------
def detect_signals(ind: dict, ticker: str = "") -> dict:
    """Detect active strategy entry/exit signals from indicators.
    Returns {"entries": [...], "exits": [...], "leverage": float, "atr_pct": float}."""
    if not ind:
        return {"entries": [], "exits": [], "leverage": LEVERAGE_DEFAULT, "atr_pct": 0}

    ta = calc_ta_score(ind)
    score = ta["score"]
    entries = []
    exits = []

    # --- Leverage tier ---
    atr_pct = ind.get("atr_pct", 0)
    if ticker.upper() in MAJOR_PAIRS:
        lev = LEVERAGE_MAJORS
    else:
        lev = LEVERAGE_DEFAULT
    if atr_pct > 3.0:
        lev = min(lev, 2.0)
    elif atr_pct > 2.0:
        lev = min(lev, 3.0)

    vol_ratio = ind.get("vol_ratio", 1.0)

    # --- Entry signals ---
    if score >= 65 and vol_ratio > 1.2:
        entries.append("strong_ta_long")
    if score <= 25:
        entries.append("strong_ta_short")
    if 40 <= score <= 70 and not any("strong" in e for e in entries):
        entries.append("ambiguous_long")
    if 30 <= score <= 60 and not any("strong" in e for e in entries):
        entries.append("ambiguous_short")
    if ind.get("sf_long"):
        entries.append("sf_long")
    if ind.get("sf_short"):
        entries.append("sf_short")

    # --- Exit signals ---
    willr = ind.get("willr", -50)
    if not np.isnan(willr):
        if willr > -5:
            exits.append("willr_overbought")
        if willr < -95:
            exits.append("willr_oversold")

    return {
        "entries": entries,
        "exits": exits,
        "leverage": lev,
        "atr_pct": atr_pct,
        "ta_score": score,
        "ta_components": ta["components"],
    }


def _format_score_label(score: int) -> str:
    if score >= 65:
        return "Bullish"
    elif score <= 35:
        return "Bearish"
    elif score >= 55:
        return "Lean Bullish"
    elif score <= 45:
        return "Lean Bearish"
    return "Neutral"


# ---------------------------------------------------------------------------
# LLM: Cursor Cloud Agents API (primary) or Ollama (fallback)
# ---------------------------------------------------------------------------
def _cursor_auth_header() -> str:
    return "Basic " + base64.b64encode(f"{CURSOR_API_KEY}:".encode()).decode()


def _ollama_llm(prompt: str, max_tokens: int) -> str:
    return _ollama_chat(
        [{"role": "user", "content": prompt}],
        max_tokens,
    )


def _ollama_chat(messages: list[dict], max_tokens: int) -> str:
    """Multi-turn Ollama /api/chat (roles: system, user, assistant)."""
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"num_predict": min(max_tokens, 8192)},
            },
            timeout=180,
        )
        if not resp.ok:
            logger.error(f"Ollama HTTP {resp.status_code}: {resp.text[:500]}")
            return f"_Ollama-Fehler ({resp.status_code}). Läuft `ollama serve`?_"
        data = resp.json()
        msg = data.get("message") or {}
        text = (msg.get("content") or "").strip()
        if text:
            return text
        return "_Ollama: leere Antwort._"
    except Exception as e:
        logger.error(f"Ollama error: {e}")
        return f"_Ollama nicht erreichbar (`{OLLAMA_BASE_URL}`)._\n_{e}_"


def _cursor_format_conversation(messages: list[dict]) -> str:
    assistant_chunks: list[str] = []
    for m in messages:
        t = (m.get("type") or "").lower()
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if "assistant" in t:
            assistant_chunks.append(text)
    if assistant_chunks:
        return "\n\n".join(assistant_chunks)
    return "\n\n".join((m.get("text") or "").strip() for m in messages if (m.get("text") or "").strip())


def _cursor_cloud_llm(
    prompt: str, max_tokens: int, *, for_telegram_chat: bool = False
) -> str:
    """POST /v0/agents @ api.cursor.com — Sygnif Agent LLM (aligned with Cloud Agent worker).

    If ``for_telegram_chat`` is True, return only assistant text (fluent chat); otherwise
    include task status and link (slash / agent tools).
    """
    _ = max_tokens
    if for_telegram_chat:
        wrapped = (
            "[Sygnif Finance Agent — Telegram chat. Output ONLY your reply to the user. "
            "No task links, no 'I will browse/search', no repository or PR discussion unless asked. "
            "Natural, fluent dialogue.]\n\n"
            + prompt
        )
    else:
        wrapped = (
            "[Sygnif Finance Agent — reply in conversation only; no PR unless asked.]\n\n" + prompt
        )
    body: dict = {
        "prompt": {"text": wrapped},
        "source": {"repository": CURSOR_AGENT_REPOSITORY, "ref": CURSOR_AGENT_REF},
        "target": {"autoCreatePr": False},
    }
    if CURSOR_AGENT_MODEL:
        body["model"] = CURSOR_AGENT_MODEL
    try:
        r = requests.post(
            f"{CURSOR_API_BASE}/v0/agents",
            headers={
                "Authorization": _cursor_auth_header(),
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        if not r.ok:
            return f"_Cursor API {r.status_code}:_{r.text[:800]}_"
        task = r.json()
        task_id = task.get("id")
        if not task_id:
            return "_Cursor: keine Task-ID._"
        url = task.get("target", {}).get("url") or f"https://cursor.com/agents?id={task_id}"
        deadline = time.monotonic() + max(60, CURSOR_AGENT_MAX_WAIT_SEC)
        status = task.get("status", "")
        summary = (task.get("summary") or "").strip()
        while time.monotonic() < deadline:
            if status in ("FINISHED", "FAILED", "CANCELLED"):
                break
            time.sleep(10)
            gr = requests.get(
                f"{CURSOR_API_BASE}/v0/agents/{task_id}",
                headers={"Authorization": _cursor_auth_header()},
                timeout=60,
            )
            if not gr.ok:
                logger.error(f"Cursor get_task {gr.status_code}")
                break
            task = gr.json()
            status = task.get("status", "")
            summary = (task.get("summary") or "").strip()

        cr = requests.get(
            f"{CURSOR_API_BASE}/v0/agents/{task_id}/conversation",
            headers={"Authorization": _cursor_auth_header()},
            timeout=60,
        )
        conv_text = ""
        if cr.ok:
            msgs = cr.json().get("messages") or []
            conv_text = _cursor_format_conversation(msgs)
        if for_telegram_chat:
            text_out = (conv_text or "").strip()
            if text_out:
                return text_out
            if summary and status == "FINISHED" and summary.strip():
                return summary.strip()
            if status == "FAILED":
                return f"_Cursor Task fehlgeschlagen._ [Task]({url})"
            return (
                f"_Noch keine Antwort (Status: {status})._ "
                f"Erhöhe ggf. `CURSOR_AGENT_MAX_WAIT_SEC` oder später erneut fragen.\n[Task]({url})"
            )
        parts = [f"*Sygnif (Cursor Cloud)* — `{status}`", f"[Task]({url})"]
        if summary:
            parts.append(f"*Summary:*\n{summary}")
        if conv_text:
            parts.append(conv_text)
        elif status == "FAILED":
            parts.append("_Task FAILED._")
        else:
            parts.append("_Noch keine Antwort — später erneut oder CURSOR_AGENT_MAX_WAIT_SEC erhöhen._")
        return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"Cursor cloud LLM: {e}")
        return f"_Cursor Cloud Fehler:_ `{e}`"


def llm_conversational(user_text: str, chat_id: str, *, max_tokens: int = 3200) -> str:
    """Free-text / multi-turn path: Cursor chat-style reply, or Ollama multi-turn if configured."""
    backend = os.environ.get("LLM_BACKEND", "").strip().lower()
    if backend == "none":
        return "_LLM aus (`LLM_BACKEND=none`)._"

    k = str(chat_id)
    prior = _format_chat_history(k)
    prompt = (
        f"{CONVERSATIONAL_SYSTEM}\n\n"
        f"--- bisheriger Verlauf (alt → neu) ---\n{prior}\n\n"
        f"--- neueste Nutzernachricht ---\n{user_text}"
    )

    if backend == "ollama":
        if not OLLAMA_MODEL:
            return "_OLLAMA_MODEL fehlt._"
        msgs: list[dict] = [{"role": "system", "content": CONVERSATIONAL_SYSTEM}]
        for m in _chat_histories.get(k, []):
            r = m.get("role", "")
            if r not in ("user", "assistant"):
                continue
            msgs.append({"role": r, "content": m.get("text", "")})
        msgs.append({"role": "user", "content": user_text})
        return _ollama_chat(msgs, max_tokens)

    if backend in ("cursor", "cursor_cloud"):
        if not CURSOR_API_KEY or not CURSOR_AGENT_REPOSITORY:
            return "_CURSOR_API_KEY + CURSOR_AGENT_REPOSITORY in .env (cursor.com/settings)._"
        return _cursor_cloud_llm(prompt, max_tokens, for_telegram_chat=True)

    if CURSOR_API_KEY and CURSOR_AGENT_REPOSITORY:
        return _cursor_cloud_llm(prompt, max_tokens, for_telegram_chat=True)
    if OLLAMA_MODEL:
        msgs = [{"role": "system", "content": CONVERSATIONAL_SYSTEM}]
        for m in _chat_histories.get(k, []):
            r = m.get("role", "")
            if r not in ("user", "assistant"):
                continue
            msgs.append({"role": r, "content": m.get("text", "")})
        msgs.append({"role": "user", "content": user_text})
        return _ollama_chat(msgs, max_tokens)

    return (
        "_Kein LLM._ Setze `CURSOR_API_KEY` + `CURSOR_AGENT_REPOSITORY` (Cursor Cloud Agent). "
        "Optional: `OLLAMA_MODEL` + `ollama serve` oder `LLM_BACKEND=ollama`."
    )


def llm_analyze(prompt: str, max_tokens: int = 1500) -> str:
    """Primary: Cursor Cloud Agents API. Fallback: Ollama. `LLM_BACKEND=ollama|none`."""
    backend = os.environ.get("LLM_BACKEND", "").strip().lower()
    if backend == "none":
        return "_LLM aus (`LLM_BACKEND=none`)._"

    if backend == "ollama":
        if not OLLAMA_MODEL:
            return "_OLLAMA_MODEL fehlt._"
        return _ollama_llm(prompt, max_tokens)

    if backend in ("cursor", "cursor_cloud"):
        if not CURSOR_API_KEY or not CURSOR_AGENT_REPOSITORY:
            return "_CURSOR_API_KEY + CURSOR_AGENT_REPOSITORY in .env (cursor.com/settings)._"
        return _cursor_cloud_llm(prompt, max_tokens, for_telegram_chat=False)

    # Default: Cloud if configured (Sygnif Agent = same stack as worker), else Ollama
    if CURSOR_API_KEY and CURSOR_AGENT_REPOSITORY:
        return _cursor_cloud_llm(prompt, max_tokens, for_telegram_chat=False)
    if OLLAMA_MODEL:
        return _ollama_llm(prompt, max_tokens)

    return (
        "_Kein LLM._ Primär: `CURSOR_API_KEY` + `CURSOR_AGENT_REPOSITORY` (Cursor Cloud Agent). "
        "Optional: `OLLAMA_MODEL` + `ollama serve`."
    )


def _llm_available() -> bool:
    """True if llm_analyze can reach Cursor Cloud or Ollama (not stub / none)."""
    backend = os.environ.get("LLM_BACKEND", "").strip().lower()
    if backend == "none":
        return False
    if backend == "ollama":
        return bool(OLLAMA_MODEL)
    if backend in ("cursor", "cursor_cloud"):
        return bool(CURSOR_API_KEY and CURSOR_AGENT_REPOSITORY)
    if CURSOR_API_KEY and CURSOR_AGENT_REPOSITORY:
        return True
    if OLLAMA_MODEL:
        return True
    return False


def _format_slash_no_llm(raw: str, ctx: str) -> str:
    """Telegram reply when no LLM: show gathered context instead of only an error line."""
    head = (
        "_Kein LLM aktiv_ (`LLM_BACKEND=none` oder Cursor/Ollama nicht konfiguriert). "
        "**Rohdaten** statt Zusammenfassung:\n\n"
    )
    body = (ctx or "").strip()
    max_len = 3800
    if len(body) > max_len:
        body = body[:max_len].rstrip() + "\n\n_(gekürzt)_"
    return head + body + f"\n\n_Befehl:_ `{raw}`"


# ---------------------------------------------------------------------------
# Pair filtering helper
# ---------------------------------------------------------------------------
_STABLECOIN_EXCLUDE = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP", "USDS", "USDE"}


def _filter_pairs(tickers: list[dict], min_turnover: float = 1_000_000) -> list[dict]:
    """Filter USDT pairs, exclude stablecoins and leveraged tokens."""
    pairs = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym.replace("USDT", "")
        if base in _STABLECOIN_EXCLUDE or any(x in base for x in ("2L", "3L", "5L", "2S", "3S", "5S")):
            continue
        try:
            price = float(t.get("lastPrice", 0))
            change = float(t.get("price24hPcnt", 0)) * 100
            turnover = float(t.get("turnover24h", 0))
        except (ValueError, TypeError):
            continue
        if turnover < min_turnover:
            continue
        pairs.append({"sym": base, "price": price, "change": change, "vol": turnover})
    return pairs


def _fmt_price(price: float) -> str:
    if price >= 100:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.5f}"


# ---------------------------------------------------------------------------
# Command: /tendency — Market tendency (bull/bear)
# ---------------------------------------------------------------------------
def cmd_tendency() -> str:
    """Market tendency: TA scan + finance-agent expert (headlines + rules)."""
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."

    # BTC + ETH + top 3 alts by volume
    core_syms = ["BTCUSDT", "ETHUSDT"]
    pairs = _filter_pairs(tickers, min_turnover=5_000_000)
    top_alts = [p for p in sorted(pairs, key=lambda x: x["vol"], reverse=True)
                if p["sym"] not in ("BTC", "ETH")][:3]
    scan_syms = core_syms + [f"{p['sym']}USDT" for p in top_alts]

    bull_count = 0
    bear_count = 0
    total = 0
    lines = ["*Market Tendency*\n"]
    coin_data = []  # compact scan lines
    score_rows = []

    for sym in scan_syms:
        df = bybit_kline(sym, interval="60", limit=200)
        if df.empty:
            continue
        ind = calc_indicators(df)
        if not ind:
            continue
        ta = calc_ta_score(ind)
        sig = detect_signals(ind, sym.replace("USDT", ""))
        score = ta["score"]
        total += 1

        name = sym.replace("USDT", "")
        trend = ind.get("trend", "?")
        rsi = ind.get("rsi", 50)
        willr = ind.get("willr", -50)
        macd = ind.get("macd_signal_text", "?")
        pf = _fmt_price(ind["price"])
        entry = sig["entries"][0] if sig["entries"] else "none"

        if score >= 55:
            bull_count += 1
            icon = "\U0001f7e2"
        elif score <= 45:
            bear_count += 1
            icon = "\U0001f534"
        else:
            icon = "\u26aa"

        lines.append(f"{icon} `{name:>5}` {pf} TA:`{score}` {trend} RSI:`{rsi:.0f}`")
        score_rows.append({"name": name, "score": score})
        coin_data.append(
            f"{name}: ${ind['price']:.4g} {trend} TA:{score} RSI:{rsi:.0f} "
            f"WR:{willr:.0f} MACD:{macd} signal:{entry}"
        )

    lines.append("")

    # Overall verdict
    if total == 0:
        verdict = "\u2753 No data"
    elif bull_count > bear_count and bull_count >= total * 0.6:
        verdict = "\U0001f7e2 *BULLISH* — majority leaning up"
    elif bear_count > bull_count and bear_count >= total * 0.6:
        verdict = "\U0001f534 *BEARISH* — majority leaning down"
    elif bull_count > bear_count:
        verdict = "\U0001f7e1 *LEAN BULLISH* — mixed, tilting up"
    elif bear_count > bull_count:
        verdict = "\U0001f7e1 *LEAN BEARISH* — mixed, tilting down"
    else:
        verdict = "\u26aa *NEUTRAL* — no clear direction"
    lines.append(verdict)

    headlines = fetch_news("", max_items=5)
    avg_for_news = (
        sum(r["score"] for r in score_rows) / len(score_rows) if score_rows else 50.0
    )
    neutral_n = max(0, total - bull_count - bear_count)
    insight = expert_tendency_insight(
        bull_count, bear_count, neutral_n, headlines, avg_for_news
    )
    lines.append(f"\n\U0001f9e0 *Agent insight (expert):*\n{insight}")

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /market — Top crypto overview
# ---------------------------------------------------------------------------
def cmd_market() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch market data."

    pairs = _filter_pairs(tickers)
    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:15]

    lines = ["*Crypto Market Overview*\n"]
    for p in top:
        arrow = "+" if p["change"] >= 0 else ""
        vol_m = p["vol"] / 1e6
        pf = _fmt_price(p["price"])
        lines.append(f"`{p['sym']:>6}` {pf:>12} `{arrow}{p['change']:.1f}%` Vol `${vol_m:.0f}M`")

    dl = _defillama_telegram_slow_context()
    if dl:
        lines.append("")
        lines.append(dl)

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /movers — Top gainers & losers
# ---------------------------------------------------------------------------
def cmd_movers() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."

    pairs = _filter_pairs(tickers, min_turnover=500_000)
    by_change = sorted(pairs, key=lambda x: x["change"], reverse=True)
    gainers = by_change[:5]
    losers = by_change[-5:][::-1]

    lines = ["*Top Movers (24h)*\n"]
    lines.append("*Gainers:*")
    for i, g in enumerate(gainers, 1):
        lines.append(f"  {i}. `{g['sym']}` +{g['change']:.1f}% (${g['price']:.4g})")
    lines.append("\n*Losers:*")
    for i, l in enumerate(losers, 1):
        lines.append(f"  {i}. `{l['sym']}` {l['change']:.1f}% (${l['price']:.4g})")

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_")
    return "\n".join(lines)


def _fdn_telegram_btc_fundamentals_block() -> str:
    """Optional FinancialData.net BTC profile (Standard tier); empty if no key / error."""
    try:
        from fdn_fundamentals import format_telegram_btc_fundamentals_block
    except ImportError:
        return ""
    return format_telegram_btc_fundamentals_block()


def _fdn_telegram_btc_fundamentals_one_line() -> str:
    try:
        from fdn_fundamentals import format_telegram_btc_fundamentals_one_line
    except ImportError:
        return ""
    return format_telegram_btc_fundamentals_one_line()


def _fdn_telegram_briefing_equity_proxy() -> str:
    """Optional equity proxy (e.g. MSFT) when FDN_BRIEFING_EQUITY_PROXY is set."""
    proxy = os.environ.get("FDN_BRIEFING_EQUITY_PROXY", "").strip()
    if not proxy:
        return ""
    try:
        from fdn_fundamentals import format_telegram_equity_proxy_line
    except ImportError:
        return ""
    return format_telegram_equity_proxy_line(proxy)


def _newhedge_telegram_altcoins_correlation_block() -> str:
    """Optional NewHedge BTC–altcoin correlation line (Telegram only; not in HTTP ``/briefing`` pipe)."""
    try:
        from newhedge_client import format_telegram_altcoins_correlation_block
    except ImportError:
        return ""
    return format_telegram_altcoins_correlation_block()


def _defillama_telegram_slow_context() -> str:
    """Optional DefiLlama DeFi TVL + agg perp OI (hourly cache by default; disable with DEFILLAMA_CONTEXT=0)."""
    try:
        from defillama_client import format_telegram_slow_context

        return (format_telegram_slow_context() or "").strip()
    except Exception as e:
        logger.debug("defillama telegram context: %s", e)
        return ""


def _defillama_overseer_plain() -> str:
    """Same DefiLlama bundle as one line for overseer / plays JSON (plaintext)."""
    try:
        from defillama_client import format_plaintext_for_overseer

        return (format_plaintext_for_overseer() or "").strip()
    except Exception as e:
        logger.debug("defillama overseer line: %s", e)
        return ""


def _btc_specialist_snapshot_footer() -> str:
    """One-line bundle freshness (plain text — avoids Telegram Markdown mangling paths with underscores)."""
    manifest = Path(__file__).resolve().parent / "btc_specialist" / "data" / "manifest.json"
    if not manifest.is_file():
        return (
            "Bundle: no manifest — run python3 finance_agent/btc_specialist/scripts/pull_btc_context.py "
            "from repo root."
        )
    try:
        m = json.loads(manifest.read_text(encoding="utf-8"))
        ts = m.get("generated_utc", "?")
        return (
            f"Bundle: manifest.json @ {ts} — data directory finance_agent/btc_specialist/data"
        )
    except Exception:
        return "Bundle: manifest unreadable — re-run pull script."


def cmd_btc() -> str:
    """Telegram /btc — BTC specialist only (offline bundle + optional FDN/NewHedge). Live Sygnif TA: `/ta BTC`."""
    try:
        from btc_specialist import report as _btc_rep

        core = _btc_rep.build_btc_specialist_report(max_chars=4500)
    except Exception as e:
        logger.error("btc_specialist report: %s", e)
        core = f"*BTC specialist*\n\n_Report error:_ `{e}`"
    parts = ["*BTC specialist*", "", core, "", _btc_specialist_snapshot_footer()]
    fdn = _fdn_telegram_btc_fundamentals_block()
    if fdn:
        parts.extend(["", fdn])
    nh = _newhedge_telegram_altcoins_correlation_block()
    if nh:
        parts.extend(["", nh])
    parts.extend(["", "—", "*Live Sygnif TA + signals:* `/ta BTC`"])
    return "\n".join(parts).strip()


def cmd_btc_specialist() -> str:
    """Longer offline bundle (same stack as /btc, larger cap)."""
    try:
        from btc_specialist import report as _btc_rep

        core = _btc_rep.build_btc_specialist_report(max_chars=9000)
    except Exception as e:
        logger.error("btc_specialist deep report: %s", e)
        core = f"*BTC specialist (deep)*\n\n_Report error:_ `{e}`"
    parts = ["*BTC specialist (deep)*", "", core, "", _btc_specialist_snapshot_footer()]
    fdn = _fdn_telegram_btc_fundamentals_block()
    if fdn:
        parts.extend(["", fdn])
    nh = _newhedge_telegram_altcoins_correlation_block()
    if nh:
        parts.extend(["", nh])
    parts.extend(["", "—", "*Live Sygnif TA + signals:* `/ta BTC`"])
    return "\n".join(parts).strip()


def cmd_briefing() -> str:
    """Telegram / finance-agent: HTTP-parity pipe body + optional FDN lines + HTTP hint + BTC specialist footer.

    Raw `GET /briefing` stays pipe-only for overseer/LLM consumers; Telegram adds fundamentals when configured.
    Optional NewHedge BTC–alts correlation line when ``NEWHEDGE_API_KEY`` is set (same as ``/btc``).
    """
    body = _briefing(None)
    host = FINANCE_AGENT_HTTP_HOST
    port = FINANCE_AGENT_HTTP_PORT
    fdn_bits = [_fdn_telegram_btc_fundamentals_one_line(), _fdn_telegram_briefing_equity_proxy()]
    fdn_block = "\n".join(x for x in fdn_bits if x).strip()
    fdn_section = f"{fdn_block}\n\n" if fdn_block else ""
    nh = _newhedge_telegram_altcoins_correlation_block()
    nh_section = f"{nh}\n\n" if nh else ""
    cmd_md_block = ""
    try:
        import crypto_market_data as _cmd_md2

        b = _cmd_md2.get_bundle_cached()
        if b:
            pretty = _cmd_md2.format_bundle_text(b, max_chars=1200).strip()
            if pretty:
                cmd_md_block = f"{pretty}\n\n"
    except Exception as e:
        logger.debug("briefing telegram crypto_market_data: %s", e)
    return (
        f"*Briefing* (pipe lines = `GET /briefing` contract)\n\n"
        f"```\n{body}\n```\n\n"
        f"{fdn_section}"
        f"{nh_section}"
        f"{cmd_md_block}"
        f"_HTTP:_ `http://{host}:{port}/briefing?symbols=BTC,ETH`\n"
        f"{_btc_specialist_snapshot_footer()}\n"
        f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
    ).strip()


# ---------------------------------------------------------------------------
# Command: /ta <TICKER> — Technical analysis + strategy signals
# ---------------------------------------------------------------------------
def cmd_ta(ticker: str) -> str:
    ticker = ticker.upper().strip() or "BTC"
    symbol = f"{ticker}USDT"

    df = bybit_kline(symbol, interval="60", limit=200)
    if df.empty:
        return f"No data for `{ticker}`. Check ticker symbol."

    ind = calc_indicators(df)
    if not ind:
        return f"Not enough data for `{ticker}`."

    sig = detect_signals(ind, ticker)
    p = ind["price"]
    pf = f"${p:,.2f}" if p >= 1 else f"${p:.6f}"

    ema200_line = ""
    if ind["ema200"] is not None:
        e200 = ind["ema200"]
        ema200_line = f"  EMA 200: `{e200:.4g}` {'(above)' if p > e200 else '(below)'}\n"

    # Strategy signals section
    score = sig["ta_score"]
    label = _format_score_label(score)
    entry_str = ", ".join(sig["entries"]) if sig["entries"] else "None"
    exit_str = ", ".join(sig["exits"]) if sig["exits"] else "None"

    # Score breakdown
    comps = sig["ta_components"]
    comp_parts = [f"{k}({v:+d})" for k, v in comps.items() if v != 0]
    comp_str = " ".join(comp_parts) if comp_parts else "all neutral"

    sf_status = ""
    if ind.get("sf_long"):
        sf_status = "SF Long active"
    elif ind.get("sf_short"):
        sf_status = "SF Short active"
    else:
        sf_status = "No active pattern"

    mf = ind.get("mfi")
    if mf is None or (isinstance(mf, float) and np.isnan(mf)):
        mfi_line = "  MFI(14): `N/A`\n"
    else:
        mfi_line = f"  MFI(14): `{mf:.1f}` — {ind.get('mfi_signal', '')}\n"
    obd = ind.get("obv_change_pct")
    if obd is None or (isinstance(obd, float) and np.isnan(obd)):
        obv_line = "  OBV Δ: `N/A`\n"
    else:
        obv_line = f"  OBV Δ: `{obd:+.2f}%` (cumulative OBV in strategy)\n"

    msg = (
        f"*Technical Analysis: {ticker}*\n"
        f"*Price:* `{pf}`\n\n"
        f"*Strategy Signals:*\n"
        f"  TA Score: `{score}/100` ({label})\n"
        f"  Entry: `{entry_str}`\n"
        f"  Exit: `{exit_str}`\n"
        f"  Leverage: `{sig['leverage']:.0f}x` (ATR {sig['atr_pct']:.1f}%)\n"
        f"  Swing Failure: {sf_status}\n"
        f"  Score: `{comp_str}`\n\n"
        f"*Trend:* `{ind['trend']}`\n"
        f"*EMAs:*\n"
        f"  EMA 9: `{ind['ema9']:.4g}`\n"
        f"  EMA 21: `{ind['ema21']:.4g}`\n"
        f"  EMA 50: `{ind['ema50']:.4g}`\n"
        f"{ema200_line}\n"
        f"*RSI:* `{ind['rsi']:.1f}` — {ind['rsi_signal']} | RSI3: `{ind['rsi3']:.0f}`\n"
        f"*MACD:* `{ind['macd']:.4g}` — {ind['macd_signal_text']}\n\n"
        f"*Oscillators:*\n"
        f"  Williams %R: `{ind['willr']:.0f}`\n"
        f"  StochRSI: `{ind['stochrsi_k']:.0f}`\n"
        f"  CCI: `{ind['cci']:.0f}` | CMF: `{ind['cmf']:.3f}`\n"
        f"{mfi_line}"
        f"{obv_line}"
        f"  Aroon U/D: `{ind['aroonu']:.0f}/{ind['aroond']:.0f}`\n\n"
        f"*Bollinger:* `{ind['bb_position']}` "
        f"(`{ind['bb_lower']:.4g}` — `{ind['bb_upper']:.4g}`)\n\n"
        f"*Levels:*\n"
        f"  Support: `{ind['support']:.4g}` | Resistance: `{ind['resistance']:.4g}`\n\n"
        f"*Volume:* `{ind['volume']:,.0f}` ({ind['vol_ratio']:.1f}x avg)\n"
        f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')} · 1h candles_"
    )
    return msg


# ---------------------------------------------------------------------------
# Command: /research <TICKER> — TA + news (finance-agent expert, no LLM)
# ---------------------------------------------------------------------------
def cmd_research(ticker: str) -> str:
    ticker = ticker.upper().strip() or "BTC"
    symbol = f"{ticker}USDT"

    # 1. Fetch market data
    df = bybit_kline(symbol, interval="60", limit=200)
    ind = calc_indicators(df) if not df.empty else {}
    sig = detect_signals(ind, ticker)

    # 2. Fetch news
    headlines = fetch_news(ticker)

    # 3. Price from tickers
    tickers = bybit_tickers()
    pair_data = next((t for t in tickers if t.get("symbol") == symbol), {})
    price = float(pair_data.get("lastPrice", 0))
    change_24h = float(pair_data.get("price24hPcnt", 0)) * 100
    vol_24h = float(pair_data.get("turnover24h", 0))

    analysis = expert_research_markdown(
        ticker, ind or {}, sig, price, change_24h, vol_24h, headlines
    )

    msg = (
        f"*Research Report: {ticker}*\n"
        f"*Price:* `${price:.4g}` (`{change_24h:+.1f}%` 24h)\n"
        f"*TA Score:* `{sig['ta_score']}/100` | Signals: `{', '.join(sig['entries']) or 'None'}`\n\n"
        f"{analysis}\n\n"
        f"_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"
    )
    return msg


# ---------------------------------------------------------------------------
# Command: /plays — Strategy-aligned opportunities (finance-agent expert)
# ---------------------------------------------------------------------------
def cmd_plays() -> str:

    # Gather market context
    tickers = bybit_tickers()
    pairs = []
    exclude = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP", "USDS", "USDE"}
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym.replace("USDT", "")
        if base in exclude or any(x in base for x in ("2L", "3L", "5L", "2S", "3S", "5S")):
            continue
        try:
            change = float(t.get("price24hPcnt", 0)) * 100
            turnover = float(t.get("turnover24h", 0))
            price = float(t.get("lastPrice", 0))
        except (ValueError, TypeError):
            continue
        if turnover < 1_000_000:
            continue
        pairs.append({"sym": base, "price": price, "change": change, "vol": turnover})

    top_by_vol = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:10]
    top_gainers = sorted(pairs, key=lambda x: x["change"], reverse=True)[:5]
    top_losers = sorted(pairs, key=lambda x: x["change"])[:5]

    # Enrich top pairs with TA scores
    market_ctx = "Top by volume (with strategy TA score):\n"
    for p in top_by_vol:
        df = bybit_kline(f"{p['sym']}USDT", "60", 200)
        ind = calc_indicators(df) if not df.empty else {}
        sig = detect_signals(ind, p["sym"])
        p["_ta_score"] = sig["ta_score"]
        p["_entries"] = list(sig.get("entries") or [])
        p["_ind"] = ind
        p["_sig"] = sig
        signal_str = sig["entries"][0] if sig["entries"] else "no_signal"
        market_ctx += (
            f"  {p['sym']}: ${p['price']:.4g} ({p['change']:+.1f}%) "
            f"vol ${p['vol']/1e6:.0f}M | TA:{sig['ta_score']} {signal_str} "
            f"| Lev:{sig['leverage']:.0f}x\n"
        )
    market_ctx += "\nTop gainers:\n"
    for p in top_gainers:
        market_ctx += f"  {p['sym']}: +{p['change']:.1f}%\n"
    market_ctx += "\nTop losers:\n"
    for p in top_losers:
        market_ctx += f"  {p['sym']}: {p['change']:.1f}%\n"

    dl_plain = _defillama_overseer_plain()
    if dl_plain:
        market_ctx += f"\nDeFi / perp OI (DefiLlama, slow context): {dl_plain}\n"

    # Fetch BTC TA for macro context
    btc_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ind = calc_indicators(btc_df) if not btc_df.empty else {}
    btc_sig = detect_signals(btc_ind, "BTC")
    btc_ctx = ""
    if btc_ind:
        btc_ctx = (
            f"\nBTC Context: ${btc_ind['price']:,.0f}, {btc_ind['trend']}, "
            f"RSI {btc_ind['rsi']:.0f}, MACD {btc_ind['macd_signal_text']}, "
            f"TA Score: {btc_sig['ta_score']}/100"
        )

    analysis = expert_plays_from_scan(
        top_by_vol, btc_ind, btc_sig, not btc_df.empty
    )
    if btc_ctx:
        analysis = f"{btc_ctx}\n\n{analysis}"

    # Save plays for trade overseer
    try:
        requests.post(
            f"{OVERSEER_URL}/plays",
            json={"raw_text": analysis, "market_context": market_ctx},
            timeout=3,
        )
    except Exception:
        pass  # Overseer may not be running

    return f"*Investment Plays*\n\n{analysis}\n\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"


# ---------------------------------------------------------------------------
# Command: /signals — Quick scan: active entry signals across top pairs
# ---------------------------------------------------------------------------
def cmd_signals() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."

    pairs = _filter_pairs(tickers, min_turnover=2_000_000)

    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:12]

    longs = []
    shorts = []
    ambiguous = []

    for p in top:
        df = bybit_kline(f"{p['sym']}USDT", "60", 200)
        ind = calc_indicators(df) if not df.empty else {}
        if not ind:
            continue
        sig = detect_signals(ind, p["sym"])
        score = sig["ta_score"]
        entries = sig["entries"]

        row = f"  `{p['sym']:>5}` TA:`{score}` "

        if "strong_ta_long" in entries or "sf_long" in entries:
            detail = f"RSI:{ind['rsi']:.0f} vol:{ind['vol_ratio']:.1f}x lev:{sig['leverage']:.0f}x"
            sig_name = "strong_ta" if "strong_ta_long" in entries else "sf_long"
            longs.append(row + f"`{sig_name}` ({detail})")
        elif "strong_ta_short" in entries or "sf_short" in entries:
            detail = f"RSI:{ind['rsi']:.0f} lev:{sig['leverage']:.0f}x"
            sig_name = "strong_ta_short" if "strong_ta_short" in entries else "sf_short"
            shorts.append(row + f"`{sig_name}` ({detail})")
        elif "ambiguous_long" in entries or "ambiguous_short" in entries:
            zone = "40-70" if "ambiguous_long" in entries else "30-60"
            ambiguous.append(row + f"ambiguous ({zone})")

    lines = ["*Active Strategy Signals*\n"]
    if longs:
        lines.append("LONG:")
        lines.extend(longs)
    if shorts:
        lines.append("\nSHORT:")
        lines.extend(shorts)
    if ambiguous:
        lines.append("\nAMBIGUOUS (sentiment / expert zone):")
        lines.extend(ambiguous)
    if not longs and not shorts and not ambiguous:
        lines.append("_No active signals across top pairs._")

    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')} · 1h candles_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /scan — Opportunity scanner (TA + news + expert ranking)
# ---------------------------------------------------------------------------
def cmd_scan() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."

    pairs = _filter_pairs(tickers, min_turnover=2_000_000)
    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:15]

    # 1. Compute TA + signals for all pairs
    signal_pairs = []
    for p in top:
        df = bybit_kline(f"{p['sym']}USDT", "60", 200)
        if df.empty:
            continue
        ind = calc_indicators(df)
        if not ind:
            continue
        sig = detect_signals(ind, p["sym"])
        entries = sig["entries"]
        # Skip pairs with no actionable signal
        if not entries or entries == ["ambiguous_short"]:
            continue
        signal_pairs.append({
            "sym": p["sym"], "ind": ind, "sig": sig,
            "price": ind["price"], "vol": p["vol"],
        })

    if not signal_pairs:
        return "*Scan* | No active signals across top 15 pairs."

    # 2. Fetch news for top signal pairs (max 6)
    scan_pairs = signal_pairs[:6]
    scan_rows = []
    for sp in scan_pairs:
        sym = sp["sym"]
        ind = sp["ind"]
        sig = sp["sig"]
        entry = sig["entries"][0]

        headlines = fetch_news(sym, max_items=2)
        news_str = headlines[0].split(" — ")[0] if headlines else "No recent news"

        scan_rows.append(
            {
                "sym": sym,
                "price": ind["price"],
                "trend": ind["trend"],
                "ta_score": sig["ta_score"],
                "entry": entry,
                "rsi": ind["rsi"],
                "willr": ind["willr"],
                "lev": sig["leverage"],
                "news_str": news_str,
            }
        )

    ranking = expert_scan_ranking_rows(scan_rows)

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"*Scan* | {now_str}\n"]
    lines.append(ranking or "_No ranked rows._")
    lines.append(f"\n_Scanned {len(top)} pairs, {len(signal_pairs)} with signals_")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /overview — Full trade + market overview (consults overseer)
# ---------------------------------------------------------------------------
OVERSEER_TRADES = f"{OVERSEER_URL}/trades"


def _duration_str(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "--"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def cmd_trades_and_history() -> str:
    """Open trades + Freqtrade profit aggregates (no per-closed-trade list in overseer API)."""
    try:
        resp = requests.get(OVERSEER_TRADES, timeout=12)
        data = resp.json()
        trades = data.get("trades", [])
        profits = data.get("profits", [])
    except Exception as e:
        return f"*Trades & history*\nOverseer unavailable: `{e}`\n_Check `OVERSEER_URL` and trade-overseer._"

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines: list[str] = [f"*Trades & history* | _{now_str}_\n"]

    lines.append("*History (closed — Freqtrade `/profit` aggregates)*")
    if not profits:
        lines.append("_No profit summary from overseer._\n")
    else:
        for p in profits:
            inst = (p.get("instance") or "?").upper()
            pall = float(p.get("profit_all", 0) or 0)
            pcl = float(p.get("profit_closed", 0) or 0)
            wins = int(p.get("winning_trades", 0) or 0)
            losses = int(p.get("losing_trades", 0) or 0)
            n = wins + losses
            wr = f"{wins / n * 100:.0f}%" if n else "—"
            best = (p.get("best_pair") or "—").replace("/USDT", "")
            lines.append(
                f"• `{inst}` P/L all `{pall:+.4f}` USDT | closed `{pcl:+.4f}` | "
                f"W/L {wins}/{losses} ({wr}) | best `{best}`"
            )

    lines.append("\n*Current (open — Freqtrade `status` via overseer)*")
    if not trades:
        lines.append("_No open trades._")
    else:
        spot = [t for t in trades if t.get("instance") == "spot"]
        fut = [t for t in trades if t.get("instance") == "futures"]
        for label, group in (("Spot", spot), ("Futures", fut)):
            if not group:
                continue
            lines.append(f"*{label}* ({len(group)})")
            for t in sorted(group, key=lambda x: float(x.get("profit_pct", 0) or 0), reverse=True):
                pair = (
                    (t.get("pair") or "")
                    .replace("/USDT:USDT", "")
                    .replace("/USDT", "")
                )
                pct = float(t.get("profit_pct", 0) or 0)
                pnl = float(t.get("profit_abs", 0) or 0)
                dur = _duration_str(float(t.get("trade_duration", 0) or 0))
                tag = (t.get("enter_tag") or "")[:18]
                tid = t.get("trade_id", "?")
                em = "\U0001f7e2" if pct >= 0 else "\U0001f534"
                # Code span — legacy Markdown _{tag}_ breaks on underscores (claude_s*, strong_ta).
                extra = f" `{tag}`" if tag else ""
                lines.append(f"{em} `{pair}` `{pct:+.1f}%` ({pnl:+.4f}) {dur} id={tid}{extra}")

    lines.append(
        "\n_Per closed-trade log: Freqtrade UI or `user_data/tradesv3*.sqlite` — "
        "overseer only forwards open list + `/profit` totals._"
    )
    return "\n".join(lines)


def cmd_overview() -> str:
    # 1. Fetch trades + profits from overseer
    try:
        resp = requests.get(OVERSEER_TRADES, timeout=10)
        data = resp.json()
        trades = data.get("trades", [])
        profits = data.get("profits", [])
    except Exception as e:
        return f"Overseer unavailable: {e}"

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"*SYGNIF OVERVIEW*", f"_{now_str}_\n"]

    # 2. Profit summary per instance
    for p in profits:
        inst = p.get("instance", "?")
        total = p.get("profit_all", 0)
        wins = p.get("winning_trades", 0)
        losses = p.get("losing_trades", 0)
        total_closed = wins + losses
        wr = f"{wins / total_closed * 100:.0f}%" if total_closed else "--"
        lines.append(f"*{inst.upper()}:* P/L `{total:+.4f}` | W/L {wins}/{losses} ({wr})")

    # 3. Open trades with TA context
    if trades:
        # Get TA for traded symbols
        trade_syms = list({
            t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
            for t in trades
        })
        ta_map = {}
        for sym in trade_syms:
            df = bybit_kline(f"{sym}USDT", "60", 200)
            if not df.empty:
                ind = calc_indicators(df)
                if ind:
                    sig = detect_signals(ind, sym)
                    ta_map[sym] = {"ind": ind, "sig": sig}

        # Group by instance
        spot = [t for t in trades if t["instance"] == "spot"]
        futures = [t for t in trades if t["instance"] == "futures"]

        for label, group in [("Spot", spot), ("Futures", futures)]:
            if not group:
                continue
            lines.append(f"\n*{label} ({len(group)}):*")
            for t in sorted(group, key=lambda x: x["profit_pct"], reverse=True):
                pair = t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
                pct = t["profit_pct"]
                pnl = t["profit_abs"]
                dur = _duration_str(t["trade_duration"])
                tag = (t.get("enter_tag") or "")[:14]
                emoji = "\U0001f7e2" if pct >= 0 else "\U0001f534"

                line = f"{emoji} *{pair}* `{pct:+.1f}%` ({pnl:+.4f}) {dur}"
                if tag:
                    line += f" `{tag}`"
                lines.append(line)

                # TA context
                ctx = ta_map.get(pair)
                if ctx:
                    s = ctx["sig"]
                    i = ctx["ind"]
                    entry = s["entries"][0] if s["entries"] else ""
                    exit_s = s["exits"][0] if s["exits"] else ""
                    parts = [f"TA:{s['ta_score']}"]
                    if entry:
                        parts.append(entry)
                    if exit_s:
                        parts.append(f"EXIT:{exit_s}")
                    parts.append(f"RSI:{i['rsi']:.0f}")
                    parts.append(f"WR:{i['willr']:.0f}")
                    lines.append(f"    `{' '.join(parts)}`")

        total_unreal = sum(t["profit_abs"] for t in trades)
        lines.append(f"\n*Unrealized:* `{total_unreal:+.4f}` USDT")
    else:
        lines.append("\n_No open trades_")

    # 4. Market tendency (BTC + ETH)
    lines.append("\n*Market:*")
    for sym_name in ["BTC", "ETH"]:
        df = bybit_kline(f"{sym_name}USDT", "60", 200)
        if df.empty:
            continue
        ind = calc_indicators(df)
        if not ind:
            continue
        ta = calc_ta_score(ind)
        pf = _fmt_price(ind["price"])
        trend = ind["trend"]
        if ta["score"] >= 55:
            icon = "\U0001f7e2"
        elif ta["score"] <= 45:
            icon = "\U0001f534"
        else:
            icon = "\u26aa"
        lines.append(f"  {icon} {sym_name} {pf} TA:`{ta['score']}` {trend}")

    # 5. Bybit API health — check rate limits
    try:
        resp = requests.get(
            f"{BYBIT}/market/tickers",
            params={"category": "spot", "symbol": "BTCUSDT"},
            timeout=5,
        )
        rl_remaining = resp.headers.get("X-Bapi-Limit-Status", "?")
        rl_limit = resp.headers.get("X-Bapi-Limit", "?")
        rl_reset = resp.headers.get("X-Bapi-Limit-Reset-Timestamp", "")
        status_code = resp.status_code

        if status_code == 200:
            lines.append(f"\n*API:* `{rl_remaining}/{rl_limit}` calls left")
            # Warn if low
            try:
                remaining = int(rl_remaining)
                limit = int(rl_limit)
                if remaining < limit * 0.2:
                    lines.append(f"  \u26a0\ufe0f Rate limit low!")
            except (ValueError, TypeError):
                pass
        else:
            lines.append(f"\n*API:* \u26a0\ufe0f Status {status_code}")
    except Exception:
        lines.append(f"\n*API:* \u26a0\ufe0f Bybit unreachable")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /news — Latest headlines
# ---------------------------------------------------------------------------
def cmd_news(ticker: str = "") -> str:
    headlines = fetch_news(ticker.upper().strip() if ticker else "")
    if not headlines:
        return "Could not fetch news."
    title = f"*Crypto News*" + (f" ({ticker.upper()})" if ticker else "")
    lines = [title, ""]
    for i, h in enumerate(headlines, 1):
        lines.append(f"{i}. {h}")
    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /deduce — structured deductive reasoning (local LLM)
# ---------------------------------------------------------------------------
def cmd_deduce(args: str) -> str:
    """Premises → intermediate steps → conclusion; Sygnif / market aware."""
    raw = (args or "").strip()
    if not raw:
        return (
            "*Deduktiv*\n\n"
            "Nutze: `/deduce <These oder Frage>`\n"
            "Beispiel: `/deduce Wenn BTC die Woche bullisch bleibt, welche Alts passen zu Sygnif swing tags?`"
        )
    prompt = f"""You are a trading research assistant for Sygnif (Freqtrade, Bybit, TA score 0–100, tags like strong_ta, sygnif_s*, swing_failure, sygnif_swing; legacy fa_* / claude_* in DB).

Task: answer using explicit DEDUCTIVE reasoning.

Format your answer EXACTLY with these sections (use the headings):
1) **Primissen** — facts/assumptions you rely on (bullet list)
2) **Schritte** — numbered logical steps (if A and B then C)
3) **Fazit** — one tight conclusion
4) **Unsicherheiten** — what could invalidate the chain (bullets)

User question (may be German or English):
{raw}

Keep total under ~900 words. Prefer precise terms over hype. No generic disclaimers."""
    return llm_analyze(prompt, max_tokens=2500)


# ---------------------------------------------------------------------------
# Command: /ask and /chat — free-form LLM dialogue (stateless turns)
# ---------------------------------------------------------------------------
def cmd_ask(args: str, chat_id: str | None = None) -> str:
    """Open-ended chat with LLM; same memory as plain-text messages (Telegram-Verlauf)."""
    raw = (args or "").strip()
    cid = str(chat_id) if chat_id is not None else str(TG_CHAT)
    if not raw:
        return (
            "*Freier Chat (Cursor Cloud Agent)*\n\n"
            "• Nachricht **ohne** `/` schreiben — Kontext = bisheriger Chat.\n"
            "• Oder `/ask` / `/chat` mit Text.\n"
            "• `/clear` — Verlauf löschen.\n"
            f"• Limit: ca. {TELEGRAM_CHAT_MAX_HISTORY} Nachrichten / {TELEGRAM_CHAT_MAX_CHARS} Zeichen (Session, bei Bot-Neustart leer).\n"
            "• Optional: `TELEGRAM_CONVERSATIONAL_PARSE_MODE=markdown` wenn du Markdown willst (Standard: plain)."
        )
    return conversational_reply(raw, cid)


# ---------------------------------------------------------------------------
# Command: /fa_help
# ---------------------------------------------------------------------------
def cmd_help() -> str:
    return (
        "*Sygnif Finance Agent*\n\n"
        "`/sygnif` / `/cursor` — **Zyklus:** Cursor-Worker + Overseer + Strategie-Adaptation + Signals + Tendency + Macro (ein Kontext für das LLM)\n"
        "`/sygnif state` — letzter Observer-Snapshot (`advisor_state.json`, kein LLM)\n"
        "`/sygnif pending` — Warteschlange für Live-Overrides (`advisor_pending.json`)\n"
        "`/sygnif approve <id>` — freigegebene Overrides → `strategy_adaptation.json` (Hot-Reload)\n"
        "`/sygnif analytics` — Nur Runtime-Overrides (`strategy_adaptation.json`)\n"
        "`/finance-agent cycle` — gleicher Rohdaten-Bundle wie `/sygnif`\n"
        "`/finance-agent` — Comprehensive research\n"
        "`/finance-agent briefing` — Pipe briefing (same as HTTP `GET /briefing`; Telegram adds optional FDN lines)\n"
        "`/finance-agent crypto-daily` — Crypto Market Data: täglicher README-Report (`crypto_market_data_daily_analysis.md`)\n"
        "`/finance-agent network` — [Giansn/Network](https://github.com/Giansn/Network) submodule (short + nodes/NN summary)\n"
        "`/finance-agent network nodes` — topology + `run_npu` / `placement` / `wire_tensor` + optional `NETWORK_NN_STATUS_URL`\n"
        "`/finance-agent network docs` — doc index + SSM hint\n"
        "`/finance-agent trades` / `check` — open positions + closed P/L aggregates (overseer)\n"
        "`/finance-agent <cmd>` — Run specific module\n"
        "`/finance-agent <TICKER>` — Research for ticker\n"
        "`/overview` — Trades + TA + market (full dashboard)\n"
        "`/tendency` — Market tendency (bull/bear)\n"
        "`/signals` — Active entry signals (top pairs)\n"
        "`/scan` — Deep scan: signals + news + AI ranking\n"
        "`/market` — Top 15 overview + optional DefiLlama DeFi context (slow, ≤1h cache; off: `DEFILLAMA_CONTEXT=0`)\n"
        "`/macro` — BTC TA + breadth + same optional DefiLlama block\n"
        "`/movers` — Gainers & losers (24h)\n"
        "`/ta BTC` — TA + strategy signals\n"
        "`/btc` — BTC specialist bundle (`btc_specialist/report.py`) + optional FDN + optional NewHedge; live TA: `/ta BTC`\n"
        "`/btc-specialist` — same stack as `/btc`, longer report (higher char cap)\n"
        "`/finance-agent crypto-daily` — Crypto Market Data README-Tagesreport (Datei; Cron: `run_crypto_market_data_daily.py`)\n"
        "`/research ETH` — Full AI research report\n"
        "`/plays` — AI investment plays\n"
        "`/news` — Latest crypto headlines\n"
        "`/deduce <text>` — Deductive chain (premises → steps → conclusion)\n"
        "`/ask` / `/chat` — LLM mit *Chat-Verlauf* (wie Freitext ohne `/`)\n"
        "`/clear` — Chat-Verlauf löschen\n"
        "`/evaluate` — Force trade evaluation\n"
        "`/fa_help` — This message"
    )


# ---------------------------------------------------------------------------
# Command: /macro — BTC-led macro overlay
# ---------------------------------------------------------------------------
def cmd_macro() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch market data."

    btc_df = bybit_kline("BTCUSDT", "240", 200)
    btc_ind = calc_indicators(btc_df) if not btc_df.empty else {}
    btc_sig = detect_signals(btc_ind, "BTC") if btc_ind else {"ta_score": 50}

    pairs = _filter_pairs(tickers, min_turnover=5_000_000)
    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:20]
    up = sum(1 for p in top if p["change"] > 0)
    breadth = f"{up}/{len(top)} up" if top else "n/a"

    if not btc_ind:
        return f"*Macro*\nBreadth: `{breadth}`\n_BTC data unavailable_"

    ta = btc_sig.get("ta_score", 50)
    regime = "Mixed / transition"
    if ta >= 65:
        regime = "Risk-on bias"
    elif ta <= 35:
        regime = "Risk-off bias"

    lines = [
        "*Macro-Crypto Context*",
        f"BTC: `{_fmt_price(btc_ind['price'])}` | `{btc_ind['trend']}` | TA `{ta}`",
        f"RSI `{btc_ind['rsi']:.0f}` | WR `{btc_ind['willr']:.0f}` | MACD `{btc_ind['macd_signal_text']}`",
        f"Breadth (top vol alts): `{breadth}`",
        f"Regime: *{regime}*",
    ]
    dl = _defillama_telegram_slow_context()
    if dl:
        lines.append("")
        lines.append(dl)
    lines.append(f"\n_{datetime.now(timezone.utc).strftime('%H:%M UTC')}_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command: /finance-agent crypto-daily — README daily analysis file
# ---------------------------------------------------------------------------
def cmd_crypto_market_daily() -> str:
    """Last daily analysis from ErcinDedeoglu/crypto-market-data (all README JSONs); CC BY 4.0."""
    p = Path(__file__).resolve().parent / "btc_specialist" / "data" / "crypto_market_data_daily_analysis.md"
    if not p.is_file():
        return (
            "*Crypto Market Data — tägliche README-Analyse*\n\n"
            "_Noch keine Datei._ Einmal täglich ausführen:\n"
            "`python3 finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py`\n\n"
            "Oder vollständiger BTC-Kontext inkl. gleicher Rohdaten:\n"
            "`python3 finance_agent/btc_specialist/scripts/pull_btc_context.py`\n\n"
            "_Cron (UTC 06:00 Beispiel):_\n"
            "`0 6 * * * cd $HOME && python3 finance_agent/btc_specialist/scripts/run_crypto_market_data_daily.py`"
        )
    try:
        raw = p.read_text(encoding="utf-8")
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except OSError as e:
        return f"*crypto-daily* — read error: `{e}`"
    cap = 3600
    if len(raw) > cap:
        body = raw[:cap].rstrip() + "\n\n…_(Telegram gekürzt — Volltext: `crypto_market_data_daily_analysis.md`)_"
    else:
        body = raw.strip()
    return (
        f"*Crypto Market Data — daily README analysis*\n"
        f"_File mtime:_ `{mtime}` _(CC BY 4.0 — not Sygnif TA)_\n\n"
        f"{body}"
    )


# ---------------------------------------------------------------------------
# Command: /finance-agent — umbrella router
# ---------------------------------------------------------------------------
def cmd_finance_agent(args: str, chat_id: str | None = None) -> str:
    raw = (args or "").strip()
    cid = str(chat_id) if chat_id is not None else str(TG_CHAT)
    if not raw:
        sections = [
            ("Tendency", cmd_tendency()),
            ("Signals", cmd_signals()),
            ("Macro", cmd_macro()),
            ("Top Plays", cmd_plays()),
        ]
        lines = [f"*Finance Agent Comprehensive* | {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"]
        for title, content in sections:
            lines.append(f"*{title}*")
            snippet = content.strip()
            if len(snippet) > 1200:
                snippet = snippet[:1200].rstrip() + "\n... (truncated)"
            lines.append(snippet)
            lines.append("")
        lines.append("*Active Ops Modules:*")
        lines.append("- BTC dependency gating for alts")
        lines.append("- Strategy comparison baseline: `fa_s0`")
        lines.append("- Futures shorts module with squeeze-risk filter")
        return "\n".join(lines).strip()

    parts = raw.split(maxsplit=1)
    sub = parts[0].lower().strip()
    tail = parts[1].strip() if len(parts) > 1 else ""
    if sub == "network":
        return cmd_network_section(tail)
    if sub in ("trades", "check"):
        return cmd_trades_and_history()
    subcommands = {
        "market": lambda: cmd_market(),
        "movers": lambda: cmd_movers(),
        "signals": lambda: cmd_signals(),
        "scan": lambda: cmd_scan(),
        "plays": lambda: cmd_plays(),
        "tendency": lambda: cmd_tendency(),
        "macro": lambda: cmd_macro(),
        "overview": lambda: cmd_overview(),
        "evaluate": lambda: cmd_evaluate(),
        "cycle": lambda: gather_sygnif_cycle(),
        "analytics": lambda: cmd_strategy_analytics(),
        "help": lambda: cmd_help(),
        "deduce": lambda: cmd_deduce(tail),
        "ask": lambda: cmd_ask(tail, cid),
        "chat": lambda: cmd_ask(tail, cid),
        "ta": lambda: cmd_ta(tail or "BTC"),
        "btc": lambda: cmd_btc(),
        "briefing": lambda: cmd_briefing(),
        "research": lambda: cmd_research(tail or "BTC"),
        "btc-specialist": lambda: cmd_btc_specialist(),
        "crypto-daily": lambda: cmd_crypto_market_daily(),
    }
    if sub in subcommands:
        return subcommands[sub]()
    if sub.isalpha() and 2 <= len(sub) <= 10:
        if not tail:
            return cmd_research(sub.upper())
        return (
            f"*Finance agent*\nMehrteilige Eingabe — `{sub}` wird hier *nicht* als Ticker gelesen. "
            f"Bitte `/ask …`, `/finance-agent deduce …`, oder einen festen Unterbefehl (`overview`, `trades`).\n"
            f"Rohtext: `{raw}`"
        )
    return (
        "Unknown /finance-agent command. Use `trades|check|network|network nodes|network docs|overview|cycle|"
        "analytics|market|movers|ta <TICK>|btc|btc-specialist|crypto-daily|briefing|signals|scan|research <TICK>|"
        "plays|tendency|macro|deduce|ask`"
    )


# ---------------------------------------------------------------------------
# Command: /overseer — Trade overseer overview
# ---------------------------------------------------------------------------
def cmd_overseer() -> str:
    try:
        resp = requests.get(f"{OVERSEER_URL}/overview", timeout=5)
        data = resp.json()
        commentary = data.get("last_commentary", "")
        if commentary:
            return commentary
        return f"*Overseer* | {data.get('open_trades', 0)} trades tracked, no recent alerts."
    except Exception as e:
        return f"Overseer unavailable: {e}"


# ---------------------------------------------------------------------------
# Command: /evaluate — Force trade evaluation
# ---------------------------------------------------------------------------
def cmd_evaluate() -> str:
    # 1. Fetch trades from overseer
    try:
        resp = requests.get(OVERSEER_TRADES, timeout=10)
        data = resp.json()
        trades = data.get("trades", [])
        profits = data.get("profits", [])
    except Exception as e:
        return f"Overseer unavailable: {e}"

    if not trades:
        return "*Evaluate* | No open trades."

    # 2. Get TA for each traded symbol
    trade_syms = list({
        t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        for t in trades
    })
    ta_map = {}
    ta_context = []
    for sym in trade_syms:
        df = bybit_kline(f"{sym}USDT", "60", 200)
        if df.empty:
            continue
        ind = calc_indicators(df)
        if not ind:
            continue
        sig = detect_signals(ind, sym)
        ta_map[sym] = {"ind": ind, "sig": sig}
        entry = sig["entries"][0] if sig["entries"] else "none"
        exit_s = sig["exits"][0] if sig["exits"] else "none"
        ta_context.append(
            f"{sym}: ${ind['price']:.4g} {ind['trend']} TA:{sig['ta_score']} "
            f"RSI:{ind['rsi']:.0f} WR:{ind['willr']:.0f} "
            f"MACD:{ind['macd_signal_text']} CMF:{ind['cmf']:.3f} "
            f"S:{ind['support']:.4g} R:{ind['resistance']:.4g} "
            f"signal:{entry} exit:{exit_s}"
        )

    raw = expert_evaluate_lines(trades, ta_map)

    # 5. Parse actions into lookup
    actions = {}
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) >= 2:
            sym = parts[0].replace("[s]", "").replace("[f]", "")
            act = parts[1].upper()
            reason = parts[2] if len(parts) > 2 else ""
            if act in ("HOLD", "TRAIL", "CUT"):
                actions[sym] = {"action": act, "reason": reason}

    # 6. Build Freqtrade-style table
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    sorted_trades = sorted(trades, key=lambda x: x["profit_pct"], reverse=True)

    # P/L totals
    total_pnl = sum(t["profit_abs"] for t in trades)
    spot_trades = [t for t in trades if t["instance"] == "spot"]
    fut_trades = [t for t in trades if t["instance"] == "futures"]

    lines = [f"*Evaluate* | {now_str}\n"]

    # Header
    lines.append("`  # Pair         P/L%   Action  Reason`")
    lines.append("`" + "-" * 50 + "`")

    for t in sorted_trades:
        pair = t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        inst = t["instance"][0]
        tid = t.get("trade_id", "?")
        pct = t["profit_pct"]

        display = f"{pair}" if inst == "s" else f"{pair}(f)"
        act_info = actions.get(pair, {"action": "HOLD", "reason": ""})
        act = act_info["action"]
        reason = act_info["reason"][:20]

        # Action icon
        if act == "CUT":
            icon = "\u2716"
        elif act == "TRAIL":
            icon = "\u2795"
        else:
            icon = "\u2022"

        lines.append(
            f"`{tid:>3} {display:<12} {pct:>+6.1f}%` {icon}`{act:<5}` _{reason}_"
        )

    lines.append("`" + "-" * 50 + "`")
    lines.append(f"`    TOTAL      {total_pnl:>+8.4f} USDT  ({len(trades)} trades)`")

    # Summary counts
    cuts = sum(1 for a in actions.values() if a["action"] == "CUT")
    trails = sum(1 for a in actions.values() if a["action"] == "TRAIL")
    holds = len(trades) - cuts - trails
    if cuts:
        lines.append(f"\n\u2716 *{cuts} CUT* | \u2795 {trails} TRAIL | \u2022 {holds} HOLD")
    else:
        lines.append(f"\n\u2795 {trails} TRAIL | \u2022 {holds} HOLD")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Slash commands: prefer finance-agent `cmd_*` tool output (see TELEGRAM_SLASH_TOOL_FIRST);
# optional LLM summarization for /sygnif, /deduce, /ask, etc.
# ---------------------------------------------------------------------------
_PLAYS_AGENT_HINT = """
(Format-Ziel für /plays: genau 3 umsetzbare Plays im Sygnif-Stil — Entry-Typen: strong_ta, strong_ta_short,
fa_s*/fa_short_s*, swing_failure. Pro Play: Thesis, Entry, TP, SL, Timeframe, Risk.)
"""


def _gather_tendency_for_agent() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."
    core_syms = ["BTCUSDT", "ETHUSDT"]
    pairs = _filter_pairs(tickers, min_turnover=5_000_000)
    top_alts = [p for p in sorted(pairs, key=lambda x: x["vol"], reverse=True)
                if p["sym"] not in ("BTC", "ETH")][:3]
    scan_syms = core_syms + [f"{p['sym']}USDT" for p in top_alts]

    bull_count = 0
    bear_count = 0
    total = 0
    coin_data: list[str] = []
    lines: list[str] = []
    for sym in scan_syms:
        df = bybit_kline(sym, interval="60", limit=200)
        if df.empty:
            continue
        ind = calc_indicators(df)
        if not ind:
            continue
        ta = calc_ta_score(ind)
        sig = detect_signals(ind, sym.replace("USDT", ""))
        score = ta["score"]
        total += 1
        name = sym.replace("USDT", "")
        trend = ind.get("trend", "?")
        rsi = ind.get("rsi", 50)
        willr = ind.get("willr", -50)
        macd = ind.get("macd_signal_text", "?")
        entry = sig["entries"][0] if sig["entries"] else "none"
        if score >= 55:
            bull_count += 1
            icon = "\U0001f7e2"
        elif score <= 45:
            bear_count += 1
            icon = "\U0001f534"
        else:
            icon = "\u26aa"
        lines.append(f"{icon} `{name:>5}` {_fmt_price(ind['price'])} TA:`{score}` {trend} RSI:`{rsi:.0f}`")
        coin_data.append(
            f"{name}: ${ind['price']:.4g} {trend} TA:{score} RSI:{rsi:.0f} "
            f"WR:{willr:.0f} MACD:{macd} signal:{entry}"
        )
    if total == 0:
        verdict = "No data"
    elif bull_count > bear_count and bull_count >= total * 0.6:
        verdict = "BULLISH majority"
    elif bear_count > bull_count and bear_count >= total * 0.6:
        verdict = "BEARISH majority"
    elif bull_count > bear_count:
        verdict = "LEAN BULLISH"
    elif bear_count > bull_count:
        verdict = "LEAN BEARISH"
    else:
        verdict = "NEUTRAL"
    headlines = fetch_news("", max_items=5)
    news_text = "\n".join(f"- {h}" for h in headlines) if headlines else "No recent news."
    data_block = "\n".join(coin_data)
    table = "\n".join(lines)
    return (
        f"Tendency scan (BTC/ETH + top 3 alts by vol)\n"
        f"Rule-of-thumb verdict: {verdict} | bull:{bull_count} bear:{bear_count} "
        f"neutral:{total - bull_count - bear_count} (n={total})\n"
        f"{table}\n\nPer-coin:\n{data_block}\n\nHeadlines:\n{news_text}"
    )


def _gather_research_for_agent(ticker: str) -> str:
    ticker = ticker.upper().strip() or "BTC"
    symbol = f"{ticker}USDT"
    df = bybit_kline(symbol, interval="60", limit=200)
    ind = calc_indicators(df) if not df.empty else {}
    sig = detect_signals(ind, ticker)
    headlines = fetch_news(ticker)
    news_text = "\n".join(f"- {h}" for h in headlines[:5]) if headlines else "No recent news."
    tickers = bybit_tickers()
    pair_data = next((t for t in tickers if t.get("symbol") == symbol), {})
    price = float(pair_data.get("lastPrice", 0))
    change_24h = float(pair_data.get("price24hPcnt", 0)) * 100
    vol_24h = float(pair_data.get("turnover24h", 0))
    ta_summary = "No TA data available."
    strat_summary = ""
    if ind:
        ta_summary = (
            f"Price: ${ind['price']:.4g}, Trend: {ind['trend']}, "
            f"RSI14: {ind['rsi']:.1f} ({ind['rsi_signal']}), RSI3: {ind['rsi3']:.0f}, "
            f"MACD: {ind['macd_signal_text']} (hist: {ind['macd_hist']:.4g}), "
            f"BB position: {ind['bb_position']}, "
            f"Williams%%R: {ind['willr']:.0f}, StochRSI: {ind['stochrsi_k']:.0f}, "
            f"Aroon U/D: {ind['aroonu']:.0f}/{ind['aroond']:.0f}, CMF: {ind['cmf']:.3f}, "
            f"Support: {ind['support']:.4g}, Resistance: {ind['resistance']:.4g}, "
            f"Volume: {ind['vol_ratio']:.1f}x average"
        )
        entry_str = ", ".join(sig["entries"]) if sig["entries"] else "None"
        strat_summary = (
            f"\nSTRATEGY CONTEXT:\n"
            f"- TA Score: {sig['ta_score']}/100 ({_format_score_label(sig['ta_score'])})\n"
            f"- Active Signals: {entry_str}\n"
            f"- Leverage Tier: {sig['leverage']:.0f}x (ATR {sig['atr_pct']:.1f}%)\n"
            f"- Swing Failure: {'SF Long' if ind.get('sf_long') else 'SF Short' if ind.get('sf_short') else 'None'}"
        )
    return (
        f"Research raw data for {ticker}:\n"
        f"Price: ${price:.4g} (24h: {change_24h:+.1f}%) Vol ${vol_24h/1e6:.1f}M\n"
        f"{ta_summary}{strat_summary}\n\nNews:\n{news_text}"
    )


def _gather_plays_for_agent() -> str:
    tickers = bybit_tickers()
    pairs = []
    exclude = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP", "USDS", "USDE"}
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym.replace("USDT", "")
        if base in exclude or any(x in base for x in ("2L", "3L", "5L", "2S", "3S", "5S")):
            continue
        try:
            change = float(t.get("price24hPcnt", 0)) * 100
            turnover = float(t.get("turnover24h", 0))
            price = float(t.get("lastPrice", 0))
        except (ValueError, TypeError):
            continue
        if turnover < 1_000_000:
            continue
        pairs.append({"sym": base, "price": price, "change": change, "vol": turnover})

    top_by_vol = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:10]
    top_gainers = sorted(pairs, key=lambda x: x["change"], reverse=True)[:5]
    top_losers = sorted(pairs, key=lambda x: x["change"])[:5]

    market_ctx = "Top by volume (with strategy TA score):\n"
    for p in top_by_vol:
        df = bybit_kline(f"{p['sym']}USDT", "60", 200)
        ind = calc_indicators(df) if not df.empty else {}
        sig = detect_signals(ind, p["sym"])
        signal_str = sig["entries"][0] if sig["entries"] else "no_signal"
        market_ctx += (
            f"  {p['sym']}: ${p['price']:.4g} ({p['change']:+.1f}%) "
            f"vol ${p['vol']/1e6:.0f}M | TA:{sig['ta_score']} {signal_str} "
            f"| Lev:{sig['leverage']:.0f}x\n"
        )
    market_ctx += "\nTop gainers:\n"
    for p in top_gainers:
        market_ctx += f"  {p['sym']}: +{p['change']:.1f}%\n"
    market_ctx += "\nTop losers:\n"
    for p in top_losers:
        market_ctx += f"  {p['sym']}: {p['change']:.1f}%\n"

    btc_df = bybit_kline("BTCUSDT", "60", 200)
    btc_ind = calc_indicators(btc_df) if not btc_df.empty else {}
    btc_sig = detect_signals(btc_ind, "BTC")
    btc_ctx = ""
    if btc_ind:
        btc_ctx = (
            f"\nBTC Context: ${btc_ind['price']:,.0f}, {btc_ind['trend']}, "
            f"RSI {btc_ind['rsi']:.0f}, MACD {btc_ind['macd_signal_text']}, "
            f"TA Score: {btc_sig['ta_score']}/100"
        )
    return f"{market_ctx}{btc_ctx}{_PLAYS_AGENT_HINT}"


def _gather_scan_for_agent() -> str:
    tickers = bybit_tickers()
    if not tickers:
        return "Failed to fetch data."
    pairs = _filter_pairs(tickers, min_turnover=2_000_000)
    top = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:15]

    signal_pairs = []
    for p in top:
        df = bybit_kline(f"{p['sym']}USDT", "60", 200)
        if df.empty:
            continue
        ind = calc_indicators(df)
        if not ind:
            continue
        sig = detect_signals(ind, p["sym"])
        entries = sig["entries"]
        if not entries or entries == ["ambiguous_short"]:
            continue
        signal_pairs.append({
            "sym": p["sym"], "ind": ind, "sig": sig,
            "price": ind["price"], "vol": p["vol"],
        })

    if not signal_pairs:
        return "No active signals across top 15 pairs (scan)."

    scan_pairs = signal_pairs[:6]
    data_lines = []
    for sp in scan_pairs:
        sym = sp["sym"]
        ind = sp["ind"]
        sig = sp["sig"]
        entry = sig["entries"][0]
        headlines = fetch_news(sym, max_items=2)
        news_str = headlines[0].split(" — ")[0] if headlines else "No recent news"
        data_lines.append(
            f"{sym}: ${ind['price']:.4g} {ind['trend']} "
            f"TA:{sig['ta_score']} {entry} RSI:{ind['rsi']:.0f} "
            f"WR:{ind['willr']:.0f} Lev:{sig['leverage']:.0f}x "
            f"| News: \"{news_str}\""
        )
    data_block = "\n".join(data_lines)
    return (
        f"Deep scan (top 15 vol pairs, up to 6 with signals + news). "
        f"Scanned {len(top)} pairs, {len(signal_pairs)} with signals.\n\n"
        f"Candidates:\n{data_block}\n\n"
        "(Rankiere nach Überzeugung / gib kompakte Empfehlung — eine LLM-Antwort.)"
    )


def _gather_evaluate_for_agent() -> str:
    try:
        resp = requests.get(OVERSEER_TRADES, timeout=10)
        data = resp.json()
        trades = data.get("trades", [])
    except Exception as e:
        return f"Overseer unavailable: {e}"

    if not trades:
        return "No open trades."

    trade_syms = list({
        t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        for t in trades
    })
    ta_context = []
    for sym in trade_syms:
        df = bybit_kline(f"{sym}USDT", "60", 200)
        if df.empty:
            continue
        ind = calc_indicators(df)
        if not ind:
            continue
        sig = detect_signals(ind, sym)
        entry = sig["entries"][0] if sig["entries"] else "none"
        exit_s = sig["exits"][0] if sig["exits"] else "none"
        ta_context.append(
            f"{sym}: ${ind['price']:.4g} {ind['trend']} TA:{sig['ta_score']} "
            f"RSI:{ind['rsi']:.0f} WR:{ind['willr']:.0f} "
            f"MACD:{ind['macd_signal_text']} CMF:{ind['cmf']:.3f} "
            f"S:{ind['support']:.4g} R:{ind['resistance']:.4g} "
            f"signal:{entry} exit:{exit_s}"
        )

    trade_lines = []
    for t in sorted(trades, key=lambda x: x["profit_pct"]):
        pair = t["pair"].replace("/USDT:USDT", "").replace("/USDT", "")
        inst = t["instance"][0]
        tag = t.get("enter_tag", "") or "?"
        trade_lines.append(
            f"{pair}[{inst}] {t['profit_pct']:+.2f}% ${t['current_rate']:.4g} {tag}"
        )

    ta_block = "\n".join(ta_context) if ta_context else "No TA data"
    trades_block = "\n".join(trade_lines)
    return (
        f"Evaluate: HOLD / TRAIL / CUT pro Position sinnvoll einordnen.\n\n"
        f"TA:\n{ta_block}\n\nTrades:\n{trades_block}"
    )


def _gather_finance_agent_for_agent(args: str) -> str:
    raw = (args or "").strip()
    if not raw:
        parts = [
            "=== TENDENCY (raw) ===\n" + _gather_tendency_for_agent(),
            "=== SIGNALS ===\n" + cmd_signals(),
            "=== MACRO ===\n" + cmd_macro(),
            "=== PLAYS (raw) ===\n" + _gather_plays_for_agent(),
        ]
        return (
            "Finance-Agent Gesamtreport — fasse zusammen, priorisiere klare Kernaussagen.\n\n"
            + "\n\n".join(parts)
        )

    parts = raw.split(maxsplit=1)
    sub = parts[0].lower().strip()
    tail = parts[1].strip() if len(parts) > 1 else ""
    if sub == "network":
        return (
            "=== NETWORK SUBMODULE (Sygnif → github.com/Giansn/Network) ===\n"
            + cmd_network_section(tail)
            + "\n=== END NETWORK ==="
        )
    if sub in ("trades", "check"):
        return (
            "=== TRADES OPEN + HISTORY (aggregate) ===\n"
            + cmd_trades_and_history()
            + "\n=== END TRADES ==="
        )
    if sub == "market":
        return cmd_market()
    if sub == "movers":
        return cmd_movers()
    if sub == "signals":
        return cmd_signals()
    if sub == "scan":
        return _gather_scan_for_agent()
    if sub == "plays":
        return _gather_plays_for_agent()
    if sub == "tendency":
        return _gather_tendency_for_agent()
    if sub == "macro":
        return cmd_macro()
    if sub == "overview":
        return cmd_overview()
    if sub == "evaluate":
        return _gather_evaluate_for_agent()
    if sub == "cycle":
        return gather_sygnif_cycle()
    if sub == "analytics":
        return cmd_strategy_analytics()
    if sub == "help":
        return cmd_help()
    if sub == "deduce":
        return _gather_slash_context("/deduce", tail, f"/finance-agent deduce {tail}")
    if sub in ("ask", "chat"):
        return _gather_slash_context(f"/{sub}", tail, raw)
    if sub == "ta":
        return cmd_ta(tail or "BTC")
    if sub == "btc":
        return cmd_btc()
    if sub == "briefing":
        return cmd_briefing()
    if sub == "research":
        return _gather_research_for_agent(tail or "BTC")
    if sub == "btc-specialist":
        return cmd_btc_specialist()
    if sub in ("crypto-daily", "cryptodaily", "crypto_md_daily"):
        return (
            "=== CRYPTO MARKET DATA — DAILY README ANALYSIS (file) ===\n"
            + cmd_crypto_market_daily()
            + "\n=== END CRYPTO-DAILY ==="
        )
    if sub.isalpha() and 2 <= len(sub) <= 10:
        if not tail:
            return _gather_research_for_agent(sub.upper())
        return (
            "Ad-hoc /finance-agent request (first token is not a known subcommand; "
            "do not treat it as a spot ticker):\n"
            f"{raw}"
        )
    return (
        f"Unknown /finance-agent subcommand: {raw} (try `trades`, `check`, `network`, "
        "`crypto-daily`, `network nodes`, `overview`)"
    )


def _gather_slash_context(cmd: str, args: str, raw: str) -> str:
    try:
        if cmd == "/market":
            return cmd_market()
        if cmd == "/movers":
            return cmd_movers()
        if cmd == "/ta":
            return cmd_ta(args or "BTC")
        if cmd == "/btc":
            return cmd_btc()
        if cmd == "/btc-specialist":
            return cmd_btc_specialist()
        if cmd == "/news":
            return cmd_news(args)
        if cmd == "/signals":
            return cmd_signals()
        if cmd == "/overview":
            return cmd_overview()
        if cmd == "/macro":
            return cmd_macro()
        if cmd == "/overseer":
            return cmd_overseer()
        if cmd == "/fa_help":
            return cmd_help()
        if cmd == "/tendency":
            return _gather_tendency_for_agent()
        if cmd == "/research":
            return _gather_research_for_agent(args or "BTC")
        if cmd == "/plays":
            return _gather_plays_for_agent()
        if cmd == "/scan":
            return _gather_scan_for_agent()
        if cmd == "/evaluate":
            return _gather_evaluate_for_agent()
        if cmd == "/deduce":
            a = (args or "").strip()
            if not a:
                return "(Nutzer: /deduce ohne Text — kurz erklären: `/deduce <Frage>`)"
            return (
                "Deduktive Aufgabe (Sygnif: Freqtrade, Bybit, TA 0–100, Tags wie strong_ta, fa_s*, swing_failure):\n"
                f"{a}"
            )
        if cmd in ("/ask", "/chat"):
            a = (args or "").strip()
            if not a:
                return (
                    "(Nutzer: /ask oder /chat ohne Text — erkläre Freitext ohne Slash, "
                    f"/ask mit Text, /clear; Limit ~{TELEGRAM_CHAT_MAX_HISTORY} Nachrichten.)"
                )
            return f"(Beantworte als fortgesetzten Dialog; Nutzerfrage:)\n{a}"
        if cmd == "/finance-agent":
            return _gather_finance_agent_for_agent(args)
        if cmd in ("/sygnif", "/cursor"):
            # Single entry point: overseer + strategy analytics + signals + tendency (+ Cursor worker health).
            a = (args or "").strip()
            if not a:
                return gather_sygnif_cycle()
            sub = a.split(maxsplit=1)[0].lower()
            rest = a.split(maxsplit=1)[1].strip() if " " in a else ""
            if sub == "analytics":
                return cmd_strategy_analytics()
            if sub == "tendency":
                return _gather_tendency_for_agent()
            if sub == "finance":
                return _gather_finance_agent_for_agent(rest)
            if sub == "signals":
                return cmd_signals()
            if sub == "macro":
                return cmd_macro()
            if sub in ("ask", "chat"):
                return _gather_slash_context(f"/{sub}", rest, raw)
            if sub == "help":
                return (
                    "`/sygnif` — voller Zyklus (Worker + Overseer + Adaptation + Signals + Tendency + Macro).\n"
                    "`/sygnif state|pending|approve <id>` — Observer / Freigabe (ohne LLM; siehe /fa_help).\n"
                    "`/sygnif analytics` — nur `strategy_adaptation.json`.\n"
                    "`/sygnif tendency|signals|macro|finance [args]` — Teilmodul.\n"
                    "`/cursor` — Alias wie `/sygnif`."
                )
            return gather_sygnif_cycle()
        if cmd == "/clear":
            return "(Intern: Verlauf geleert.)"
    except Exception as e:
        logger.error(f"_gather_slash_context {cmd}: {traceback.format_exc()}")
        return f"Fehler beim Laden der Daten: {e}"
    return f"(Unbekannter Befehl {cmd} — keine Rohdaten; ggf. /fa_help.)"


def _telegram_slash_tool_first_enabled() -> bool:
    v = (os.environ.get("TELEGRAM_SLASH_TOOL_FIRST") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _slash_tools_first_body(cmd: str, args: str, raw: str, chat_id: str) -> str | None:
    """Return deterministic finance-agent Telegram body, or None → LLM / gather path."""
    cmd_n = cmd.lower().split("@")[0]
    args = args or ""

    if cmd_n == "/finance-agent":
        rest = args.strip()
        if not rest:
            return cmd_finance_agent("", chat_id)
        head = rest.split(maxsplit=1)[0].strip().lower()
        if head in ("deduce", "ask", "chat"):
            return None
        return cmd_finance_agent(rest, chat_id)

    if cmd_n in ("/deduce", "/ask", "/chat"):
        return None

    if cmd_n in ("/sygnif", "/cursor"):
        a = args.strip()
        if not a:
            return None
        first, _, rest = a.partition(" ")
        sub = first.lower().split("@")[0]
        if sub in ("ask", "chat", "deduce"):
            return None
        if sub == "help":
            return cmd_help()
        if sub == "analytics":
            return cmd_strategy_analytics()
        if sub == "tendency":
            return cmd_tendency()
        if sub == "signals":
            return cmd_signals()
        if sub == "macro":
            return cmd_macro()
        if sub == "finance":
            rr = rest.strip()
            if not rr:
                return cmd_finance_agent("", chat_id)
            h2 = rr.split(maxsplit=1)[0].strip().lower()
            if h2 in ("deduce", "ask", "chat"):
                return None
            return cmd_finance_agent(rr, chat_id)
        return None

    dispatch: dict[str, object] = {
        "/market": cmd_market,
        "/movers": cmd_movers,
        "/signals": cmd_signals,
        "/overview": cmd_overview,
        "/macro": cmd_macro,
        "/overseer": cmd_overseer,
        "/fa_help": cmd_help,
        "/tendency": cmd_tendency,
        "/plays": cmd_plays,
        "/scan": cmd_scan,
        "/evaluate": cmd_evaluate,
        "/btc": cmd_btc,
        "/btc-specialist": cmd_btc_specialist,
    }
    fn = dispatch.get(cmd_n)
    if fn is not None:
        try:
            return fn()  # type: ignore[misc]
        except Exception as e:
            logger.error("_slash_tools_first_body %s: %s", cmd_n, e)
            return f"_Tool error:_ `{e}`"

    if cmd_n == "/ta":
        try:
            return cmd_ta((args or "BTC").strip() or "BTC")
        except Exception as e:
            logger.error("_slash_tools_first_body /ta: %s", e)
            return f"_Tool error:_ `{e}`"
    if cmd_n == "/news":
        try:
            return cmd_news(args)
        except Exception as e:
            logger.error("_slash_tools_first_body /news: %s", e)
            return f"_Tool error:_ `{e}`"
    if cmd_n == "/research":
        try:
            return cmd_research((args or "BTC").strip() or "BTC")
        except Exception as e:
            logger.error("_slash_tools_first_body /research: %s", e)
            return f"_Tool error:_ `{e}`"
    return None


def agent_generate_slash_reply(full_text: str, chat_id: str) -> str:
    raw = full_text.strip()
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "/clear":
        clear_chat_history(chat_id)
        return "Chat-Verlauf geleert. `/fa_help` zeigt Befehle."

    if cmd in ("/ask", "/chat"):
        a = (args or "").strip()
        if not a:
            return cmd_ask("", chat_id)
        return conversational_reply(a, str(chat_id))
    if cmd == "/deduce":
        return cmd_deduce(args or "")

    if cmd == "/finance-agent":
        rest = (args or "").strip()
        if rest:
            head = rest.split(maxsplit=1)[0].strip().lower()
            tail = rest.split(maxsplit=1)[1].strip() if " " in rest else ""
            if head == "ask":
                if not tail:
                    return cmd_ask("", chat_id)
                return conversational_reply(tail, str(chat_id))
            if head == "chat":
                if not tail:
                    return cmd_ask("", chat_id)
                return conversational_reply(tail, str(chat_id))
            if head == "deduce":
                return cmd_deduce(tail)

    if _telegram_slash_tool_first_enabled():
        direct = _slash_tools_first_body(cmd, args, raw, str(chat_id))
        if direct is not None:
            return direct

    ctx = _gather_slash_context(cmd, args, raw)
    if not _llm_available():
        return _format_slash_no_llm(raw, ctx)

    hist = _format_chat_history(chat_id)
    kb = load_finance_agent_kb(max_chars=24000) if cmd == "/finance-agent" else ""
    kb_block = (
        f"\n\n--- FINANCE_AGENT_CANONICAL_KB (Cursor subagent + skill parity) ---\n{kb}\n--- END_KB ---\n\n"
        if kb
        else ""
    )
    prompt = (
        "Du bist Sygnif Finance Agent (Telegram, Markdown). "
        "Slash-Befehle laufen zentral über denselben LLM-Pfad wie der Cursor-Agent (Cursor Cloud API, sofern konfiguriert). "
        "Erzeuge die *vollständige* Antwort auf den Slash-Befehl — eigenständig formuliert, "
        "mobilfreundlich. Nutze SERVER-KONTEXT nur als Fakten; keine erfundenen Kurse.\n\n"
        f"--- Bisheriger Chat ---\n{hist}\n\n"
        f"--- SERVER-KONTEXT ---\n{ctx}{kb_block}"
        f"--- BEFEHL ---\n{raw}\n\n"
        "Antwort in Markdown. Sprache wie der Nutzer (DE/EN). "
        "Wenn FINANCE_AGENT_CANONICAL_KB gesetzt ist: halte dich an Tag-/Exit-Semantik, Pfade und Telegram-Parität dort; "
        "widerspreche dem KB nicht ohne expliziten Verweis auf aktuelleren Repo-Code."
    )
    return llm_analyze(prompt, max_tokens=2800)


def agent_slash_dispatch(chat_id: str, full_text: str) -> str:
    out = agent_generate_slash_reply(full_text, chat_id)
    parts = full_text.strip().split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    if cmd == "/plays":
        try:
            requests.post(
                f"{OVERSEER_URL}/plays",
                json={"raw_text": out, "market_context": "agent_slash"},
                timeout=3,
            )
        except Exception:
            pass
    k = str(chat_id)
    if k not in _chat_histories:
        _chat_histories[k] = []
    _chat_histories[k].append({"role": "user", "text": full_text.strip()[:8000]})
    _chat_histories[k].append({"role": "assistant", "text": out[:12000]})
    _history_trim(k)
    return out


def handle_command(text: str, chat_id: str) -> tuple[str, object, str] | None:
    """Slash-Befehle: wo möglich deterministisch; sonst LLM (agent_slash_dispatch)."""
    if not text.strip().startswith("/"):
        return None
    stripped = text.strip()
    direct = _try_sygnif_direct(stripped)
    if direct is not None:
        return (
            "📋 Sygnif (direct)",
            lambda _a: direct,
            "",
        )
    return (
        "\U0001f916 Sygnif Agent…",
        lambda _a: agent_slash_dispatch(chat_id, stripped),
        "",
    )


# ---------------------------------------------------------------------------
# HTTP server for overseer integration (:8091)
# ---------------------------------------------------------------------------
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import json as _json


FINANCE_AGENT_HTTP_HOST = os.environ.get("FINANCE_AGENT_HTTP_HOST", "127.0.0.1").strip()
FINANCE_AGENT_HTTP_PORT = int(os.environ.get("FINANCE_AGENT_HTTP_PORT", "8091"))


def sygnif_sentiment_http_handler(data: dict) -> dict:
    """POST /sygnif/sentiment — rule-based score for Freqtrade (no LLM / no Haiku)."""
    token = (data.get("token") or "").strip().upper()
    if not token:
        return {"ok": False, "error": "missing_token"}

    try:
        ta_score = float(data.get("ta_score", 50))
    except (TypeError, ValueError):
        ta_score = 50.0

    headlines = data.get("headlines")
    if headlines is None:
        headlines = fetch_news(token, max_items=7)
    if not isinstance(headlines, list):
        headlines = []

    live_raw = ""
    if data.get("include_live", True):
        try:
            import live_market_snapshot as _lms

            live_raw = (_lms.fetch_finance_agent_market_context(token) or "").strip()
        except Exception as e:
            logger.warning("sygnif_sentiment: live_market_snapshot: %s", e)

    score, reason = expert_sygnif_sentiment_score(token, ta_score, headlines, live_raw)
    return {"ok": True, "score": score, "reason": reason}


def _briefing(symbols: list[str] | None = None) -> str:
    """Return compact market briefing optimized for Plutus-3B consumption.

    Format per line (pipe-delimited for easy 3B parsing):
      BTC $67,200 uptrend|RSI:65 WR:-32 StRSI:45|MACD:bull CMF:+0.12|S:65800 R:68400|TA:72 strong_ta_long 5x
    """
    lines = []
    # Always include BTC + ETH
    core = ["BTCUSDT", "ETHUSDT"]
    extra = [f"{s}USDT" for s in (symbols or []) if f"{s}USDT" not in core]
    for sym in core + extra[:4]:  # max 6 total
        df = bybit_kline(sym, interval="60", limit=200)
        if df.empty:
            continue
        ta = calc_indicators(df)
        if not ta:
            continue
        name = sym.replace("USDT", "")
        sig = detect_signals(ta, name)
        price = ta.get("price", 0)
        if price >= 100:
            pf = f"${price:,.0f}"
        elif price >= 1:
            pf = f"${price:.2f}"
        else:
            pf = f"${price:.5f}"
        trend = ta.get("trend", "?").replace("Strong ", "s-")
        rsi = ta.get("rsi", 0)
        willr = ta.get("willr", -50)
        stochrsi = ta.get("stochrsi_k", 50)
        macd_sig = ta.get("macd_signal_text", "?").lower().replace(" ", "_")
        cmf = ta.get("cmf", 0)
        sup = ta.get("support", 0)
        res = ta.get("resistance", 0)
        entry = sig["entries"][0] if sig["entries"] else "none"
        exit_sig = sig["exits"][0] if sig["exits"] else ""
        exit_part = f" EXIT:{exit_sig}" if exit_sig else ""
        lev = sig["leverage"]

        lines.append(
            f"{name} {pf} {trend}"
            f"|RSI:{rsi:.0f} WR:{willr:.0f} StRSI:{stochrsi:.0f}"
            f"|MACD:{macd_sig} CMF:{cmf:+.2f}"
            f"|S:{sup:.4g} R:{res:.4g}"
            f"|TA:{sig['ta_score']} {entry} {lev:.0f}x{exit_part}"
        )
    core = "\n".join(lines) if lines else "No data"
    try:
        import crypto_market_data as _cmd_md

        pipe = _cmd_md.briefing_lines_plain(max_chars=900).strip()
        if pipe:
            return f"{core}\n\n{pipe}"
    except Exception as e:
        logger.debug("briefing crypto_market_data: %s", e)
    return core


def _build_local_overseer_commentary(prompt: str) -> str:
    """Deterministic trade_overseer lines (no LLM)."""
    lines_out = []
    seen = set()
    for raw in (prompt or "").splitlines():
        m = re.search(r"\b([A-Z0-9]+)\[([sf])\]\s+([+-]?\d+(?:\.\d+)?)%", raw)
        if not m:
            continue
        sym, inst, pct_s = m.group(1), m.group(2), m.group(3)
        key = (sym, inst)
        if key in seen:
            continue
        seen.add(key)
        pct = float(pct_s)
        if pct <= -2.0:
            action, reason = "CUT", "loss beyond tolerance"
        elif pct >= 3.0:
            action, reason = "TRAIL", "lock gains, trend extension"
        else:
            action, reason = "HOLD", "no decisive trigger"
        lines_out.append(f"{sym}{inst} {pct:+.2f}%: {action} — {reason}")

    if not lines_out:
        return "No actionable trades."
    return "\n".join(lines_out[:12])


class _BriefingHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if self.path.startswith("/briefing"):
            syms = None
            if "?" in self.path:
                qs = self.path.split("?", 1)[1]
                for part in qs.split("&"):
                    if part.startswith("symbols="):
                        syms = part.split("=", 1)[1].split(",")
            body = _briefing(syms)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body.encode())
        elif path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        elif path == "/training/status":
            from training_hub import training_status_json_bytes

            body = training_status_json_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/training":
            from training_hub import training_json_bytes, training_page_html

            qs = parse_qs(urlparse(self.path).query)
            fmt = (qs.get("format") or ["json"])[0].lower()
            if fmt == "html":
                body = training_page_html().encode("utf-8")
                ctype = "text/html; charset=utf-8"
            else:
                body = training_json_bytes()
                ctype = "application/json; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        ln = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(ln) if ln > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return

        if path == "/sygnif/sentiment":
            out = sygnif_sentiment_http_handler(data if isinstance(data, dict) else {})
            status = 200 if out.get("ok") else 422
            self._send_json(status, out)
            return

        if path == "/overseer/commentary":
            # Same contract as trade_overseer/llm_client.py → OVERSEER_AGENT_URL (rules-only, no LLM).
            prompt = (data.get("prompt") or "").strip() if isinstance(data, dict) else ""
            if not prompt:
                self._send_json(400, {"ok": False, "error": "missing_prompt", "commentary": ""})
                return
            commentary = _build_local_overseer_commentary(prompt)
            self._send_json(200, {"ok": True, "commentary": commentary})
            return

        self.send_response(404)
        self.end_headers()


def start_finance_agent_http_server(*, block: bool = False) -> None:
    """Listen on FINANCE_AGENT_HTTP_HOST:FINANCE_AGENT_HTTP_PORT (default 127.0.0.1:8091)."""
    addr = (FINANCE_AGENT_HTTP_HOST, FINANCE_AGENT_HTTP_PORT)
    server = HTTPServer(addr, _BriefingHandler)
    if block:
        logger.info("Finance agent HTTP on %s:%s (foreground)", addr[0], addr[1])
        server.serve_forever()
    else:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info("Finance agent HTTP on %s:%s (background thread)", addr[0], addr[1])


def _start_http() -> None:
    """Start briefing HTTP unless skipped or port already bound (e.g. Docker finance-agent)."""
    skip = os.environ.get("FINANCE_AGENT_SKIP_HTTP", "").strip().lower()
    if skip in ("1", "true", "yes", "on"):
        logger.info("Finance agent HTTP disabled (FINANCE_AGENT_SKIP_HTTP=%s).", skip)
        return
    try:
        start_finance_agent_http_server(block=False)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
            logger.warning(
                "Finance agent HTTP not started: %s:%s in use — likely Docker `finance-agent`. "
                "Telegram polling continues; briefing stays on the container.",
                FINANCE_AGENT_HTTP_HOST,
                FINANCE_AGENT_HTTP_PORT,
            )
            return
        raise


def main():
    if not TG_TOKEN:
        print("Set AGENT_BOT_TOKEN (or SYGNIF_HEDGE_BOT_TOKEN / FINANCE_BOT_TOKEN) env var")
        sys.exit(1)
    if not TG_CHAT:
        print("Set AGENT_CHAT_ID or TELEGRAM_CHAT_ID env var")
        sys.exit(1)

    _start_http()
    _start_advisor_background()

    logger.info("Finance Agent started. Polling for commands...")
    tg_send("Finance Agent online.", reply_markup=KEYBOARD)

    offset = 0
    while True:
        try:
            updates, offset = tg_poll(offset)
            for update in updates:
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if text and str(chat_id) == str(TG_CHAT):
                    stripped = (text or "").strip()
                    if not stripped:
                        continue
                    if stripped.startswith("/"):
                        reply = handle_command(text, chat_id)
                        if reply is None:
                            tg_send("_Unbekannter Befehl._ Siehe `/fa_help`", reply_markup=KEYBOARD)
                            continue
                    else:
                        # Freitext: fluent chat (Cursor clean reply + optional plain parse_mode)
                        reply = (
                            "\U0001f4ac …",
                            lambda _a: conversational_reply(stripped, chat_id),
                            "",
                            _conversational_parse_mode(),
                        )
                    if isinstance(reply, tuple):
                        # Slow command: (loading_msg, handler, args) or + parse_mode for final send
                        if len(reply) == 4:
                            loading, handler_fn, handler_args, final_parse_mode = reply
                        else:
                            loading, handler_fn, handler_args = reply
                            final_parse_mode = "Markdown"
                        if TELEGRAM_CHAT_TYPING:
                            tg_chat_action(chat_id)
                        tg_send(loading)
                        try:
                            result = handler_fn(handler_args)
                            tg_send(
                                result,
                                parse_mode=final_parse_mode,
                                reply_markup=KEYBOARD,
                            )
                        except Exception as e:
                            logger.error(f"Slow command error: {traceback.format_exc()}")
                            tg_send(f"Error: {e}", reply_markup=KEYBOARD)
                    else:
                        tg_send(reply, reply_markup=KEYBOARD)
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
