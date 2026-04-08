#!/usr/bin/env python3
"""
Mode router for finance-agent task dispatch.

Routes tasks into one of:
- futures_long
- futures_short
- spot
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


VALID_MODES = {"futures_long", "futures_short", "spot"}


@dataclass
class RouteDecision:
    mode: str
    reason: str


def _normalize_labels(labels: Iterable[str] | None) -> set[str]:
    if not labels:
        return set()
    return {str(x).strip().lower() for x in labels if str(x).strip()}


def infer_mode(title: str = "", description: str = "", labels: Iterable[str] | None = None) -> RouteDecision:
    """
    Determine execution mode from task metadata.

    Priority:
    1) explicit labels
    2) keyword inference from title/description
    3) safe default to spot
    """
    normalized = _normalize_labels(labels)

    # Explicit labels (highest priority)
    if "futures-short" in normalized or "short" in normalized:
        return RouteDecision(mode="futures_short", reason="label_match:futures-short")
    if "futures-long" in normalized or "long" in normalized:
        return RouteDecision(mode="futures_long", reason="label_match:futures-long")
    if "spot" in normalized:
        return RouteDecision(mode="spot", reason="label_match:spot")

    text = f"{title}\n{description}".lower()

    # Keyword inference
    has_futures = any(k in text for k in ["futures", "perp", "perpetual", "short", "leverage"])
    has_short = any(k in text for k in ["short", "sell", "bearish", "breakdown"])
    has_long = any(k in text for k in ["long", "buy", "bullish", "breakout"])
    has_spot = any(k in text for k in ["spot", "cash market", "long-only"])

    if has_futures and has_short:
        return RouteDecision(mode="futures_short", reason="keyword_match:futures+short")
    if has_futures and has_long:
        return RouteDecision(mode="futures_long", reason="keyword_match:futures+long")
    if has_spot:
        return RouteDecision(mode="spot", reason="keyword_match:spot")
    if has_futures:
        # Conservative default for generic futures tasks.
        return RouteDecision(mode="futures_long", reason="keyword_match:futures_default")

    # Final conservative fallback.
    return RouteDecision(mode="spot", reason="default:spot")


def validate_mode(mode: str) -> bool:
    return mode in VALID_MODES
