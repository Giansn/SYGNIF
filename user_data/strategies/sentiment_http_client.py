"""
POST Sygnif sentiment to finance-agent HTTP (rule-based expert, no LLM).

Env:
  SYGNIF_SENTIMENT_HTTP_URL — full URL e.g. http://127.0.0.1:8091/sygnif/sentiment
  SYGNIF_SENTIMENT_HTTP_TIMEOUT_SEC — default 240
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def post_sygnif_sentiment(
    url: str,
    token: str,
    current_price: float,
    ta_score: float,
    headlines: list[str],
    *,
    include_live: bool = True,
    timeout: Optional[int] = None,
) -> tuple[bool, Optional[float], str]:
    """
    Returns (ok, score, error_message).
    ok True and score set when finance-agent returns ok + numeric score.
    """
    to = timeout if timeout is not None else int(os.environ.get("SYGNIF_SENTIMENT_HTTP_TIMEOUT_SEC", "240"))
    payload = {
        "token": token,
        "price": current_price,
        "ta_score": ta_score,
        "headlines": headlines,
        "include_live": include_live,
    }
    try:
        r = requests.post(
            url.strip(),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=max(30, to),
        )
        try:
            data = r.json()
        except json.JSONDecodeError:
            return False, None, f"invalid_json_http_{r.status_code}"
        if not isinstance(data, dict):
            return False, None, "invalid_response_shape"
        if not data.get("ok"):
            err = data.get("error") or data.get("message") or r.text[:200]
            return False, None, str(err)
        sc = data.get("score")
        if sc is None:
            return False, None, "missing_score"
        return True, float(sc), ""
    except requests.RequestException as e:
        logger.warning("SYGNIF_SENTIMENT_HTTP request error: %s", e)
        return False, None, str(e)
