"""
Brain Regions - 11 specialized neural populations modeled after biological neuroscience.

Each region has unique neuron dynamics, internal connectivity, and specialized processing.
"""

import numpy as np
from brain.neurons import NeuronPopulation
from brain.config import BrainConfig


class BrainRegion:
    """Base class for a brain region."""

    def __init__(self, name: str, n_neurons: int, params: dict, dt: float = 1.0):
        self.name = name
        self.n_neurons = n_neurons
        self.neurons = NeuronPopulation(n_neurons, **params, dt=dt)
        self.dt = dt

        # Region-specific state
        self.activity_history = []
        self.max_history = 100

        # 3D position for visualization (normalized coordinates)
        self.position = np.array([0.0, 0.0, 0.0])

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        """Process one timestep. Returns fired neurons."""
        fired = self.neurons.step(external_input, t, noise_amplitude=BrainConfig.THALAMIC_NOISE)
        rate = self.neurons.get_firing_rate()
        self.activity_history.append(rate)
        if len(self.activity_history) > self.max_history:
            self.activity_history.pop(0)
        return fired

    def get_state(self) -> dict:
        return {
            "name": self.name,
            "n_neurons": self.n_neurons,
            "firing_rate": float(self.neurons.get_firing_rate()),
            "mean_potential": float(np.mean(self.neurons.v)),
            "position": self.position.tolist(),
            **self.neurons.get_state(),
        }


class SensoryCortex(BrainRegion):
    """Processes raw sensory input (vision, audio, touch).
    Tonotopic/retinotopic organization with lateral inhibition."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("sensory_cortex", n_neurons,
                        BrainConfig.NEURON_PARAMS["sensory_cortex"], dt)
        self.position = np.array([0.0, 0.3, 0.8])
        # Subdivisions for different modalities
        third = n_neurons // 3
        self.vision_range = (0, third)
        self.audio_range = (third, 2 * third)
        self.touch_range = (2 * third, n_neurons)

    def encode_vision(self, image_features: np.ndarray) -> np.ndarray:
        """Encode visual features into spike currents."""
        current = np.zeros(self.n_neurons)
        v_start, v_end = self.vision_range
        n_vis = v_end - v_start
        if len(image_features) > 0:
            # Map features to neuron currents
            mapped = np.interp(
                np.linspace(0, 1, n_vis),
                np.linspace(0, 1, len(image_features)),
                image_features
            )
            current[v_start:v_end] = mapped * 20.0  # Scale to meaningful current
        return current

    def encode_audio(self, spectral_features: np.ndarray) -> np.ndarray:
        """Encode audio spectral features into spike currents."""
        current = np.zeros(self.n_neurons)
        a_start, a_end = self.audio_range
        n_aud = a_end - a_start
        if len(spectral_features) > 0:
            mapped = np.interp(
                np.linspace(0, 1, n_aud),
                np.linspace(0, 1, len(spectral_features)),
                spectral_features
            )
            current[a_start:a_end] = mapped * 15.0
        return current

    def encode_text(self, text_features: np.ndarray) -> np.ndarray:
        """Encode text features into spike currents (via touch/language area)."""
        current = np.zeros(self.n_neurons)
        t_start, t_end = self.touch_range
        n_txt = t_end - t_start
        if len(text_features) > 0:
            mapped = np.interp(
                np.linspace(0, 1, n_txt),
                np.linspace(0, 1, len(text_features)),
                text_features
            )
            current[t_start:t_end] = mapped * 18.0
        return current


class FeatureLayer(BrainRegion):
    """Edge/pattern detection via lateral inhibition.
    Extracts features from raw sensory data."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("feature_layer", n_neurons,
                        BrainConfig.NEURON_PARAMS["feature_layer"], dt)
        self.position = np.array([0.0, 0.1, 0.5])
        # Lateral inhibition weights (local competition)
        self.inhibition_strength = 0.3

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        # Apply lateral inhibition: active neurons suppress neighbors
        inhibition = np.zeros(self.n_neurons)
        if hasattr(self, '_prev_fired') and np.any(self._prev_fired):
            fired_idx = np.where(self._prev_fired)[0]
            for idx in fired_idx[:100]:  # Limit for performance
                lo = max(0, idx - 5)
                hi = min(self.n_neurons, idx + 6)
                inhibition[lo:hi] -= self.inhibition_strength
            inhibition[fired_idx] = 0  # Don't inhibit self

        fired = super().step(external_input + inhibition, t)
        self._prev_fired = fired.copy()
        return fired


class AssociationCortex(BrainRegion):
    """The brain's largest region - binds different sensory modalities together.
    Cross-modal associations via STDP learning."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("association", n_neurons,
                        BrainConfig.NEURON_PARAMS["association"], dt)
        self.position = np.array([0.0, 0.0, 0.0])
        # Association strength tracking
        self.binding_strength = np.zeros(n_neurons)

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        fired = super().step(external_input, t)
        # Track neurons that consistently fire together (binding)
        if np.any(fired):
            self.binding_strength[fired] += 0.01
            self.binding_strength *= 0.999  # Slow decay
        return fired


class ConceptLayer(BrainRegion):
    """Forms abstract concepts using winner-take-all competition.
    Sparse representations where only a few neurons fire for each concept."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("concept_layer", n_neurons,
                        BrainConfig.NEURON_PARAMS["concept_layer"], dt)
        self.position = np.array([0.0, -0.2, 0.0])
        self.wta_strength = 2.0  # Winner-take-all inhibition

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        # Winner-take-all: only top-k neurons can fire
        fired = super().step(external_input, t)
        if np.sum(fired) > 0:
            # Keep only top 5% most active
            k = max(1, int(self.n_neurons * 0.05))
            potentials = self.neurons.v.copy()
            potentials[~fired] = -100
            if np.sum(fired) > k:
                threshold = np.partition(potentials, -k)[-k]
                suppress = fired & (potentials < threshold)
                self.neurons.v[suppress] = self.neurons.c[suppress]
                fired[suppress] = False
        return fired


class PredictiveLayer(BrainRegion):
    """Continuously predicts what sensory input comes next.
    When prediction error is high, the brain pays attention and learns faster."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("predictive", n_neurons,
                        BrainConfig.NEURON_PARAMS["predictive"], dt)
        self.position = np.array([0.3, 0.3, 0.3])
        self.prediction = np.zeros(n_neurons)
        self.prediction_error = 0.0
        self.surprise = 0.0

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        fired = super().step(external_input, t)
        # Compute prediction error
        actual = fired.astype(np.float32)
        self.prediction_error = float(np.mean(np.abs(actual - self.prediction)))
        self.surprise = min(1.0, self.prediction_error * 7.0)
        # Update prediction (exponential moving average — more adaptive)
        self.prediction = 0.82 * self.prediction + 0.18 * actual
        return fired

    def get_state(self) -> dict:
        state = super().get_state()
        state["prediction_error"] = self.prediction_error
        state["surprise"] = self.surprise
        return state


class MotorCortex(BrainRegion):
    """Generates motor commands for movement and speech output.
    All outputs pass through the safety kernel."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("motor_cortex", n_neurons,
                        BrainConfig.NEURON_PARAMS["motor_cortex"], dt)
        self.position = np.array([-0.3, 0.4, 0.2])
        self.motor_output = np.zeros(n_neurons)

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        fired = super().step(external_input, t)
        # Decode motor output from firing pattern
        self.motor_output = fired.astype(np.float32)
        return fired

    def get_motor_command(self) -> np.ndarray:
        return self.motor_output


class Cerebellum(BrainRegion):
    """Timing, coordination, and motor learning.
    Provides error correction for motor commands."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("cerebellum", n_neurons,
                        BrainConfig.NEURON_PARAMS["cerebellum"], dt)
        self.position = np.array([0.0, -0.5, -0.3])
        self.timing_buffer = np.zeros((10, n_neurons))
        self.timing_idx = 0

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        fired = super().step(external_input, t)
        # Store timing information in circular buffer
        self.timing_buffer[self.timing_idx % 10] = fired.astype(np.float32)
        self.timing_idx += 1
        return fired


class ReflexArc(BrainRegion):
    """Fast stimulus-response pathways.
    Bypasses higher processing for urgent responses."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("reflex_arc", n_neurons,
                        BrainConfig.NEURON_PARAMS["reflex_arc"], dt)
        self.position = np.array([0.0, -0.3, 0.5])
        self.reflex_threshold = 0.3
        self.reflex_active = False

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        fired = super().step(external_input, t)
        rate = self.neurons.get_firing_rate()
        self.reflex_active = rate > self.reflex_threshold
        return fired


class Brainstem(BrainRegion):
    """Manages energy and survival homeostasis.
    Controls arousal, sleep/wake cycles, basic life functions."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("brainstem", n_neurons,
                        BrainConfig.NEURON_PARAMS["brainstem"], dt)
        self.position = np.array([0.0, -0.6, -0.1])
        # Homeostatic variables
        self.energy = 1.0
        self.arousal = 0.5

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        fired = super().step(external_input, t)
        # Energy management
        total_activity = self.neurons.get_firing_rate()
        self.energy -= total_activity * 0.001  # Activity costs energy
        self.energy = np.clip(self.energy + 0.0005, 0.1, 1.0)  # Slow recovery
        # Arousal tracks overall brain activity
        self.arousal = 0.95 * self.arousal + 0.05 * total_activity
        return fired

    def get_state(self) -> dict:
        state = super().get_state()
        state["energy"] = float(self.energy)
        state["arousal"] = float(self.arousal)
        return state


class Hippocampus(BrainRegion):
    """Memory consolidation and replay.
    Stores episodic memories and replays them during low-activity periods."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("hippocampus", n_neurons,
                        BrainConfig.NEURON_PARAMS["hippocampus"], dt)
        self.position = np.array([0.2, -0.1, -0.2])
        # Memory buffer (stores spike patterns with strength tracking)
        self.memory_buffer = []
        self._memory_strength = []
        self.max_memories = 1000
        self.replay_mode = False

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        fired = super().step(external_input, t)
        rate = self.neurons.get_firing_rate()

        # Store significant activity patterns with strength tracking
        if rate > 0.05 and not self.replay_mode:
            pattern = fired.copy()
            # Check similarity to recent memories — don't store near-duplicates
            is_novel = True
            if len(self.memory_buffer) > 0:
                last = self.memory_buffer[-1]
                overlap = np.sum(pattern & last) / max(np.sum(pattern | last), 1)
                if overlap > 0.8:
                    is_novel = False

            if is_novel:
                self.memory_buffer.append(pattern)
                self._memory_strength.append(1.0)
                if len(self.memory_buffer) > self.max_memories:
                    # Remove weakest memory, not oldest
                    weakest = np.argmin(self._memory_strength)
                    self.memory_buffer.pop(weakest)
                    self._memory_strength.pop(weakest)

        # Replay memories during low activity — weighted by recency + strength
        if rate < 0.01 and len(self.memory_buffer) > 0:
            self.replay_mode = True
            n = len(self.memory_buffer)
            # Recency weight: recent memories replay more
            recency = np.linspace(0.3, 1.0, n)
            strength = np.array(self._memory_strength)
            weights = recency * strength
            weights /= weights.sum()
            idx = np.random.choice(n, p=weights)
            replay_pattern = self.memory_buffer[idx]
            self.neurons.inject_current(replay_pattern.astype(np.float32) * 10.0)
            # Strengthen replayed memory (consolidation)
            self._memory_strength[idx] = min(self._memory_strength[idx] * 1.05, 5.0)
            # Decay others slightly
            for i in range(n):
                if i != idx:
                    self._memory_strength[i] *= 0.999
        else:
            self.replay_mode = False

        return fired

    def get_state(self) -> dict:
        state = super().get_state()
        state["memories_stored"] = len(self.memory_buffer)
        state["replay_mode"] = self.replay_mode
        return state


class PrefrontalCortex(BrainRegion):
    """Working memory and planning.
    Maintains persistent activity for goals and context."""

    def __init__(self, n_neurons: int, dt: float = 1.0):
        super().__init__("prefrontal", n_neurons,
                        BrainConfig.NEURON_PARAMS["prefrontal"], dt)
        self.position = np.array([0.0, 0.5, 0.4])
        # Working memory: persistent activity (stronger loop → higher sustained PFC drive)
        self.working_memory = np.zeros(n_neurons, dtype=np.float32)
        self.wm_decay = 0.997

    def step(self, external_input: np.ndarray, t: float) -> np.ndarray:
        # Working memory provides sustained input
        total_input = external_input + self.working_memory * 4.0
        fired = super().step(total_input, t)
        # Update working memory: strengthen for active neurons, decay for inactive
        self.working_memory *= self.wm_decay
        if np.any(fired):
            self.working_memory[fired] = np.minimum(
                self.working_memory[fired] + 0.12, 1.0
            )
        return fired


# Factory function to create all regions
def create_all_regions(total_neurons: int = None, dt: float = 1.0) -> dict:
    """Create all 11 brain regions with proper neuron counts."""
    if total_neurons is None:
        total_neurons = BrainConfig.TOTAL_NEURONS

    regions = {}
    region_classes = {
        "sensory_cortex": SensoryCortex,
        "feature_layer": FeatureLayer,
        "association": AssociationCortex,
        "concept_layer": ConceptLayer,
        "predictive": PredictiveLayer,
        "motor_cortex": MotorCortex,
        "cerebellum": Cerebellum,
        "reflex_arc": ReflexArc,
        "brainstem": Brainstem,
        "hippocampus": Hippocampus,
        "prefrontal": PrefrontalCortex,
    }

    for name, cls in region_classes.items():
        n = max(100, int(total_neurons * BrainConfig.REGION_PROPORTIONS[name]))
        regions[name] = cls(n, dt=dt)

    return regions
