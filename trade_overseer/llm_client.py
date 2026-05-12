"""Overseer commentary client.

Priority:
1) External agent webhook (OVERSEER_AGENT_URL)
2) OpenVINO NPU (SYGNIF_LLM_BACKEND=npu)
3) Anthropic Claude API
4) None (rules-only fallback in overseer)
"""
import logging
import os

import requests
from urllib.parse import urlparse, urljoin

logger = logging.getLogger("overseer.llm")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"
AGENT_URL = os.environ.get("OVERSEER_AGENT_URL", "").strip()
AGENT_TOKEN = os.environ.get("OVERSEER_AGENT_TOKEN", "").strip()
LOCAL_AGENT_ONLY = os.environ.get("SYGNIF_LOCAL_AGENT_ONLY", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

_LLM_BACKENDS_NPU = frozenset({"npu", "openvino", "local_npu"})


def _llm_backend() -> str:
    return os.environ.get("SYGNIF_LLM_BACKEND", "anthropic").strip().lower()


def _llm_disabled() -> bool:
    """Hard-disable all LLM backends (Anthropic/NPU) when explicitly requested."""
    for key in ("SYGNIF_LLM_BACKEND", "LLM_BACKEND"):
        if os.environ.get(key, "").strip().lower() == "none":
            return True
    return False


def _is_local_agent_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    # Docker Desktop + Linux host-gateway mapping use host.docker.internal to reach the host.
    return host in {"127.0.0.1", "localhost", "::1", "host.docker.internal"}


def _looks_like_cloud_misconfig_error(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    return (
        "kein llm" in t
        and "cursor_api_key" in t
        and "cursor_agent_repository" in t
    ) or ("cloud agent" in t and "local model" in t)


SYSTEM_PROMPT = """Freqtrade bot monitor (spot [s] + futures [f], Bybit).
Input: TA briefing lines then trade lines with P&L delta.

TA briefing format (pipe-delimited):
  COIN $price trend|RSI:N WR:N StRSI:N|MACD:dir CMF:N|S:support R:resistance|TA:score signal leverage

Key signals: strong_ta_long (TA>=65+vol), strong_ta_short (TA<=25), sf_long/sf_short (swing failure).
TA score 40-70 = ambiguous (Claude sentiment zone). WR>-5 = overbought exit. WR<-95 = oversold exit.

Output format — one line per flagged (*) trade:
EDGE[f] +3.4% (was +1.8%): TRAIL — RSI 78 WR:-3 overbought, lock +2%.
NIGHT[s] -2.2% (was -1.5%): CUT — TA:28 downtrend, broke S:$0.42.
ADA[s] +0.3% (new): HOLD — TA:62 uptrend, RSI 55 room to run.
FART[f] -2.4% (was -2.1%): CUT — TA:31 RSI 38 weak, no support."""


def evaluate(prompt: str, timeout: int = 30) -> str | None:
    """Send prompt to configured overseer backends in priority order."""
    if _llm_disabled():
        # Still allow OVERSEER_AGENT_URL (local finance-agent / cursor worker), but never cloud LLMs.
        pass
    if AGENT_URL:
        if LOCAL_AGENT_ONLY and not _is_local_agent_url(AGENT_URL):
            logger.warning("Ignoring non-local OVERSEER_AGENT_URL because SYGNIF_LOCAL_AGENT_ONLY=1")
        else:
            try:
                headers = {"content-type": "application/json"}
                if AGENT_TOKEN:
                    headers["authorization"] = f"Bearer {AGENT_TOKEN}"
                resp = requests.post(
                    AGENT_URL,
                    headers=headers,
                    json={"prompt": prompt, "source": "trade_overseer"},
                    timeout=timeout,
                )
                if resp.ok:
                    data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
                    text = data.get("commentary") or data.get("text") or (resp.text or "").strip()
                    if text:
                        text = text.strip()
                        if _looks_like_cloud_misconfig_error(text):
                            logger.warning(
                                "Local agent returned cloud-misconfig hint; suppressing and falling back."
                            )
                        else:
                            return text
                logger.error("Agent endpoint error: %s %s", resp.status_code, resp.text[:120])
            except Exception as e:
                logger.error("Agent endpoint failure: %s", e)

    if _llm_disabled():
        logger.warning("LLM backends disabled (SYGNIF_LLM_BACKEND/LLM_BACKEND=none); skipping Anthropic/NPU")
        return None

    if _llm_backend() in _LLM_BACKENDS_NPU:
        import npu_genai_client

        npu_timeout = max(
            timeout,
            int(os.environ.get("SYGNIF_NPU_MIN_TIMEOUT", "120")),
        )
        full = f"{SYSTEM_PROMPT}\n\n---\n\n{prompt}"
        try:
            out = npu_genai_client.evaluate_combined_prompt(full, timeout=npu_timeout)
            if out:
                return out
        except Exception as e:
            logger.error("NPU GenAI evaluate error: %s", e)

    if not ANTHROPIC_KEY:
        logger.warning(
            "No commentary backend: set OVERSEER_AGENT_URL, or SYGNIF_LLM_BACKEND=npu with model, or ANTHROPIC_API_KEY"
        )
        return None

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 500,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        if resp.ok:
            return resp.json()["content"][0]["text"].strip()
        logger.error("Claude API error: %s %s", resp.status_code, resp.text[:100])
        return None
    except requests.exceptions.Timeout:
        logger.warning("Claude timeout, skipping LLM eval")
        return None
    except Exception as e:
        logger.error("Claude error: %s", e)
        return None


def is_available() -> bool:
    """True if any commentary backend is likely usable."""
    if AGENT_URL:
        if LOCAL_AGENT_ONLY and not _is_local_agent_url(AGENT_URL):
            logger.warning("Non-local OVERSEER_AGENT_URL configured while local-only mode is active.")
        else:
            try:
                headers = {}
                if AGENT_TOKEN:
                    headers["authorization"] = f"Bearer {AGENT_TOKEN}"
                # OVERSEER_AGENT_URL is a POST JSON endpoint; probe /health on same host:port instead.
                parsed = urlparse(AGENT_URL)
                health_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/health")
                resp = requests.get(health_url, headers=headers, timeout=5)
                if resp.status_code < 500:
                    return True
            except Exception:
                pass

    if _llm_disabled():
        return False

    if _llm_backend() in _LLM_BACKENDS_NPU:
        try:
            import npu_genai_client

            if npu_genai_client.is_available():
                return True
        except Exception:
            pass

    if ANTHROPIC_KEY:
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL,
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                timeout=10,
            )
            return resp.ok
        except Exception:
            return False

    return False
