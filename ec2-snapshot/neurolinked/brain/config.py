"""
NeuroLinked Brain Configuration
All hyperparameters for the neuromorphic brain system.
"""

import numpy as np


class BrainConfig:
    # --- Scale ---
    TOTAL_NEURONS = 100_000  # Default 100K, set to 1_000_000 for full scale
    SYNAPSES_PER_NEURON = 1200  # Average connections per neuron

    # --- Region proportions (must sum to 1.0) ---
    REGION_PROPORTIONS = {
        "sensory_cortex":    0.107,
        "feature_layer":     0.101,
        "association":       0.364,
        "concept_layer":     0.038,
        "predictive":        0.156,
        "motor_cortex":      0.025,
        "cerebellum":        0.075,
        "reflex_arc":        0.046,
        "brainstem":         0.027,
        "hippocampus":       0.033,
        # Executive / WM hub — slightly larger share (taken from association budget)
        "prefrontal":        0.028,
    }

    # --- Izhikevich neuron parameters by region ---
    # (a, b, c, d) - different firing patterns per region
    NEURON_PARAMS = {
        "sensory_cortex":  {"a": 0.02, "b": 0.2, "c": -65, "d": 8},     # Regular spiking
        "feature_layer":   {"a": 0.02, "b": 0.25, "c": -65, "d": 8},    # Regular spiking
        "association":     {"a": 0.02, "b": 0.2, "c": -50, "d": 2},     # Chattering
        "concept_layer":   {"a": 0.02, "b": 0.2, "c": -55, "d": 4},    # Intrinsically bursting
        "predictive":      {"a": 0.1, "b": 0.2, "c": -65, "d": 2},     # Fast spiking
        "motor_cortex":    {"a": 0.02, "b": 0.2, "c": -65, "d": 8},    # Regular spiking
        "cerebellum":      {"a": 0.1, "b": 0.2, "c": -65, "d": 2},     # Fast spiking
        "reflex_arc":      {"a": 0.1, "b": 0.26, "c": -65, "d": 2},    # Fast spiking
        "brainstem":       {"a": 0.02, "b": 0.25, "c": -65, "d": 0.05}, # Low-threshold
        "hippocampus":     {"a": 0.02, "b": 0.2, "c": -50, "d": 2},    # Chattering
        "prefrontal":      {"a": 0.02, "b": 0.2, "c": -55, "d": 4},    # Intrinsically bursting
    }

    # --- Connectivity matrix (source -> target probability) ---
    # Sparse: only define non-zero connections
    CONNECTIVITY = {
        ("sensory_cortex", "feature_layer"):  0.15,
        ("sensory_cortex", "reflex_arc"):     0.10,
        ("feature_layer", "association"):     0.20,
        ("feature_layer", "concept_layer"):   0.08,
        ("association", "concept_layer"):     0.12,
        ("association", "predictive"):        0.15,
        ("association", "hippocampus"):       0.10,
        ("association", "prefrontal"):        0.12,
        ("concept_layer", "association"):     0.10,
        ("concept_layer", "predictive"):      0.10,
        ("concept_layer", "prefrontal"):      0.12,
        ("predictive", "association"):        0.12,
        ("predictive", "sensory_cortex"):     0.05,
        ("predictive", "motor_cortex"):       0.06,
        ("prefrontal", "motor_cortex"):       0.15,
        ("prefrontal", "association"):        0.08,
        ("prefrontal", "predictive"):         0.06,
        ("motor_cortex", "cerebellum"):       0.12,
        ("motor_cortex", "brainstem"):        0.10,
        ("cerebellum", "motor_cortex"):       0.10,
        ("cerebellum", "brainstem"):          0.05,
        ("reflex_arc", "motor_cortex"):       0.15,
        ("reflex_arc", "brainstem"):          0.08,
        ("brainstem", "sensory_cortex"):      0.03,
        ("brainstem", "motor_cortex"):        0.05,
        ("hippocampus", "association"):       0.12,
        ("hippocampus", "prefrontal"):        0.11,
        ("hippocampus", "concept_layer"):     0.06,
    }

    # --- STDP Learning ---
    STDP_TAU_PLUS = 20.0     # ms, LTP time constant
    STDP_TAU_MINUS = 20.0    # ms, LTD time constant
    STDP_A_PLUS = 0.015      # LTP amplitude — boosted for faster predictive learning
    STDP_A_MINUS = 0.012     # LTD amplitude (slightly stronger for stability)
    STDP_W_MAX = 1.0         # Maximum synapse weight
    STDP_W_MIN = 0.0         # Minimum synapse weight

    # --- Prefrontal executive bias (text sensory → direct PFC current) ---
    # ``PFC_TEXT_BIAS_GAIN``: optional bias on any text input (0 = off).
    # ``PFC_TEXT_EXECUTIVE_BIAS_GAIN``: Sygnif / structured telemetry (``executive_boost`` from server).
    PFC_TEXT_BIAS_GAIN = 0.0
    PFC_TEXT_EXECUTIVE_BIAS_GAIN = 22.0

    # --- Neuromodulators ---
    DOPAMINE_BASELINE = 0.5
    ACETYLCHOLINE_BASELINE = 0.5
    NOREPINEPHRINE_BASELINE = 0.3
    SEROTONIN_BASELINE = 0.5

    # --- Simulation ---
    DT = 1.0                 # Timestep in ms
    STEPS_PER_UPDATE = 10    # Steps per WebSocket update
    THALAMIC_NOISE = 5.0     # Background noise amplitude

    # --- Safety kernel ---
    SAFETY_FORCE_LIMIT = 100.0
    SAFETY_VELOCITY_LIMIT = 50.0
    SAFETY_COLLISION_MARGIN = 0.1

    # --- Development stages ---
    STAGES = {
        "EMBRYONIC":    (0, 100_000),
        "JUVENILE":     (100_000, 2_000_000),
        "ADOLESCENT":   (2_000_000, 10_000_000),
        "MATURE":       (10_000_000, float("inf")),
    }

    # --- Server ---
    HOST = "0.0.0.0"
    PORT = 8000
    WS_UPDATE_RATE = 30  # Hz
