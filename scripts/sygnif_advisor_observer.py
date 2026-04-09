#!/usr/bin/env python3
"""
Background observation bundle for Sygnif: worker, overseer, adaptation path check,
optional horizon snapshot line, heuristic *proposals* (pending human approval).

Writes:
  user_data/advisor_state.json       — last run (JSON)
  user_data/advisor_pending.json     — queue of proposed overrides (append-only until approved)
  ~/.local/share/sygnif-agent/advisor_history.jsonl — append audit log

Does not modify strategy_adaptation.json (use Telegram /sygnif approve <id> or manual edit).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

SYGNIF_REPO = Path(os.environ.get("SYGNIF_REPO", "/home/ubuntu/xrp_claude_bot")).resolve()
OVERSEER_URL = os.environ.get("OVERSEER_URL", "http://127.0.0.1:8090").rstrip("/")
WORKER_HEALTH = os.environ.get(
    "CURSOR_WORKER_HEALTH_URL", "http://127.0.0.1:8093/healthz"
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _horizon_line(symbol: str = "XRP") -> str:
    pred_dir = Path.home() / ".local/share/sygnif-agent/predictions"
    latest = pred_dir / f"{symbol.upper()}_latest.json"
    if not latest.exists():
        return f"horizon: no snapshot for {symbol} (run weekly_strategy_analysis or prediction_horizon save)"
    script = SYGNIF_REPO / "scripts" / "prediction_horizon_check.py"
    try:
        p = subprocess.run(
            [sys.executable, str(script), "check", "--symbol", symbol],
            capture_output=True,
            text=True,
            timeout=90,
        )
        return (p.stdout or p.stderr or "").strip()[:2000]
    except Exception as e:
        return f"horizon check error: {e}"


def _btc_ta_hint() -> float | None:
    """Lightweight BTC 1h TA score proxy (same API as finance_agent)."""
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "spot", "symbol": "BTCUSDT", "interval": "60", "limit": 200},
            timeout=15,
        )
        r.raise_for_status()
        lst = (r.json().get("result") or {}).get("list") or []
        if not lst:
            return None
        # Bybit returns newest first
        closes = [float(x[4]) for x in reversed(lst)]
        if len(closes) < 20:
            return None
        # Minimal RSI-like 0-100 proxy: position in 14-bar range (not full TA score)
        last = closes[-1]
        window = closes[-15:-1]
        lo, hi = min(window), max(window)
        if hi <= lo:
            return 50.0
        pos = (last - lo) / (hi - lo) * 100.0
        return max(0.0, min(100.0, pos))
    except Exception:
        return None


def _load_pending() -> dict:
    p = SYGNIF_REPO / "user_data" / "advisor_pending.json"
    if not p.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "items": []}


def _save_pending(data: dict) -> None:
    p = SYGNIF_REPO / "user_data" / "advisor_pending.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)


def _proposal_fingerprint(proposed: dict, reason: str) -> str:
    raw = json.dumps(proposed, sort_keys=True) + "|" + reason
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _append_heuristic_proposals(state: dict) -> None:
    """Add at most one pending proposal per fingerprint (audit-friendly, non-auto-apply)."""
    btc_hint = state.get("btc_range_proxy")
    pending = _load_pending()
    items = pending.get("items", [])
    existing = {_proposal_fingerprint(x.get("proposed_overrides") or {}, x.get("reason") or "") for x in items if x.get("status") == "pending"}

    proposals: list[dict] = []
    if isinstance(btc_hint, (int, float)) and btc_hint < 22.0:
        prop = {
            "sentiment_threshold_buy": 57.0,
        }
        reason = (
            "Heuristic: BTC 1h range-position proxy very low (<22) — optional: raise "
            "sentiment_threshold_buy to reduce marginal longs in risk-off chop."
        )
        fp = _proposal_fingerprint(prop, reason)
        if fp not in existing:
            proposals.append(
                {
                    "id": uuid.uuid4().hex[:10],
                    "created_utc": _utc(),
                    "source": "advisor_observer",
                    "proposed_overrides": prop,
                    "reason": reason,
                    "status": "pending",
                    "verify_note": "Compare with advisor_state.json btc_range_proxy next run.",
                }
            )

    if not proposals:
        return

    for pr in proposals:
        items.append(pr)
    pending["items"] = items
    _save_pending(pending)


def build_state() -> dict:
    worker = {}
    try:
        h = requests.get(WORKER_HEALTH, timeout=3)
        worker = {"http_status": h.status_code, "ok": h.ok}
    except Exception as e:
        worker = {"error": str(e)}

    overseer = {}
    try:
        ov = requests.get(f"{OVERSEER_URL}/overview", timeout=6)
        overseer = {"http_status": ov.status_code, "snippet": ov.text[:1500]}
    except Exception as e:
        overseer = {"error": str(e)}

    adapt_path = SYGNIF_REPO / "user_data" / "strategy_adaptation.json"
    adaptation = {}
    if adapt_path.is_file():
        try:
            adaptation = json.loads(adapt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            adaptation = {"error": str(e)}
    else:
        adaptation = {"missing": str(adapt_path)}

    btc_rp = _btc_ta_hint()
    state = {
        "generated_utc": _utc(),
        "sygnif_repo": str(SYGNIF_REPO),
        "cursor_worker": worker,
        "overseer": overseer,
        "strategy_adaptation_path": str(adapt_path),
        "strategy_adaptation": adaptation,
        "btc_range_proxy": btc_rp,
        "horizon_check": _horizon_line("XRP"),
        "verification": [
            "Predictions: compare horizon_check weekly vs spot (prediction_horizon_check).",
            "Proposals: see advisor_pending.json — approve via /sygnif approve <id> (Telegram).",
            "Live overrides: only after approve or manual edit of strategy_adaptation.json.",
        ],
    }
    return state


def run_observer(append_heuristics: bool = True) -> dict:
    state = build_state()
    out = SYGNIF_REPO / "user_data" / "advisor_state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    log_dir = Path.home() / ".local/share/sygnif-agent"
    log_dir.mkdir(parents=True, exist_ok=True)
    logf = log_dir / "advisor_history.jsonl"
    with open(logf, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": state["generated_utc"], "btc_range_proxy": state.get("btc_range_proxy")}, ensure_ascii=False) + "\n")

    if append_heuristics and os.environ.get("ADVISOR_HEURISTICS", "1").strip() not in ("0", "false", "no"):
        try:
            _append_heuristic_proposals(state)
        except Exception:
            pass
    return state


def main() -> int:
    st = run_observer()
    print(json.dumps({"ok": True, "generated_utc": st["generated_utc"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
