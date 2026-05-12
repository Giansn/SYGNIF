"""
Izhikevich Spiking Neuron Model - Vectorized NumPy Implementation

Each neuron has membrane potential (v) and recovery variable (u).
Different parameter sets produce different firing patterns:
- Regular spiking, Fast spiking, Chattering, Intrinsically bursting, etc.
"""

import numpy as np


class NeuronPopulation:
    """Manages a population of Izhikevich spiking neurons."""

    def __init__(self, n: int, a: float, b: float, c: float, d: float, dt: float = 1.0):
        self.n = n
        self.dt = dt

        # Izhikevich parameters (can vary per neuron with noise)
        noise = np.random.uniform(0.95, 1.05, n)
        self.a = np.full(n, a) * noise
        self.b = np.full(n, b) * noise
        self.c = np.full(n, c) + np.random.uniform(-2, 2, n)
        self.d = np.full(n, d) * noise

        # State variables
        self.v = np.full(n, -65.0)  # Membrane potential (mV)
        self.u = self.b * self.v     # Recovery variable
        self.fired = np.zeros(n, dtype=bool)
        self.last_spike_time = np.full(n, -1000.0)

        # Input current accumulator
        self.I = np.zeros(n)

        # Spike history for STDP (circular buffer)
        self.spike_count = 0
        self.total_spikes = 0

        # Refractory period tracking
        self.refractory = np.zeros(n, dtype=np.float32)
        self.refractory_period = 2.0  # ms

    def step(self, external_current: np.ndarray, t: float, noise_amplitude: float = 5.0):
        """Advance one timestep. Returns boolean array of which neurons fired."""
        # Add thalamic noise + external input
        noise = np.random.randn(self.n) * noise_amplitude
        total_I = self.I + external_current + noise

        # Reset input accumulator
        self.I = np.zeros(self.n)

        # Apply refractory period
        in_refractory = self.refractory > 0
        total_I[in_refractory] = 0
        self.refractory = np.maximum(0, self.refractory - self.dt)

        # Izhikevich model update (0.5ms substeps for numerical stability)
        for _ in range(2):
            dv = (0.04 * self.v ** 2 + 5 * self.v + 140 - self.u + total_I) * (self.dt / 2)
            du = self.a * (self.b * self.v - self.u) * (self.dt / 2)
            self.v += dv
            self.u += du

        # Detect spikes (threshold = 30 mV)
        self.fired = self.v >= 30.0
        spike_count = np.sum(self.fired)
        self.spike_count = spike_count
        self.total_spikes += spike_count

        # Reset fired neurons
        if spike_count > 0:
            self.v[self.fired] = self.c[self.fired]
            self.u[self.fired] += self.d[self.fired]
            self.last_spike_time[self.fired] = t
            self.refractory[self.fired] = self.refractory_period

        # Clamp membrane potential
        self.v = np.clip(self.v, -100, 30)

        return self.fired

    def inject_current(self, current: np.ndarray):
        """Add current to specific neurons (from synaptic input)."""
        self.I += current

    def get_firing_rate(self) -> float:
        """Get instantaneous firing rate as fraction of population."""
        return self.spike_count / max(self.n, 1)

    def get_state(self) -> dict:
        """Get current state for visualization."""
        return {
            "firing_rate": float(self.get_firing_rate()),
            "mean_potential": float(np.mean(self.v)),
            "spike_count": int(self.spike_count),
            "active_neurons": int(np.sum(self.v > -50)),
        }
