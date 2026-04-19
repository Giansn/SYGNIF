#!/usr/bin/env python3
"""
Optional **PyTorch** path for swarm **vote aggregation** (vectorized, ``inference_mode``).

- Default behaviour matches the legacy Python mean / conflict / label thresholds.
- **Weighted mean:** ``SYGNIF_SWARM_PT_WEIGHTS`` — comma-separated floats, one per vote in order
  (``ml,ch,sc,ta,...``); shorter lists are padded with ``1.0``. Weights apply only to **swarm_mean**;
  **conflict** / **spread** stay on raw discrete votes for interpretability.

Env:
  SYGNIF_SWARM_PYTORCH=1   — use this module when ``torch`` is importable
  SYGNIF_SWARM_PT_WEIGHTS= — e.g. ``1,1,1.2,0.9`` (optional)
"""
from __future__ import annotations

import os
from typing import Any


def torch_available() -> bool:
    try:
        import torch  # noqa: PLC0415, F401

        return True
    except ImportError:
        return False


def _parse_weights(n: int) -> list[float]:
    raw = (os.environ.get("SYGNIF_SWARM_PT_WEIGHTS") or "").strip()
    if not raw:
        return [1.0] * n
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: list[float] = []
    for p in parts[:n]:
        try:
            out.append(float(p))
        except ValueError:
            out.append(1.0)
    while len(out) < n:
        out.append(1.0)
    return out[:n]


def aggregate_vote_stats(
    vote_ints: list[int],
    *,
    mean_threshold: float = 0.25,
) -> dict[str, Any]:
    """
    Return keys: mean, label, conflict, spread, engine_detail.

    ``spread`` / ``conflict`` use only non-zero votes (same semantics as ``swarm_knowledge``).
    """
    import torch

    if not vote_ints:
        return {
            "mean": 0.0,
            "label": "SWARM_MIXED",
            "conflict": False,
            "spread": 0,
            "engine_detail": "pytorch_empty",
        }

    w_list = _parse_weights(len(vote_ints))
    with torch.inference_mode():
        t = torch.tensor(vote_ints, dtype=torch.float32)
        w = torch.tensor(w_list, dtype=torch.float32)
        w_sum = w.sum().clamp(min=1e-6)
        mean_t = (t * w).sum() / w_sum
        mean = float(mean_t.item())

        nz = t[t != 0]
        if nz.numel() >= 2:
            spread = int(round(float((nz.max() - nz.min()).item())))
        else:
            spread = 0
        conflict = spread >= 2

    if mean > mean_threshold:
        label = "SWARM_BULL"
    elif mean < -mean_threshold:
        label = "SWARM_BEAR"
    else:
        label = "SWARM_MIXED"

    detail = "pytorch_weighted_mean" if any(abs(x - 1.0) > 1e-6 for x in w_list) else "pytorch_mean"
    return {
        "mean": mean,
        "label": label,
        "conflict": conflict,
        "spread": spread,
        "engine_detail": detail,
    }
