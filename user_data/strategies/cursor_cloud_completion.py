"""
Minimal Cursor Cloud Agents API client for Sygnif sentiment (same stack as finance_agent).

Env: CURSOR_API_KEY, CURSOR_AGENT_REPOSITORY, optional CURSOR_AGENT_REF, CURSOR_API_BASE,
     CURSOR_AGENT_MODEL, SENTIMENT_CURSOR_MAX_WAIT_SEC (default 180).
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

CURSOR_API_BASE = os.environ.get("CURSOR_API_BASE", "https://api.cursor.com").rstrip("/")
CURSOR_API_KEY = os.environ.get("CURSOR_API_KEY", "").strip()
CURSOR_AGENT_REPOSITORY = os.environ.get("CURSOR_AGENT_REPOSITORY", "").strip()
CURSOR_AGENT_REF = os.environ.get("CURSOR_AGENT_REF", "main").strip()
CURSOR_AGENT_MODEL = os.environ.get("CURSOR_AGENT_MODEL", "").strip()
SENTIMENT_CURSOR_MAX_WAIT_SEC = int(os.environ.get("SENTIMENT_CURSOR_MAX_WAIT_SEC", "180"))


def _auth_header() -> str:
    return "Basic " + base64.b64encode(f"{CURSOR_API_KEY}:".encode()).decode()


def _format_assistant_text(messages: list[dict]) -> str:
    chunks: list[str] = []
    for m in messages:
        t = (m.get("type") or "").lower()
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if "assistant" in t:
            chunks.append(text)
    if chunks:
        return "\n\n".join(chunks)
    return "\n\n".join((m.get("text") or "").strip() for m in messages if (m.get("text") or "").strip())


def cursor_cloud_completion(
    prompt: str,
    *,
    label: str = "Sygnif sentiment",
) -> Optional[str]:
    """
    Run a one-shot agent task; return assistant text from conversation (for JSON parsing).
    None on misconfiguration, HTTP failure, or timeout.
    """
    if not CURSOR_API_KEY or not CURSOR_AGENT_REPOSITORY:
        return None

    wrapped = (
        f"[{label} — reply with ONLY the requested JSON object, no markdown fence.]\n\n" + prompt
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
                "Authorization": _auth_header(),
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        if not r.ok:
            logger.error(f"Cursor sentiment POST {r.status_code}: {r.text[:500]}")
            return None
        task = r.json()
        task_id = task.get("id")
        if not task_id:
            logger.error("Cursor sentiment: no task id")
            return None

        deadline = time.monotonic() + max(30, SENTIMENT_CURSOR_MAX_WAIT_SEC)
        status = task.get("status", "")
        while time.monotonic() < deadline:
            if status in ("FINISHED", "FAILED", "CANCELLED"):
                break
            time.sleep(5)
            gr = requests.get(
                f"{CURSOR_API_BASE}/v0/agents/{task_id}",
                headers={"Authorization": _auth_header()},
                timeout=60,
            )
            if not gr.ok:
                logger.error(f"Cursor sentiment poll {gr.status_code}")
                break
            task = gr.json()
            status = task.get("status", "")

        if status == "FAILED":
            logger.warning("Cursor sentiment task FAILED")
            return None

        cr = requests.get(
            f"{CURSOR_API_BASE}/v0/agents/{task_id}/conversation",
            headers={"Authorization": _auth_header()},
            timeout=60,
        )
        if not cr.ok:
            logger.error(f"Cursor sentiment conversation {cr.status_code}")
            return None
        msgs = cr.json().get("messages") or []
        text = _format_assistant_text(msgs).strip()
        return text if text else None
    except Exception as e:
        logger.error(f"Cursor sentiment error: {e}")
        return None
