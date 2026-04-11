"""Finance-agent–aligned LLM digest for crypto market daily README (dashboard bundle).

Uses ``llm_analyze`` + ``load_finance_agent_kb`` from ``bot.py`` (same Cursor Cloud Agents
API + ``CURSOR_AGENT_REPOSITORY`` as Telegram ``/finance-agent`` and the same repo the
``cursor-agent-worker`` serves on ``CURSOR_WORKER_HEALTH_URL``, default ``8093/healthz``).

Optional: ``CRYPTO_CONTEXT_LLM=0`` to skip during ``pull_btc_context.py`` for a fast pull.
Optional: ``CRYPTO_CONTEXT_REQUIRE_WORKER=1`` to skip LLM when the private worker is not healthy.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def _ensure_finance_agent_path() -> None:
    fa = Path(__file__).resolve().parent
    s = str(fa)
    if s not in sys.path:
        sys.path.insert(0, s)


def _cursor_worker_healthy() -> bool:
    """True if ``GET CURSOR_WORKER_HEALTH_URL`` returns OK (default ``127.0.0.1:8093/healthz``)."""
    url = (os.environ.get("CURSOR_WORKER_HEALTH_URL") or "http://127.0.0.1:8093/healthz").strip()
    if not url:
        return False
    try:
        r = requests.get(url, timeout=3)
        if not r.ok:
            return False
        ct = (r.headers.get("content-type") or "").lower()
        if "json" in ct:
            body = r.json()
            if isinstance(body, dict) and body.get("status") is not None:
                return str(body.get("status")).lower() == "ok"
        return True
    except Exception:
        return False


def _json_loads_flexible(raw: str) -> list | None:
    text = (raw or "").strip()
    if not text:
        return None
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text):
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def generate_crypto_context_sections_llm(md_path: Path) -> list[dict[str, str]] | None:
    """
    Call Cursor Cloud / Ollama via ``llm_analyze`` with finance-agent KB + daily README.

    When ``CRYPTO_CONTEXT_REQUIRE_WORKER=1``, returns ``None`` if the private worker
    health check fails (so cron can fall back to heuristics while the worker is down).

    Returns ``None`` on failure (caller falls back to heuristic summaries in ``report.py``).
    """
    _ensure_finance_agent_path()
    from bot import llm_analyze, load_finance_agent_kb  # noqa: PLC0415

    worker_ok = _cursor_worker_healthy()
    require_worker = os.environ.get("CRYPTO_CONTEXT_REQUIRE_WORKER", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if require_worker and not worker_ok:
        logger.warning(
            "crypto_context_llm: CURSOR_WORKER_HEALTH_URL not healthy; "
            "skipping LLM (CRYPTO_CONTEXT_REQUIRE_WORKER=1)"
        )
        return None

    md = md_path.read_text(encoding="utf-8").strip()
    if len(md) > 14000:
        md = md[:14000] + "\n\n_(truncated for LLM input)_"

    kb = load_finance_agent_kb(max_chars=8000)
    worker_ctx = ""
    if worker_ok:
        worker_ctx = (
            "## Runtime context (Sygnif)\n"
            "The Sygnif Cursor private worker (`cursor-agent-worker.service`) is healthy at "
            "`CURSOR_WORKER_HEALTH_URL`. This task uses the same Cursor Cloud Agent repository "
            "and finance-agent KB as that worker (`~/SYGNIF`).\n\n"
        )
    prompt = (
        "You are the Sygnif finance-agent (same role and constraints as Telegram /finance-agent).\n\n"
        f"{worker_ctx}"
        "## Knowledge base excerpt\n"
        f"{kb}\n\n"
        "## Daily crypto market snapshot (third-party CC BY 4.0 — NOT Sygnif TA, NOT Bybit OHLC)\n"
        f"{md}\n\n"
        "## Task\n"
        "Return a JSON array ONLY (no markdown fences unless wrapping the whole array). "
        "Each element: {\"title\": string, \"analysis\": string}.\n"
        "One object per `##` section in the snapshot, in order. `title` = the section heading text only (no #).\n"
        "`analysis` = 2–4 sentences of real analysis: synthesize what matters for BTC spot/futures risk; "
        "call out contradictions between metrics; lean risk-on vs risk-off; say what is weak vs actionable. "
        "Do NOT paste raw numbers, bullet lists, or 'Lead:/Supporting:' templates. Prose only.\n"
    )

    raw = llm_analyze(prompt, max_tokens=4500)
    if not raw or str(raw).strip().startswith("_"):
        logger.warning("crypto_context_llm: llm_analyze returned empty or error prefix")
        return None

    arr = _json_loads_flexible(str(raw))
    if not isinstance(arr, list):
        logger.warning("crypto_context_llm: could not parse JSON array from LLM output")
        return None

    out: list[dict[str, str]] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        t = (item.get("title") or "").strip()
        a = (item.get("analysis") or "").strip()
        if t and a:
            out.append({"title": t, "analysis": a})
    return out if out else None
