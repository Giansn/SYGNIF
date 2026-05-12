"""brain/output_classifier.py — salience head over the brain's existing neurons.

Per CLAUDE.md §6.2, the output classifier picks ~50 association + ~30 concept_layer
neurons as a "salience head" and projects their activation pattern onto a
discrete label set: {routine, alert, risk_event, trend_change, win, loss, explain}.

Implementation principles:
  • READ-ONLY against the running brain — does NOT modify state, doesn't change
    neuron count, doesn't disturb STDP. Safe to enable on a hot brain.
  • Lazy sampling — pull neuron samples once per call, no background thread.
  • Persistent neuron-index seed so the same 80 neurons get sampled across
    process restarts (deterministic salience over time).
  • V1 projection matrix is uniform — every neuron contributes equally to its
    "preferred" label based on a deterministic hash partition. V2 will refine
    via STDP-correlated outcomes once we have outcome data.

Use:
    from brain.output_classifier import SalienceClassifier
    sc = SalienceClassifier(brain, seed=42)
    out = sc.classify()
    # → {"label": "routine", "confidence": 0.34, "scores": {...}}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

LABELS = ("routine", "alert", "risk_event", "trend_change",
          "win", "loss", "explain")

# Persisted per-neuron preferred-label assignment
_SAMPLER_PATH = Path(os.environ.get(
    "SYGNIF_SALIENCE_SAMPLER",
    str(Path.home() / ".sygnif" / "salience-sampler.json"),
))


class SalienceClassifier:
    """Salience head — picks fixed neurons, projects to label."""

    def __init__(self, brain, *, n_assoc: int = 50, n_concept: int = 30, seed: int = 42):
        self.brain = brain
        self.n_assoc = n_assoc
        self.n_concept = n_concept
        self.seed = seed
        self.indices = self._load_or_pick_indices()
        # Per-neuron preferred label (initial uniform partition, refined later
        # by SalienceClassifier.refine_from_outcome()).
        self.preferred = self._load_or_assign_preferred()

    # ---- index selection ---------------------------------------------------
    def _load_or_pick_indices(self) -> dict:
        """Return {region_name: np.array of neuron indices to sample}."""
        if _SAMPLER_PATH.exists():
            try:
                d = json.loads(_SAMPLER_PATH.read_text())
                return {k: np.array(v, dtype=int) for k, v in d.get("indices", {}).items()}
            except Exception:
                pass
        rng = np.random.default_rng(self.seed)
        regions = self.brain.regions
        out: dict = {}
        if "association" in regions:
            n = regions["association"].n_neurons
            out["association"] = rng.choice(n, size=min(self.n_assoc, n), replace=False)
        if "concept_layer" in regions:
            n = regions["concept_layer"].n_neurons
            out["concept_layer"] = rng.choice(n, size=min(self.n_concept, n), replace=False)
        self._persist(out, None)
        return out

    def _load_or_assign_preferred(self) -> np.ndarray:
        """Per-sampled-neuron preferred label index. V1: deterministic round-robin."""
        if _SAMPLER_PATH.exists():
            try:
                d = json.loads(_SAMPLER_PATH.read_text())
                if "preferred" in d:
                    return np.array(d["preferred"], dtype=int)
            except Exception:
                pass
        total = sum(len(v) for v in self.indices.values())
        # Round-robin over labels — uniform partition
        pref = np.array([i % len(LABELS) for i in range(total)], dtype=int)
        self._persist(self.indices, pref)
        return pref

    def _persist(self, indices: dict, preferred) -> None:
        _SAMPLER_PATH.parent.mkdir(parents=True, exist_ok=True)
        d = {"indices": {k: v.tolist() for k, v in indices.items()}}
        if preferred is not None:
            d["preferred"] = preferred.tolist()
        _SAMPLER_PATH.write_text(json.dumps(d, indent=2))

    # ---- classification ----------------------------------------------------
    def _sample_activations(self) -> np.ndarray:
        """Return concatenated [association_v, concept_v, association_binding] vector."""
        parts = []
        for region_name, idxs in self.indices.items():
            region = self.brain.regions.get(region_name)
            if region is None:
                continue
            try:
                v = region.neurons.v[idxs]
                parts.append(v)
            except Exception:
                parts.append(np.zeros(len(idxs)))
        if not parts:
            return np.zeros(self.n_assoc + self.n_concept)
        return np.concatenate(parts)

    def classify(self) -> dict:
        """Sample current activations, project to label scores."""
        acts = self._sample_activations()
        if len(acts) == 0:
            return {"label": "routine", "confidence": 0.0,
                    "scores": {l: 0.0 for l in LABELS}}
        # Normalize activations into 0..1 (membrane potential typically -70..30)
        norm = (acts - acts.min()) / max(acts.max() - acts.min(), 1e-6)
        # Aggregate per preferred-label bucket
        scores = np.zeros(len(LABELS))
        for i, label_idx in enumerate(self.preferred[:len(norm)]):
            scores[label_idx] += norm[i]
        # Normalize to probabilities
        if scores.sum() > 0:
            scores = scores / scores.sum()
        # Apply prefrontal boost — when prefrontal firing is high (decision-making),
        # bias toward "alert" / "explain" labels
        try:
            pf = self.brain.regions.get("prefrontal")
            if pf is not None:
                pf_rate = float(pf.neurons.get_firing_rate())
                if pf_rate > 0.05:
                    boost = min(0.3, pf_rate * 5)
                    scores[LABELS.index("alert")]   += boost * 0.5
                    scores[LABELS.index("explain")] += boost * 0.5
                    scores = scores / scores.sum()
        except Exception:
            pass
        # Apply predictive surprise — high surprise → trend_change / risk_event
        try:
            pred = self.brain.regions.get("predictive")
            if pred is not None:
                surprise = float(getattr(pred, "surprise", 0))
                if surprise > 0.3:
                    boost = min(0.4, surprise)
                    scores[LABELS.index("trend_change")] += boost * 0.6
                    scores[LABELS.index("risk_event")]   += boost * 0.4
                    scores = scores / scores.sum()
        except Exception:
            pass

        winner_idx = int(np.argmax(scores))
        return {
            "label":      LABELS[winner_idx],
            "confidence": round(float(scores[winner_idx]), 4),
            "scores":     {LABELS[i]: round(float(s), 4) for i, s in enumerate(scores)},
            "n_sampled":  len(acts),
        }

    def refine_from_outcome(self, outcome_label: str, *, lr: float = 0.05) -> None:
        """STDP-correlation refinement: nudge per-neuron preferred labels toward
        outcome_label, weighted by each neuron's CURRENT firing strength.

        Logic: at the moment a trade closes with outcome (win/loss/etc),
        sample which neurons in our 80-neuron salience head are firing
        strongly RIGHT NOW. Those neurons get their preferred-label
        assignment pushed toward outcome_label with probability
        proportional to firing strength × lr.

        This creates a positive feedback loop:
          • Neurons that are active during a "win" moment become more
            likely to predict "win" in the future when active again
          • Over many trades, the salience head learns to associate
            ambient brain state with outcome label

        Persistence: the updated preferred[] array is written back to
        ~/.sygnif/salience-sampler.json so it survives restarts.
        """
        if outcome_label not in LABELS:
            return
        target_idx = LABELS.index(outcome_label)
        acts = self._sample_activations()
        if len(acts) == 0:
            return
        # Normalize activations to probabilities (high-firing → high probability of being nudged)
        norm = (acts - acts.min()) / max(acts.max() - acts.min(), 1e-6)
        norm = norm / max(norm.sum(), 1e-6)
        # For each neuron, with probability lr × firing_strength, switch its
        # preferred label to target_idx. Bounded so no single outcome can
        # collapse the entire head to one label.
        rng = np.random.default_rng()
        n_to_nudge = max(1, int(lr * len(acts)))
        weights = norm[:len(self.preferred)]
        weights = weights / max(weights.sum(), 1e-6)
        candidates = rng.choice(len(self.preferred), size=n_to_nudge,
                                 replace=False, p=weights)
        # Don't let any single label exceed 50% of the head — preserves diversity
        cur_share = (self.preferred == target_idx).sum() / max(len(self.preferred), 1)
        if cur_share >= 0.5:
            return
        self.preferred[candidates] = target_idx
        self._persist(self.indices, self.preferred)
