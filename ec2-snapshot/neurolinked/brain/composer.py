"""brain/composer.py — DLP Phase 2.A response composer + template library.

Takes a salience label + state context, fills a matching template, returns
grounded English. Mirrors the DLP §6.2 response_composer.compose() spec.

Strict-mode slot fill: missing required slot raises MissingSlot;
missing optional slot drops that line. No silent fallback strings.

Use:
    from brain.composer import compose
    text = compose(label="routine", ctx={...})
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


class MissingSlot(KeyError):
    pass


class NoTemplate(LookupError):
    pass


# --- template library -------------------------------------------------------
_TEMPLATES: list[dict] = [
    {
        "id":             "routine_NORMAL_v1",
        "salience":       "routine",
        "regime_filter":  ("NORMAL", "UNKNOWN", "RANGE"),
        "lines": [
            "Box held {minutes_in_regime}min. BTC ${btc_last:,.0f}, "
            "{btc_24h:+.2%} 24h, regime {regime}.",
            "{open_count}/{max_open} legs open, net uPnL ${total_unrealized_usdc:+.2f}, "
            "equity ${equity_usdc:,.0f}.",
            ("?", "ATM IV {iv_atm_pct:.1%}, IV/RV {iv_rv:.2f}, "
                  "implied 1d ±${implied_1d_move_usd:,.0f}."),
        ],
        "required_slots": ("regime", "btc_last", "btc_24h",
                            "open_count", "max_open",
                            "total_unrealized_usdc", "equity_usdc",
                            "minutes_in_regime"),
    },
    {
        "id":             "alert_TREND_FLIP_v1",
        "salience":       "trend_change",
        "regime_filter":  ("ANY",),
        "lines": [
            "Regime flip {prev_regime} → {new_regime} at {ts_utc:%H:%M}Z.",
            ("?", "BTC ${btc_last:,.0f}, {btc_24h:+.2%} 24h."),
            ("?", "{open_count}/{max_open} legs, "
                  "{open_at_risk_count} within {at_risk_bps_threshold}bps of a short strike."),
        ],
        "required_slots": ("prev_regime", "new_regime", "ts_utc"),
    },
    {
        "id":             "risk_event_v1",
        "salience":       "risk_event",
        "regime_filter":  ("ANY",),
        "lines": [
            "Risk: {leg_symbol} {bps_to_strike}bps from short strike "
            "(was {start_bps_to_strike} at open).",
            ("?", "Theta no longer beating delta — consider {recommended_action}."),
            ("?", "Days to expiry: {dte_days}, current premium ${current_premium:.2f}."),
        ],
        "required_slots": ("leg_symbol", "bps_to_strike", "start_bps_to_strike"),
    },
    {
        "id":             "explain_open_v1",
        "salience":       "explain",
        "regime_filter":  ("ANY",),
        "lines": [
            "Opened {structure} on {expiry}. Thesis: {thesis}.",
            ("?", "Strikes via doctrine: {rule_chain}."),
            ("?", "Risk capped at ${max_loss:.2f}."),
            ("?", "Past similar: {n_similar} (avg ${avg_pnl:+.2f})."),
        ],
        "required_slots": ("structure", "expiry", "thesis"),
    },
    {
        "id":             "outcome_close_v1",
        "salience":       "win",  # also matched for "loss"
        "regime_filter":  ("ANY",),
        "lines": [
            "Closed {label} at ${pnl:+.2f}.",
            ("?", "Reason: {reason}."),
            ("?", "Hold time: {hold_min}min."),
            ("?", "Win rate this week: {weekly_win_rate:.0%} ({weekly_n} trades)."),
        ],
        "required_slots": ("label", "pnl"),
    },
    {
        "id":             "outcome_close_loss_v1",
        "salience":       "loss",
        "regime_filter":  ("ANY",),
        "lines": [
            "Closed {label} at ${pnl:+.2f}.",
            ("?", "Reason: {reason}."),
            ("?", "Hold time: {hold_min}min."),
        ],
        "required_slots": ("label", "pnl"),
    },
    {
        "id":             "alert_RISK_OFF_v1",
        "salience":       "alert",
        "regime_filter":  ("HIGH_VOL_SHOCK",),
        "lines": [
            "HIGH_VOL_SHOCK regime — flat preferred.",
            ("?", "BTC ${btc_last:,.0f}, ATR {atr_pct_1h:.2%}/h, IV {iv_atm_pct:.1%}."),
            ("?", "Insurance pool delta {ins_delta_24h:+.2%} 24h."),
        ],
        "required_slots": (),
    },
]


# --- selector ---------------------------------------------------------------
def _select(label: str, ctx: dict) -> dict:
    regime = (ctx.get("regime") or "").upper()
    candidates = [
        t for t in _TEMPLATES
        if t["salience"] == label
        and (regime in t["regime_filter"] or "ANY" in t["regime_filter"])
    ]
    if not candidates:
        # fall back to any template matching the label irrespective of regime
        candidates = [t for t in _TEMPLATES if t["salience"] == label]
    if not candidates:
        raise NoTemplate(f"no template for label={label!r} regime={regime!r}")
    # Deterministic: pick first match (templates are ordered most-specific first)
    return candidates[0]


# --- strict slot fill -------------------------------------------------------
class _SafeMap(dict):
    """Raises KeyError on missing keys (so f-string-style format() bails)."""
    def __missing__(self, key):
        raise MissingSlot(key)


def _format_line(line: str, ctx: dict) -> str:
    return line.format_map(_SafeMap(ctx))


# --- compose ----------------------------------------------------------------
def compose(label: str, ctx: dict, *,
             tone: str = "terse_trader") -> dict:
    """Return {text, template_id, slots_used, slots_dropped}."""
    tpl = _select(label, ctx)

    # Verify required slots present
    missing_required = [s for s in tpl["required_slots"] if s not in ctx]
    if missing_required:
        raise MissingSlot(f"required slots missing: {missing_required}")

    out_lines: list[str] = []
    slots_used: set[str] = set()
    slots_dropped: list[str] = []

    for entry in tpl["lines"]:
        if isinstance(entry, tuple) and entry[0] == "?":
            # Optional line — drop on any missing slot
            try:
                rendered = _format_line(entry[1], ctx)
                out_lines.append(rendered)
            except MissingSlot as e:
                slots_dropped.append(entry[1])
                continue
        else:
            # Required line — raise on missing
            rendered = _format_line(entry, ctx)
            out_lines.append(rendered)
        # Track which slots were actually used
        # (cheap: just check which keys appear in the line)
        for k in ctx.keys():
            if "{" + k in entry if isinstance(entry, str) else "{" + k in entry[1]:
                slots_used.add(k)

    return {
        "text":          "\n".join(out_lines),
        "template_id":   tpl["id"],
        "salience":      label,
        "slots_used":    sorted(slots_used),
        "slots_dropped": slots_dropped,
        "tone":          tone,
    }


def list_templates() -> list[dict]:
    """Inspection helper."""
    return [{"id": t["id"], "salience": t["salience"],
             "regime_filter": t["regime_filter"],
             "required_slots": t["required_slots"],
             "n_lines": len(t["lines"])}
            for t in _TEMPLATES]
