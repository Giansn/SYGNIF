"""Brain-text ingest helper (DLP Phase 1 trade-events hook).

Posts trade events as sensory text to the NeuroLinked brain at
http://127.0.0.1:8889/api/input/text so STDP synapses get a real
varying signal instead of the constant "SWARM_MIXED" feed.

Best-effort: every call swallows exceptions, uses a 1s timeout, and
must NEVER raise into the trader/daemon hot path. If the brain is
down or slow, the trade still completes.

Wired by 2026-05-07 fix from CLAUDE.md §6.1 (trade.open / trade.close
producer hook). Decision_engine.py emits trade.close fills here;
sygnif_neurons.py emits trade.open execute records here.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

_LOG = logging.getLogger("brain_ingest")
_BRAIN_URL = os.environ.get(
    "SYGNIF_BRAIN_TEXT_URL",
    "http://127.0.0.1:8889/api/input/text",
)
_TIMEOUT_S = float(os.environ.get("SYGNIF_BRAIN_TEXT_TIMEOUT", "8.0"))


def post_text(text: str, *, source: str = "trader", tags: list[str] | None = None) -> bool:
    """Fire-and-forget POST. Returns True on 200 OK, False on any failure.

    `source` is informational; the brain server may override it during
    storage based on its own classification rules. We pass ours so the
    audit log on the brain side carries provenance.
    """
    if not text:
        return False
    body = {"text": text, "source": source}
    if tags:
        body["tags"] = list(tags)
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _BRAIN_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        _LOG.debug("brain_ingest POST failed: %s: %s", type(e).__name__, e)
        return False


__all__ = ["post_text"]
