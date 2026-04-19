"""
Sparse Synapse System with Spike-Timing Dependent Plasticity (STDP)

Uses scipy sparse matrices for memory-efficient storage of billions of connections.
STDP strengthens connections when pre-synaptic neuron fires before post-synaptic (LTP)
and weakens them when the order is reversed (LTD).
"""

import numpy as np
from scipy import sparse
from brain.config import BrainConfig


class SynapseMatrix:
    """Sparse synapse connectivity with STDP learning between two neuron populations."""

    def __init__(self, n_pre: int, n_post: int, connection_prob: float,
                 w_init_mean: float = 0.5, w_init_std: float = 0.1):
        self.n_pre = n_pre
        self.n_post = n_post

        # Generate sparse random connectivity
        nnz = int(n_pre * n_post * connection_prob)
        if nnz == 0:
            self.weights = sparse.csr_matrix((n_post, n_pre), dtype=np.float32)
            self.nnz = 0
            return

        # Random pre/post pairs
        pre_idx = np.random.randint(0, n_pre, nnz)
        post_idx = np.random.randint(0, n_post, nnz)

        # Initial weights (log-normal distribution for biological realism)
        weights = np.random.lognormal(
            mean=np.log(w_init_mean), sigma=w_init_std, size=nnz
        ).astype(np.float32)
        weights = np.clip(weights, BrainConfig.STDP_W_MIN, BrainConfig.STDP_W_MAX)

        # Build sparse matrix (post x pre) for efficient post-synaptic current computation
        self.weights = sparse.csr_matrix(
            (weights, (post_idx, pre_idx)),
            shape=(n_post, n_pre),
            dtype=np.float32
        )
        self.weights.sum_duplicates()
        self.nnz = self.weights.nnz

        # STDP traces
        self.pre_trace = np.zeros(n_pre, dtype=np.float32)
        self.post_trace = np.zeros(n_post, dtype=np.float32)

        # Neuromodulation factor (dopamine modulates learning)
        self.modulation = 1.0

    def propagate(self, pre_fired: np.ndarray) -> np.ndarray:
        """Compute post-synaptic currents from pre-synaptic spikes."""
        if self.nnz == 0:
            return np.zeros(self.n_post)
        # Sparse matrix-vector multiply: I_post = W @ fired_pre
        pre_spikes = pre_fired.astype(np.float32)
        return np.asarray(self.weights.dot(pre_spikes)).flatten()

    def update_stdp(self, pre_fired: np.ndarray, post_fired: np.ndarray, dt: float = 1.0):
        """Apply STDP learning rule based on spike timing."""
        if self.nnz == 0:
            return

        tau_plus = BrainConfig.STDP_TAU_PLUS
        tau_minus = BrainConfig.STDP_TAU_MINUS
        a_plus = BrainConfig.STDP_A_PLUS * self.modulation
        a_minus = BrainConfig.STDP_A_MINUS * self.modulation

        # Decay traces
        self.pre_trace *= np.exp(-dt / tau_plus)
        self.post_trace *= np.exp(-dt / tau_minus)

        # Update traces for neurons that fired
        pre_mask = pre_fired.astype(bool)
        post_mask = post_fired.astype(bool)

        if np.any(pre_mask):
            self.pre_trace[pre_mask] += a_plus

        if np.any(post_mask):
            self.post_trace[post_mask] += a_minus

        # STDP weight updates for ALL active post-synaptic neurons
        if np.any(pre_mask) and np.any(post_mask):
            post_indices = np.where(post_mask)[0]
            for pi in post_indices:
                start = self.weights.indptr[pi]
                end = self.weights.indptr[pi + 1]
                if end > start:
                    cols = self.weights.indices[start:end]
                    # LTP: strengthen connections from active pre-synaptic neurons
                    dw = self.pre_trace[cols] * a_plus
                    self.weights.data[start:end] = np.clip(
                        self.weights.data[start:end] + dw,
                        BrainConfig.STDP_W_MIN, BrainConfig.STDP_W_MAX
                    )

            # LTD: weaken connections where pre fired but post didn't
            pre_indices = np.where(pre_mask)[0]
            not_post = np.where(~post_mask)[0]
            for pi in not_post[:50]:  # Sample inactive post neurons for LTD
                start = self.weights.indptr[pi]
                end = self.weights.indptr[pi + 1]
                if end > start:
                    cols = self.weights.indices[start:end]
                    active_pre = pre_mask[cols]
                    if np.any(active_pre):
                        self.weights.data[start:end][active_pre] = np.clip(
                            self.weights.data[start:end][active_pre] - a_minus * 0.5,
                            BrainConfig.STDP_W_MIN, BrainConfig.STDP_W_MAX
                        )

    def get_stats(self) -> dict:
        """Get synapse statistics."""
        if self.nnz == 0:
            return {"count": 0, "mean_weight": 0, "std_weight": 0}
        data = self.weights.data
        return {
            "count": int(self.nnz),
            "mean_weight": float(np.mean(data)),
            "std_weight": float(np.std(data)),
            "max_weight": float(np.max(data)),
            "min_weight": float(np.min(data)) if len(data) > 0 else 0,
        }
