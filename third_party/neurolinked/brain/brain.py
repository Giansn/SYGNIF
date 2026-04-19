"""
Brain Orchestrator - Main simulation loop.

Coordinates all 11 brain regions, manages inter-region synaptic connections,
runs STDP learning, and streams state to the visualization dashboard.
"""

import time
import numpy as np
from brain.config import BrainConfig
from brain.regions import create_all_regions
from brain.synapses import SynapseMatrix
from brain.safety import SafetyKernel


class Brain:
    """The complete neuromorphic brain."""

    def __init__(self, total_neurons: int = None):
        self.config = BrainConfig
        if total_neurons is None:
            total_neurons = BrainConfig.TOTAL_NEURONS

        print(f"[BRAIN] Initializing with {total_neurons:,} neurons...")
        self.total_neurons = total_neurons
        self.dt = BrainConfig.DT
        self.step_count = 0
        self.start_time = time.time()

        # Dedicated RNG for spontaneous baseline activity (thread-safe, reproducible)
        self._rng = np.random.default_rng()

        # Create all brain regions
        print("[BRAIN] Creating brain regions...")
        self.regions = create_all_regions(total_neurons, self.dt)

        # Create inter-region synaptic connections
        print("[BRAIN] Wiring synaptic connections...")
        self.connections = {}
        self.total_synapses = 0
        for (src, dst), prob in BrainConfig.CONNECTIVITY.items():
            if src in self.regions and dst in self.regions:
                syn = SynapseMatrix(
                    self.regions[src].n_neurons,
                    self.regions[dst].n_neurons,
                    prob
                )
                self.connections[(src, dst)] = syn
                self.total_synapses += syn.nnz

        print(f"[BRAIN] Total synapses: {self.total_synapses:,}")

        # Safety kernel
        self.safety = SafetyKernel()

        # Neuromodulators (global brain state)
        self.neuromodulators = {
            "dopamine": BrainConfig.DOPAMINE_BASELINE,
            "acetylcholine": BrainConfig.ACETYLCHOLINE_BASELINE,
            "norepinephrine": BrainConfig.NOREPINEPHRINE_BASELINE,
            "serotonin": BrainConfig.SEROTONIN_BASELINE,
        }

        # Development stage tracking
        self.development_stage = "EMBRYONIC"

        # Performance tracking
        self.steps_per_second = 0.0
        self._step_times = []

        # Input queue
        self._pending_sensory = {
            "vision": np.array([]),
            "audio": np.array([]),
            "text": np.array([]),
        }
        # When True, next step adds stronger direct current onto prefrontal (Sygnif / executive feeds).
        self._pending_executive_text_boost = False

        print("[BRAIN] Initialization complete!")

    def inject_sensory_input(
        self,
        modality: str,
        features: np.ndarray,
        *,
        executive_boost: bool = False,
    ):
        """Queue sensory input for next processing step.

        ``executive_boost`` (text modality only): extra prefrontal drive for structured /
        Sygnif telemetry so working-memory / planning regions track trading context.
        """
        self._pending_sensory[modality] = features
        if modality == "text":
            self._pending_executive_text_boost = bool(executive_boost)

    def _pfc_text_bias_current(self, features: np.ndarray, *, gain: float) -> np.ndarray:
        """Map encoded text features (1D) onto prefrontal neuron currents."""
        pf = self.regions.get("prefrontal")
        if pf is None or gain <= 0.0:
            return np.array([], dtype=np.float32)
        feat = np.asarray(features, dtype=np.float32).ravel()
        n = pf.n_neurons
        if feat.size == 0:
            return np.zeros(n, dtype=np.float32)
        if feat.size >= n:
            out = feat[:n].copy()
        else:
            reps = int(np.ceil(n / max(1, feat.size)))
            out = np.tile(feat, reps)[:n].astype(np.float32, copy=False)
        out *= np.float32(gain)
        return out

    def step(self):
        """Run one simulation timestep across all regions."""
        t = self.step_count * self.dt
        step_start = time.time()

        # --- 1. Process sensory input ---
        sensory = self.regions["sensory_cortex"]
        sensory_current = np.zeros(sensory.n_neurons)

        if len(self._pending_sensory["vision"]) > 0:
            sensory_current += sensory.encode_vision(self._pending_sensory["vision"])
        if len(self._pending_sensory["audio"]) > 0:
            sensory_current += sensory.encode_audio(self._pending_sensory["audio"])
        if len(self._pending_sensory["text"]) > 0:
            sensory_current += sensory.encode_text(self._pending_sensory["text"])

        # --- 2. Step each region with inter-region input ---
        fired = {}
        region_inputs = {name: np.zeros(r.n_neurons) for name, r in self.regions.items()}

        # Add sensory input
        region_inputs["sensory_cortex"] += sensory_current

        # Direct text → prefrontal executive bias (parallel to sensory→association path)
        if len(self._pending_sensory.get("text", [])) > 0:
            gain = (
                float(self.config.PFC_TEXT_EXECUTIVE_BIAS_GAIN)
                if self._pending_executive_text_boost
                else float(self.config.PFC_TEXT_BIAS_GAIN)
            )
            if gain > 0.0:
                region_inputs["prefrontal"] += self._pfc_text_bias_current(
                    self._pending_sensory["text"],
                    gain=gain,
                )

        # --- Baseline spontaneous activity ---
        # Real brains always have background firing (~1-5 Hz). Without this, a
        # brand-new brain with no input stays silent forever. This gives the
        # "always thinking" feel — scales with arousal (norepinephrine) and
        # attention (acetylcholine), so user activity makes it think harder.
        arousal = 0.5 + 0.5 * self.neuromodulators.get("norepinephrine", 0.1)
        attention = 0.7 + 0.6 * self.neuromodulators.get("acetylcholine", 0.3)
        noise_scale = 2.5 * arousal
        for name, region in self.regions.items():
            # Brainstem and sensory cortex get more noise (these are the "pacemakers")
            if name in ("brainstem", "sensory_cortex"):
                scale = noise_scale * attention * 1.8
            elif name in ("reflex_arc", "hippocampus"):
                scale = noise_scale * attention * 1.2
            else:
                scale = noise_scale * attention * 0.6
            region_inputs[name] += self._rng.normal(0, scale, region.n_neurons)

        # Propagate spikes through inter-region connections (from previous step)
        if self.step_count > 0:
            for (src, dst), syn in self.connections.items():
                if src in fired:
                    current = syn.propagate(fired[src])
                    region_inputs[dst] += current

        # Step all regions
        for name, region in self.regions.items():
            # Modulate input by neuromodulators
            modulated_input = region_inputs[name] * (
                1.0 + self.neuromodulators["acetylcholine"] * 0.5
            )
            fired[name] = region.step(modulated_input, t)

        # --- 3. Safety kernel checks motor output ---
        if "motor_cortex" in fired:
            motor_cmd = self.regions["motor_cortex"].get_motor_command()
            safe_output, is_safe, reason = self.safety.check(motor_cmd)
            if not is_safe and self.regions["reflex_arc"].reflex_active:
                self.safety.trigger_reflex_withdrawal()

        # --- 4. STDP learning (every step — real-time learning) ---
        for (src, dst), syn in self.connections.items():
            if src in fired and dst in fired:
                # Modulate learning by dopamine AND development stage
                syn.modulation = self.neuromodulators["dopamine"] * self._stage_learning_rate()
                syn.update_stdp(fired[src], fired[dst], self.dt)

        # --- 5. Synapse pruning (cut weak connections, strengthen strong ones) ---
        if self.step_count % 1000 == 0 and self.step_count > 0:
            self._prune_synapses()

        # --- 6. Update neuromodulators ---
        self._update_neuromodulators(fired)

        # --- 7. Update development stage ---
        self._update_development_stage()

        # --- 7. Clear sensory input ---
        self._pending_sensory = {
            "vision": np.array([]),
            "audio": np.array([]),
            "text": np.array([]),
        }
        self._pending_executive_text_boost = False

        # Performance tracking
        step_time = time.time() - step_start
        self._step_times.append(step_time)
        if len(self._step_times) > 100:
            self._step_times.pop(0)
        self.steps_per_second = 1.0 / max(np.mean(self._step_times), 1e-6)

        self.step_count += 1

    def _update_neuromodulators(self, fired: dict):
        """Update global neuromodulatory state based on brain activity."""
        # Dopamine: increases with prediction error (novelty/reward)
        if "predictive" in self.regions:
            pred = self.regions["predictive"]
            surprise = pred.surprise if hasattr(pred, 'surprise') else 0
            self.neuromodulators["dopamine"] = np.clip(
                0.9 * self.neuromodulators["dopamine"] + 0.1 * (0.5 + surprise * 0.5),
                0.1, 1.0
            )

        # Acetylcholine: increases with attention/sensory input
        sensory_rate = self.regions["sensory_cortex"].neurons.get_firing_rate()
        self.neuromodulators["acetylcholine"] = np.clip(
            0.95 * self.neuromodulators["acetylcholine"] + 0.05 * (0.3 + sensory_rate * 2),
            0.1, 1.0
        )

        # Norepinephrine: increases with arousal
        if "brainstem" in self.regions:
            arousal = self.regions["brainstem"].arousal
            self.neuromodulators["norepinephrine"] = np.clip(
                0.95 * self.neuromodulators["norepinephrine"] + 0.05 * arousal,
                0.1, 1.0
            )

        # Serotonin: inversely related to stress/high activity
        total_rate = np.mean([r.neurons.get_firing_rate() for r in self.regions.values()])
        self.neuromodulators["serotonin"] = np.clip(
            0.95 * self.neuromodulators["serotonin"] + 0.05 * (1.0 - total_rate * 2),
            0.1, 1.0
        )

    def _stage_learning_rate(self) -> float:
        """Return learning rate multiplier based on development stage."""
        rates = {
            "EMBRYONIC": 2.0,    # Fast learning — forming initial connections
            "JUVENILE": 1.5,     # Still learning quickly
            "ADOLESCENT": 1.0,   # Normal learning rate
            "MATURE": 0.6,       # Slower — stable, refined connections
        }
        return rates.get(self.development_stage, 1.0)

    def _prune_synapses(self):
        """Cut weak synapses and strengthen strong ones (competitive learning).
        More aggressive pruning as the brain matures."""
        prune_thresholds = {
            "EMBRYONIC": 0.12,    # Faster initial pruning for performance
            "JUVENILE": 0.18,     # More aggressive cutting
            "ADOLESCENT": 0.15,   # Moderate pruning
            "MATURE": 0.2,        # Aggressive — only strong survive
        }
        threshold = prune_thresholds.get(self.development_stage, 0.1)
        total_pruned = 0

        for (src, dst), syn in self.connections.items():
            if syn.nnz == 0:
                continue
            data = syn.weights.data
            # Find weak synapses below threshold
            weak_mask = np.abs(data) < threshold
            n_weak = int(np.sum(weak_mask))
            if n_weak > 0:
                # Prune: set weak weights to zero
                data[weak_mask] = 0
                syn.weights.eliminate_zeros()
                total_pruned += n_weak

            # Strengthen strong synapses slightly (Hebbian consolidation)
            if len(syn.weights.data) > 0:
                strong_mask = syn.weights.data > (BrainConfig.STDP_W_MAX * 0.7)
                syn.weights.data[strong_mask] = np.clip(
                    syn.weights.data[strong_mask] * 1.001,
                    0, BrainConfig.STDP_W_MAX
                )

        if total_pruned > 0:
            self.total_synapses = sum(s.nnz for s in self.connections.values())

    def _update_development_stage(self):
        """Track brain maturation based on total simulation steps."""
        for stage, (low, high) in BrainConfig.STAGES.items():
            if low <= self.step_count < high:
                self.development_stage = stage
                break

    def get_state(self) -> dict:
        """Get complete brain state for visualization."""
        regions_state = {}
        for name, region in self.regions.items():
            regions_state[name] = region.get_state()

        # Compute total firing rates per region
        region_firing = {
            name: float(region.neurons.get_firing_rate() * 100)
            for name, region in self.regions.items()
        }

        return {
            "step": self.step_count,
            "total_neurons": self.total_neurons,
            "total_synapses": self.total_synapses,
            "steps_per_second": round(self.steps_per_second, 2),
            "development_stage": self.development_stage,
            "neuromodulators": {k: round(v, 3) for k, v in self.neuromodulators.items()},
            "regions": regions_state,
            "region_firing": region_firing,
            "safety": self.safety.get_state(),
            "uptime": round(time.time() - self.start_time, 1),
        }

    def get_neuron_positions(self) -> dict:
        """Get 3D positions for all neurons (for initial visualization setup)."""
        positions = {}
        for name, region in self.regions.items():
            n = region.n_neurons
            center = region.position
            # Distribute neurons in a cloud around the region center
            spread = 0.15 + (n / self.total_neurons) * 0.3
            pos = np.random.randn(n, 3) * spread + center
            positions[name] = {
                "center": center.tolist(),
                "count": n,
                "positions": pos.tolist()[:min(n, 5000)],  # Limit for transfer
                "spread": float(spread),
            }
        return positions
