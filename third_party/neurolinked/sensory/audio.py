"""
Audio → Spike Encoding

Captures microphone input and converts spectral features to spikes.
Uses FFT-based spectral analysis for frequency decomposition.
"""

import numpy as np

try:
    import sounddevice as sd
    HAS_AUDIO = True
except (ImportError, OSError):
    HAS_AUDIO = False


class AudioEncoder:
    """Encodes audio input (microphone) into neural spike patterns."""

    def __init__(self, feature_dim: int = 256, sample_rate: int = 16000, chunk_size: int = 1024):
        self.feature_dim = feature_dim
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.active = False
        self.buffer = np.zeros(chunk_size, dtype=np.float32)
        self.stream = None

    def start_microphone(self):
        """Start microphone capture. Requires sounddevice."""
        if not HAS_AUDIO:
            print("[AUDIO] ERROR: sounddevice not installed. Run: pip install sounddevice")
            print("[AUDIO] Microphone will NOT work without sounddevice.")
            return False
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                blocksize=self.chunk_size,
                dtype='float32',
                callback=self._audio_callback
            )
            self.stream.start()
            self.active = True
            print("[AUDIO] Microphone started")
            return True
        except Exception as e:
            print(f"[AUDIO] Failed to start microphone: {e}")
            return False

    def stop_microphone(self):
        """Stop microphone capture."""
        if self.stream:
            self.stream.stop()
            self.stream.close()
        self.active = False

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback for audio stream."""
        self.buffer = indata[:, 0].copy()

    def capture_audio(self) -> np.ndarray:
        """Capture and encode current audio buffer."""
        if not self.active:
            return self._synthetic_input()
        return self._encode_audio(self.buffer)

    def _encode_audio(self, audio: np.ndarray) -> np.ndarray:
        """Process audio buffer into spike features."""
        features = np.zeros(self.feature_dim, dtype=np.float32)
        quarter = self.feature_dim // 4

        # Feature 1: Spectral magnitude (FFT)
        fft = np.fft.rfft(audio * np.hanning(len(audio)))
        magnitudes = np.abs(fft)
        # Resample to feature dimension
        spectral = np.interp(
            np.linspace(0, 1, quarter),
            np.linspace(0, 1, len(magnitudes)),
            magnitudes
        )
        spectral = spectral / (spectral.max() + 1e-8)
        features[:quarter] = spectral

        # Feature 2: Mel-scale approximation (log frequency bands)
        mel_bands = quarter
        mel_features = np.zeros(mel_bands)
        freq_per_bin = self.sample_rate / (2 * len(magnitudes))
        for i in range(mel_bands):
            # Mel-scale center frequency
            mel_low = 700 * (10 ** (i / mel_bands * 2.5) - 1)
            mel_high = 700 * (10 ** ((i + 1) / mel_bands * 2.5) - 1)
            bin_low = int(mel_low / freq_per_bin)
            bin_high = int(mel_high / freq_per_bin)
            bin_high = min(bin_high, len(magnitudes) - 1)
            if bin_low < bin_high:
                mel_features[i] = np.mean(magnitudes[bin_low:bin_high])
        mel_features = mel_features / (mel_features.max() + 1e-8)
        features[quarter:2*quarter] = mel_features

        # Feature 3: Temporal envelope
        envelope = np.abs(audio)
        env_pooled = np.interp(
            np.linspace(0, 1, quarter),
            np.linspace(0, 1, len(envelope)),
            envelope
        )
        features[2*quarter:3*quarter] = env_pooled / (env_pooled.max() + 1e-8)

        # Feature 4: Zero-crossing rate + energy features
        zcr = np.sum(np.abs(np.diff(np.sign(audio)))) / (2 * len(audio))
        energy = np.mean(audio ** 2)
        features[3*quarter] = min(zcr * 5, 1.0)
        features[3*quarter + 1] = min(energy * 100, 1.0)
        # Spectral centroid
        freqs = np.arange(len(magnitudes)) * freq_per_bin
        centroid = np.sum(freqs * magnitudes) / (np.sum(magnitudes) + 1e-8)
        features[3*quarter + 2] = min(centroid / 4000, 1.0)

        return np.clip(features, 0, 1)

    def _synthetic_input(self) -> np.ndarray:
        """Generate synthetic audio input when no microphone available."""
        features = np.zeros(self.feature_dim, dtype=np.float32)
        # Simulate ambient noise with occasional peaks
        features = np.random.exponential(0.05, self.feature_dim).astype(np.float32)
        features = np.clip(features, 0, 1)
        return features
