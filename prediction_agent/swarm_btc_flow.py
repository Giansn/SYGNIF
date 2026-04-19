"""
Swarm BTC flow: **vector** (file + swarm facts) → **synth** (card fields) → **translate** prints once.

Read-only: **no** ``POST /v5/order``, no Freqtrade forceenter.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swarm_btc_flow_constants import CONTRACT
from swarm_btc_flow_constants import DEFAULT_AMOUNT_LABEL
from swarm_btc_flow_constants import DEFAULT_LEVERAGE
from swarm_btc_flow_constants import DEFAULT_PRICE_CATEGORY
from swarm_btc_flow_constants import DEFAULT_PRICE_SYMBOL
from swarm_btc_flow_constants import K_AMOUNT_BTC
from swarm_btc_flow_constants import K_ANALYSIS_ONLY
from swarm_btc_flow_constants import K_BTC_DUMP_RISK_PCT
from swarm_btc_flow_constants import K_BTC_USD_PRICE
from swarm_btc_flow_constants import K_BULL_BEAR
from swarm_btc_flow_constants import K_CH_DETAIL
from swarm_btc_flow_constants import K_CHANNEL_PROB_DOWN_PCT
from swarm_btc_flow_constants import K_CHANNEL_PROB_UP_PCT
from swarm_btc_flow_constants import K_CONTRACT
from swarm_btc_flow_constants import K_GENERATED_UTC
from swarm_btc_flow_constants import K_LEVERAGE
from swarm_btc_flow_constants import K_MISSING_FILES
from swarm_btc_flow_constants import K_ML_DETAIL
from swarm_btc_flow_constants import K_ORDER_SIGNAL
from swarm_btc_flow_constants import K_PREDICTION_CONSENSUS
from swarm_btc_flow_constants import K_PRICE_SYMBOL
from swarm_btc_flow_constants import K_SC_DETAIL
from swarm_btc_flow_constants import K_SIDE
from swarm_btc_flow_constants import K_SOURCES_N
from swarm_btc_flow_constants import K_STAGE
from swarm_btc_flow_constants import K_SWARM_CONFLICT
from swarm_btc_flow_constants import K_SWARM_ENGINE
from swarm_btc_flow_constants import K_SWARM_LABEL
from swarm_btc_flow_constants import K_SWARM_MEAN
from swarm_btc_flow_constants import K_TA_DETAIL
from swarm_btc_flow_constants import K_TA_SCORE
from swarm_btc_flow_constants import STAGE_SYNTH
from swarm_btc_flow_constants import STAGE_VECTOR


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _prediction_dir(repo: Path) -> Path:
    for key in ("PREDICTION_AGENT_DIR", "SYGNIF_PREDICTION_AGENT_DIR"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    return repo / "prediction_agent"


def _btc_data_dir(repo: Path) -> Path:
    raw = (os.environ.get("NAUTILUS_BTC_OHLCV_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return repo / "finance_agent" / "btc_specialist" / "data"


def _ensure_finance_agent_path(repo: Path) -> None:
    fa = repo / "finance_agent"
    s = str(fa)
    if s not in sys.path:
        sys.path.insert(0, s)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _consensus_str(pred: dict[str, Any]) -> str:
    p = pred.get("predictions") if isinstance(pred.get("predictions"), dict) else {}
    for key in ("consensus_nautilus_enhanced", "consensus"):
        v = p.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    return ""


def build_swarm_btc_vector(repo: Path | None = None) -> dict[str, Any]:
    """Stage **vector**: swarm + channel / TA / ML sidecar facts (constant keys)."""
    repo = repo or _repo_root()
    _ensure_finance_agent_path(repo)
    import swarm_knowledge as sk  # noqa: PLC0415

    pred_path = _prediction_dir(repo) / "btc_prediction_output.json"
    train_path = _prediction_dir(repo) / "training_channel_output.json"
    sidecar_path = _btc_data_dir(repo) / "nautilus_strategy_signal.json"
    ta_path = _btc_data_dir(repo) / "btc_sygnif_ta_snapshot.json"

    swarm = sk.compute_swarm(
        pred_path=pred_path,
        train_path=train_path,
        sidecar_path=sidecar_path,
        ta_path=ta_path,
    )
    parts = swarm.get("sources") if isinstance(swarm.get("sources"), dict) else {}

    pred = _read_json(pred_path)
    train = _read_json(train_path)
    rec = train.get("recognition") if isinstance(train.get("recognition"), dict) else {}
    ta = _read_json(ta_path)

    down: float | None = None
    up: float | None = None
    try:
        down = float(rec.get("last_bar_probability_down_pct"))
    except (TypeError, ValueError):
        down = None
    try:
        up = float(rec.get("last_bar_probability_up_pct"))
    except (TypeError, ValueError):
        up = None

    ta_score: float | None = None
    try:
        ta_score = float(ta.get("ta_score"))
    except (TypeError, ValueError):
        ta_score = None

    return {
        K_CONTRACT: CONTRACT,
        K_STAGE: STAGE_VECTOR,
        K_GENERATED_UTC: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        K_SWARM_MEAN: swarm.get("swarm_mean"),
        K_SWARM_LABEL: swarm.get("swarm_label"),
        K_SWARM_CONFLICT: swarm.get("swarm_conflict"),
        K_SWARM_ENGINE: swarm.get("swarm_engine"),
        K_ML_DETAIL: (parts.get("ml") or {}).get("detail") if isinstance(parts.get("ml"), dict) else None,
        K_CH_DETAIL: (parts.get("ch") or {}).get("detail") if isinstance(parts.get("ch"), dict) else None,
        K_SC_DETAIL: (parts.get("sc") or {}).get("detail") if isinstance(parts.get("sc"), dict) else None,
        K_TA_DETAIL: (parts.get("ta") or {}).get("detail") if isinstance(parts.get("ta"), dict) else None,
        K_CHANNEL_PROB_DOWN_PCT: down,
        K_CHANNEL_PROB_UP_PCT: up,
        K_TA_SCORE: ta_score,
        K_PREDICTION_CONSENSUS: _consensus_str(pred),
        K_SOURCES_N: swarm.get("sources_n"),
        K_MISSING_FILES: swarm.get("missing_files"),
    }


def _signal_order_and_side(
    *,
    label: str | None,
    conflict: bool | None,
) -> tuple[str, str]:
    """Return (order_signal, side) — **paper signal** only."""
    if conflict:
        return "HOLD", "FLAT"
    lb = (label or "").strip().upper()
    if lb == "SWARM_BULL":
        return "BUY", "LONG"
    if lb == "SWARM_BEAR":
        return "SELL", "SHORT"
    return "HOLD", "FLAT"


def _bull_bear_from_label(label: str | None) -> str:
    lb = (label or "").strip().upper()
    if lb == "SWARM_BULL":
        return "BULL"
    if lb == "SWARM_BEAR":
        return "BEAR"
    return "MIXED"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def fetch_btc_card_price(
    *,
    category: str | None = None,
    symbol: str | None = None,
) -> tuple[float | None, str]:
    """Public Bybit ticker; returns (last_price, symbol_used)."""
    _ensure_finance_agent_path(_repo_root())
    import swarm_knowledge as sk  # noqa: PLC0415

    cat = (category or os.environ.get("SWARM_BTC_CARD_PRICE_CATEGORY") or DEFAULT_PRICE_CATEGORY).strip().lower()
    if cat not in ("linear", "spot"):
        cat = DEFAULT_PRICE_CATEGORY
    sym = (symbol or os.environ.get("SWARM_BTC_CARD_PRICE_SYMBOL") or DEFAULT_PRICE_SYMBOL).strip().upper()
    if not sym:
        sym = DEFAULT_PRICE_SYMBOL
    row = sk.fetch_bybit_mainnet_ticker_row(
        category=cat,
        symbol=sym,
        timeout_sec=_env_float("SYGNIF_SWARM_BYBIT_TIMEOUT_SEC", 6.0),
        cache_sec=_env_float("SWARM_BTC_CARD_PRICE_CACHE_SEC", 30.0),
    )
    if not row:
        return None, sym
    try:
        return float(row.get("lastPrice") or 0.0), sym
    except (TypeError, ValueError):
        return None, sym


def synthesize_swarm_btc_card(
    vector: dict[str, Any],
    *,
    repo: Path | None = None,
    price_override: float | None = None,
    skip_price_fetch: bool = False,
) -> dict[str, Any]:
    """Stage **synth**: constant keys for translator + optional live price."""
    repo = repo or _repo_root()
    order, side = _signal_order_and_side(
        label=str(vector.get(K_SWARM_LABEL) or ""),
        conflict=bool(vector.get(K_SWARM_CONFLICT)),
    )
    bull_bear = _bull_bear_from_label(str(vector.get(K_SWARM_LABEL) or ""))

    lev = _env_int("SWARM_BTC_CARD_LEVERAGE", DEFAULT_LEVERAGE)
    amt_raw = (os.environ.get("SWARM_BTC_CARD_AMOUNT_BTC") or "").strip()
    amount_label = amt_raw if amt_raw else DEFAULT_AMOUNT_LABEL

    px: float | None = price_override
    sym_used = (os.environ.get("SWARM_BTC_CARD_PRICE_SYMBOL") or DEFAULT_PRICE_SYMBOL).strip().upper() or DEFAULT_PRICE_SYMBOL
    if px is None and not skip_price_fetch:
        px, sym_used = fetch_btc_card_price()

    dump = vector.get(K_CHANNEL_PROB_DOWN_PCT)
    if dump is not None:
        try:
            dump_f = float(dump)
        except (TypeError, ValueError):
            dump_f = None
    else:
        dump_f = None

    return {
        K_CONTRACT: CONTRACT,
        K_STAGE: STAGE_SYNTH,
        K_GENERATED_UTC: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        K_BTC_USD_PRICE: px,
        K_PRICE_SYMBOL: sym_used,
        K_ORDER_SIGNAL: order,
        K_AMOUNT_BTC: amount_label,
        K_LEVERAGE: lev,
        K_SIDE: side,
        K_BTC_DUMP_RISK_PCT: dump_f,
        K_BULL_BEAR: bull_bear,
        K_ANALYSIS_ONLY: True,
        # echo minimal vector refs for audits (same constant namespace)
        K_SWARM_LABEL: vector.get(K_SWARM_LABEL),
        K_SWARM_CONFLICT: vector.get(K_SWARM_CONFLICT),
        K_SWARM_MEAN: vector.get(K_SWARM_MEAN),
    }


def atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
