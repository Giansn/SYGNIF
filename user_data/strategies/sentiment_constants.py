# Shared sentiment prompt prefix for Sygnif strategies.
# Mirrors the Cursor / Claude Code `/finance-agent` skill: one domain (strategy + markets).

import re
from typing import Optional

FINANCE_AGENT_SENTIMENT_INSTRUCTIONS = """You are the Sygnif finance-agent sentiment layer (same scope as the /finance-agent skill for this repo).
Context: Freqtrade on Bybit spot/futures; the Technical analysis score below is the strategy's real 0-100 Sygnif TA. Your JSON score is only a news and narrative adjustment for ambiguous-zone entries — not a full trade plan.
Rules: Ground bias in the headlines and implied macro/regulatory tone; do not invent thresholds, tag names, slot caps, or rules not given in this prompt. If headlines are noise or contradictory, use a score near 0."""

_SENTIMENT_S_TAG_RE = re.compile(
    r"^(sygnif|claude|fa)_(?:short_)?s(-?\d+)$",
    re.IGNORECASE,
)


def sentiment_tag_score_abs(tag: Optional[str]) -> Optional[int]:
    """
    Absolute value of the integer suffix on sentiment entry tags, e.g. sygnif_s-5 → 5,
    sygnif_short_s3 → 3. Returns None if the tag is not this pattern.
    """
    if not tag:
        return None
    m = _SENTIMENT_S_TAG_RE.match(tag.strip())
    if not m:
        return None
    try:
        return abs(int(m.group(2)))
    except ValueError:
        return None
