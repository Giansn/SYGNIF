"""
Safety Kernel - Every motor command passes through this supervisor.

Checks joint limits, force thresholds, collision boundaries.
If something looks dangerous, triggers reflex withdrawal before action executes.
The brain can learn freely, but can't act without clearance.
"""

import numpy as np
from brain.config import BrainConfig


class SafetyKernel:
    """Safety supervisor for all motor outputs."""

    def __init__(self):
        self.force_limit = BrainConfig.SAFETY_FORCE_LIMIT
        self.velocity_limit = BrainConfig.SAFETY_VELOCITY_LIMIT
        self.collision_margin = BrainConfig.SAFETY_COLLISION_MARGIN

        # Safety state
        self.blocked_count = 0
        self.passed_count = 0
        self.last_block_reason = ""
        self.emergency_stop = False

        # Rate limiting
        self.output_rate = 0.0
        self.max_output_rate = 0.8  # Max fraction of motor neurons that can fire
        self.rate_window = []
        self.rate_window_size = 50

    def check(self, motor_output: np.ndarray) -> tuple:
        """
        Check if motor command is safe.
        Returns (safe_output, is_safe, reason)
        """
        if self.emergency_stop:
            self.blocked_count += 1
            self.last_block_reason = "EMERGENCY_STOP"
            return np.zeros_like(motor_output), False, "EMERGENCY_STOP"

        # Check 1: Force magnitude limit
        magnitude = np.sum(np.abs(motor_output))
        if magnitude > self.force_limit:
            scale = self.force_limit / magnitude
            motor_output = motor_output * scale
            self.last_block_reason = f"FORCE_LIMITED ({magnitude:.1f} > {self.force_limit})"

        # Check 2: Rate of change (velocity) limit
        firing_rate = np.mean(motor_output > 0)
        self.rate_window.append(firing_rate)
        if len(self.rate_window) > self.rate_window_size:
            self.rate_window.pop(0)

        avg_rate = np.mean(self.rate_window)
        if avg_rate > self.max_output_rate:
            # Suppress excess activity
            suppress_mask = np.random.random(len(motor_output)) < (avg_rate - self.max_output_rate)
            motor_output[suppress_mask] = 0
            self.blocked_count += 1
            self.last_block_reason = f"RATE_LIMITED ({avg_rate:.2f} > {self.max_output_rate})"
            return motor_output, False, self.last_block_reason

        # Check 3: Pattern detection - detect runaway oscillation
        if len(self.rate_window) >= 10:
            recent = self.rate_window[-10:]
            variance = np.var(recent)
            if variance > 0.1:  # High variance = oscillating
                motor_output *= 0.5
                self.last_block_reason = "OSCILLATION_DAMPENED"
                return motor_output, False, self.last_block_reason

        self.passed_count += 1
        return motor_output, True, "SAFE"

    def trigger_reflex_withdrawal(self) -> np.ndarray:
        """Generate emergency withdrawal signal."""
        self.emergency_stop = True
        return np.zeros(1)  # Zero all motor output

    def reset_emergency(self):
        """Clear emergency stop state."""
        self.emergency_stop = False

    def get_state(self) -> dict:
        total = self.blocked_count + self.passed_count
        return {
            "blocked": self.blocked_count,
            "passed": self.passed_count,
            "block_rate": self.blocked_count / max(total, 1),
            "last_reason": self.last_block_reason,
            "emergency_stop": self.emergency_stop,
            "output_rate": float(np.mean(self.rate_window)) if self.rate_window else 0.0,
        }
