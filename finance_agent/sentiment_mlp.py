"""
Small numpy MLP for sentiment adjustment; optional blend with rule-based expert.

Train with: python3 scripts/train_sentiment_mlp.py
Weights: NPZ with normalized features + 2-layer net (no PyTorch in Docker).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
import numpy as np

from expert_sentiment import SentimentSignals

FEATURE_DIM = 12


def signals_to_feature_matrix(sig: SentimentSignals, ta_score: float) -> np.ndarray:
    """Same normalization as training script (one row)."""
    ta = float(ta_score) / 100.0
    n = min(float(sig.n_lines), 20.0) / 20.0
    bull = min(float(sig.bull_t), 40.0) / 40.0
    bear = min(float(sig.bear_t), 40.0) / 40.0
    net_t = math.tanh(float(sig.net) / 6.0)
    bh = min(float(sig.total_bh), 15.0) / 15.0
    eh = min(float(sig.total_eh), 15.0) / 15.0
    live = 1.0 if sig.has_live else 0.0
    bd = (float(sig.bull_t) - float(sig.bear_t)) / 40.0
    bd = max(-1.0, min(1.0, bd))
    ta_mid = abs(float(ta_score) - 50.0) / 50.0
    cross = ta * net_t
    bec = max(-1.0, min(1.0, (float(sig.total_bh) - float(sig.total_eh)) / 15.0))
    v = np.array(
        [ta, n, bull, bear, net_t, bh, eh, live, bd, ta_mid, cross, bec],
        dtype=np.float64,
    )
    assert v.shape == (FEATURE_DIM,)
    return v


@dataclass
class MLPWeights:
    w1: np.ndarray  # (in, h1)
    b1: np.ndarray  # (h1,)
    w2: np.ndarray  # (h1, h2)
    b2: np.ndarray  # (h2,)
    w3: np.ndarray  # (h2,) last layer weights
    b3: np.ndarray  # scalar as shape (1,)
    mean: np.ndarray
    std: np.ndarray


_mlp_cache: MLPWeights | None = None
_mlp_load_error: str | None = None


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0.0, x)


def _forward_raw(x_norm: np.ndarray, m: MLPWeights) -> float:
    """x_norm shape (FEATURE_DIM,)"""
    z1 = x_norm @ m.w1 + m.b1
    a1 = _relu(z1)
    z2 = a1 @ m.w2 + m.b2
    a2 = _relu(z2)
    out = float(a2 @ m.w3 + float(m.b3.ravel()[0]))
    return out


def predict_score(sig: SentimentSignals, ta_score: float, m: MLPWeights) -> float:
    x = signals_to_feature_matrix(sig, ta_score)
    xn = (x - m.mean) / m.std
    y = _forward_raw(xn, m)
    return max(-20.0, min(20.0, y))


def load_mlp_weights(path: str) -> MLPWeights:
    d = np.load(path, allow_pickle=False)
    return MLPWeights(
        w1=np.asarray(d["w1"], dtype=np.float64),
        b1=np.asarray(d["b1"], dtype=np.float64).ravel(),
        w2=np.asarray(d["w2"], dtype=np.float64),
        b2=np.asarray(d["b2"], dtype=np.float64).ravel(),
        w3=np.asarray(d["w3"], dtype=np.float64).ravel(),
        b3=np.asarray(d["b3"], dtype=np.float64).reshape(1),
        mean=np.asarray(d["mean"], dtype=np.float64).ravel(),
        std=np.asarray(d["std"], dtype=np.float64).ravel(),
    )


def get_loaded_mlp() -> tuple[MLPWeights | None, str | None]:
    """Singleton load from SENTIMENT_MLP_WEIGHTS."""
    global _mlp_cache, _mlp_load_error
    enabled = os.environ.get("SENTIMENT_MLP_ENABLED", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not enabled:
        return None, None
    path = (os.environ.get("SENTIMENT_MLP_WEIGHTS") or "").strip()
    if not path:
        return None, None
    if _mlp_cache is not None:
        return _mlp_cache, None
    if _mlp_load_error is not None:
        return None, _mlp_load_error
    try:
        _mlp_cache = load_mlp_weights(path)
    except Exception as e:
        _mlp_load_error = str(e)
        return None, _mlp_load_error
    return _mlp_cache, None


def optional_mlp_adjust(
    sig: SentimentSignals,
    ta_score: float,
    rule_score: float,
    reason: str,
) -> tuple[float, str]:
    m, err = get_loaded_mlp()
    if m is None:
        return rule_score, reason
    try:
        alpha = float(os.environ.get("SENTIMENT_MLP_ALPHA", "0.45"))
    except ValueError:
        alpha = 0.45
    alpha = max(0.0, min(1.0, alpha))
    ml = predict_score(sig, ta_score, m)
    blended = (1.0 - alpha) * float(rule_score) + alpha * ml
    blended = max(-20.0, min(20.0, round(blended, 2)))
    note = f" MLP blend α={alpha:.2f} (rule {rule_score:+.2f} → net {blended:+.2f})."
    return blended, reason + note


def save_weights(path: str, m: MLPWeights) -> None:
    np.savez(
        path,
        w1=m.w1,
        b1=m.b1,
        w2=m.w2,
        b2=m.b2,
        w3=m.w3,
        b3=np.asarray(m.b3).reshape(1),
        mean=m.mean,
        std=m.std,
    )
