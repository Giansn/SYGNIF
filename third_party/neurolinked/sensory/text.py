"""
Text → Spike Encoding

Converts text input into spike-compatible feature vectors.
Uses character-level and word-level encoding for rich representations.
"""

import numpy as np


class TextEncoder:
    """Encodes text into neural spike patterns."""

    def __init__(self, feature_dim: int = 256):
        self.feature_dim = feature_dim
        # Character embedding matrix (random but fixed)
        np.random.seed(42)
        self.char_embeddings = np.random.randn(256, feature_dim // 4).astype(np.float32) * 0.5
        np.random.seed(None)

        # Word hash table for semantic grouping
        self.word_vectors = {}
        self.next_word_id = 0

    def encode(self, text: str) -> np.ndarray:
        """Convert text to feature vector for neural input."""
        if not text or len(text.strip()) == 0:
            return np.array([])

        features = np.zeros(self.feature_dim, dtype=np.float32)

        # Character-level encoding (first quarter of features)
        char_features = np.zeros(self.feature_dim // 4)
        for i, ch in enumerate(text[:64]):  # First 64 chars
            code = ord(ch) % 256
            char_features += self.char_embeddings[code] * np.exp(-i * 0.05)
        features[:self.feature_dim // 4] = char_features / max(len(text), 1)

        # Word-level encoding (second quarter)
        words = text.lower().split()
        word_features = np.zeros(self.feature_dim // 4)
        for i, word in enumerate(words[:32]):
            if word not in self.word_vectors:
                # Create deterministic random vector for new words
                rng = np.random.RandomState(hash(word) % (2**31))
                self.word_vectors[word] = rng.randn(self.feature_dim // 4).astype(np.float32) * 0.3
                self.next_word_id += 1
            word_features += self.word_vectors[word] * np.exp(-i * 0.1)
        features[self.feature_dim // 4: self.feature_dim // 2] = word_features / max(len(words), 1)

        # Bigram features (third quarter)
        bigram_features = np.zeros(self.feature_dim // 4)
        for i in range(len(words) - 1):
            bigram = words[i] + "_" + words[i + 1]
            rng = np.random.RandomState(hash(bigram) % (2**31))
            bigram_features += rng.randn(self.feature_dim // 4).astype(np.float32) * 0.2
        features[self.feature_dim // 2: 3 * self.feature_dim // 4] = bigram_features / max(len(words) - 1, 1)

        # Structural features (fourth quarter)
        struct = np.zeros(self.feature_dim // 4)
        struct[0] = len(text) / 100.0                    # Length
        struct[1] = len(words) / 20.0                    # Word count
        struct[2] = sum(1 for c in text if c.isupper()) / max(len(text), 1)  # Caps ratio
        struct[3] = text.count('?') * 0.5                # Question marks
        struct[4] = text.count('!') * 0.5                # Exclamation marks
        struct[5] = text.count('.') * 0.3                # Periods
        struct[6] = np.mean([len(w) for w in words]) / 10.0 if words else 0  # Avg word length
        features[3 * self.feature_dim // 4:] = struct

        # Normalize to [0, 1] range for spike encoding
        features = np.clip(features, -3, 3)
        features = (features - features.min()) / (features.max() - features.min() + 1e-8)

        return features
