"""
Fixed JSON keys for swarm BTC **vector → synth → translate** (no live orders).

Scripts and tests must use these names only for cross-stage fields.
"""

from __future__ import annotations

# --- contract ---
CONTRACT = "swarm_btc_flow/v1"
STAGE_VECTOR = "vector"
STAGE_SYNTH = "synth"

# --- shared ---
K_CONTRACT = "contract"
K_STAGE = "stage"
K_GENERATED_UTC = "generated_utc"

# --- vector (swarm_vectoryze_btc) ---
K_SWARM_MEAN = "swarm_mean"
K_SWARM_LABEL = "swarm_label"
K_SWARM_CONFLICT = "swarm_conflict"
K_SWARM_ENGINE = "swarm_engine"
K_ML_DETAIL = "ml_detail"
K_CH_DETAIL = "ch_detail"
K_SC_DETAIL = "sc_detail"
K_TA_DETAIL = "ta_detail"
K_CHANNEL_PROB_DOWN_PCT = "channel_prob_down_pct"
K_CHANNEL_PROB_UP_PCT = "channel_prob_up_pct"
K_TA_SCORE = "ta_score"
K_PREDICTION_CONSENSUS = "prediction_consensus"
K_SOURCES_N = "sources_n"
K_MISSING_FILES = "missing_files"

# --- synth (swarm_sintysize_btc) ---
K_BTC_USD_PRICE = "btc_usd_price"
K_PRICE_SYMBOL = "price_symbol"
K_ORDER_SIGNAL = "order_signal"  # HOLD | BUY | SELL — **signal only**, not an exchange order
K_AMOUNT_BTC = "amount_btc"
K_LEVERAGE = "leverage"
K_SIDE = "side"  # LONG | SHORT | FLAT
K_BTC_DUMP_RISK_PCT = "btc_dump_risk_pct"
K_BULL_BEAR = "bull_bear"  # BULL | BEAR | MIXED
K_ANALYSIS_ONLY = "analysis_only"

# --- defaults (display / sizing hints; not execution) ---
DEFAULT_AMOUNT_LABEL = "signal only"
DEFAULT_LEVERAGE = 5
DEFAULT_PRICE_CATEGORY = "spot"
DEFAULT_PRICE_SYMBOL = "BTCUSDT"
