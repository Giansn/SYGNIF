"""
ML Signal Ensemble for Sygnif strategies.

XGBoost + LSTM-like feature (approximated via rolling statistics for cold start).
Full LSTM training requires `scripts/train_ml_ensemble.py`; this module provides:
  1. Feature engineering from existing indicator columns
  2. XGBoost model loading + inference
  3. Fallback heuristic when no trained model is available

Model file: `user_data/ml_models/xgb_signal_ensemble.json`
The model predicts P(profitable_long | features) on a 0–1 scale.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).resolve().parent.parent / "ml_models" / "xgb_signal_ensemble.json"

_xgb_model = None
_xgb_load_attempted = False


def _try_load_xgb():
    global _xgb_model, _xgb_load_attempted
    if _xgb_load_attempted:
        return _xgb_model
    _xgb_load_attempted = True
    if not MODEL_PATH.exists():
        logger.info("ML ensemble: no model at %s — using heuristic fallback", MODEL_PATH)
        return None
    try:
        import xgboost as xgb
        model = xgb.XGBClassifier()
        model.load_model(str(MODEL_PATH))
        _xgb_model = model
        logger.info("ML ensemble: loaded XGBoost model from %s", MODEL_PATH)
    except Exception as e:
        logger.warning("ML ensemble: failed to load model: %s", e)
    return _xgb_model


FEATURE_COLS = [
    "RSI_14", "RSI_3", "ADX_14", "DMP_14", "DMN_14",
    "WILLR_14", "CMF_20", "STOCHRSIk_14_14_3_3",
    "BBP_20_2.0", "AROONU_14", "AROOND_14",
    "change_pct", "ATR_14", "volume_sma_25",
    "cdl_net_bullish",
]

FEATURE_COLS_OPTIONAL = [
    "smc_bos", "smc_choch", "smc_fvg", "smc_ob",
    "vsd_signal", "vsd_in_demand", "vsd_in_supply",
    "RSI_14_1h", "RSI_14_4h",
]


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extract ML features from the indicator DataFrame."""
    feats = pd.DataFrame(index=df.index)
    for col in FEATURE_COLS + FEATURE_COLS_OPTIONAL:
        if col in df.columns:
            feats[col] = df[col].astype(np.float64).fillna(0)
        elif col in FEATURE_COLS:
            feats[col] = 0.0

    if "volume_sma_25" in feats.columns:
        feats["vol_ratio"] = np.where(
            feats["volume_sma_25"] > 0,
            df["volume"] / feats["volume_sma_25"],
            1.0,
        )
    else:
        feats["vol_ratio"] = 1.0

    feats["rsi_momentum"] = df.get("RSI_3", pd.Series(50, index=df.index)).fillna(50) - 50
    feats["adx_trend"] = df.get("ADX_14", pd.Series(0, index=df.index)).fillna(0)
    feats["price_vs_bb"] = df.get("BBP_20_2.0", pd.Series(0.5, index=df.index)).fillna(0.5) - 0.5

    return feats


def _heuristic_score(feats: pd.DataFrame) -> pd.Series:
    """Simple heuristic when no trained model is available.

    Combines a handful of key features into a 0–1 probability-like score.
    Not a replacement for a proper model — just a starting point.
    """
    score = pd.Series(0.5, index=feats.index, dtype=np.float64)

    rsi = feats.get("RSI_14", pd.Series(50, index=feats.index))
    score += np.where(rsi < 30, 0.15, np.where(rsi > 70, -0.15, 0))

    adx = feats.get("ADX_14", pd.Series(0, index=feats.index))
    dmp = feats.get("DMP_14", pd.Series(0, index=feats.index))
    dmn = feats.get("DMN_14", pd.Series(0, index=feats.index))
    score += np.where((adx > 25) & (dmp > dmn), 0.10, np.where((adx > 25) & (dmn > dmp), -0.10, 0))

    cdl = feats.get("cdl_net_bullish", pd.Series(0, index=feats.index))
    score += np.where(cdl > 0, 0.05, np.where(cdl < 0, -0.05, 0))

    smc_bull = feats.get("smc_bos", pd.Series(0, index=feats.index))
    score += np.where(smc_bull > 0, 0.05, np.where(smc_bull < 0, -0.05, 0))

    vsd_d = feats.get("vsd_in_demand", pd.Series(False, index=feats.index))
    vsd_s = feats.get("vsd_in_supply", pd.Series(False, index=feats.index))
    score += np.where(vsd_d, 0.05, np.where(vsd_s, -0.05, 0))

    return score.clip(0, 1)


def attach_ml_signal(df: pd.DataFrame) -> None:
    """Add ``ml_signal`` column (0–1) in-place.

    Uses XGBoost model if available, otherwise heuristic fallback.
    """
    feats = _build_features(df)
    model = _try_load_xgb()

    if model is not None:
        try:
            model_feats = [c for c in model.get_booster().feature_names if c in feats.columns]
            X = feats[model_feats].fillna(0)
            proba = model.predict_proba(X)[:, 1]
            df["ml_signal"] = proba
            return
        except Exception as e:
            logger.warning("ML ensemble inference failed, using heuristic: %s", e)

    df["ml_signal"] = _heuristic_score(feats)
