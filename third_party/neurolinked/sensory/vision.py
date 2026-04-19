"""
Vision → Spike Encoding

Captures webcam frames and converts to spike-compatible features.
Uses edge detection and spatial pooling for efficient encoding.
"""

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


class VisionEncoder:
    """Encodes visual input (webcam or images) into neural spike patterns."""

    def __init__(self, feature_dim: int = 256, resolution: tuple = (64, 64)):
        self.feature_dim = feature_dim
        self.resolution = resolution
        self.capture = None
        self.active = False
        self.last_frame = None
        self.prev_frame = None

    def start_webcam(self):
        """Start webcam capture. Requires opencv-python-headless."""
        if not HAS_CV2:
            print("[VISION] ERROR: OpenCV not installed. Run: pip install opencv-python-headless")
            print("[VISION] Webcam will NOT work without OpenCV.")
            return False
        try:
            self.capture = cv2.VideoCapture(0)
            if self.capture.isOpened():
                self.active = True
                print("[VISION] Webcam started")
                return True
        except Exception as e:
            print(f"[VISION] Failed to start webcam: {e}")
        return False

    def stop_webcam(self):
        """Stop webcam capture."""
        if self.capture:
            self.capture.release()
        self.active = False

    def capture_frame(self) -> np.ndarray:
        """Capture and encode a webcam frame."""
        if not self.active or not HAS_CV2:
            return self._synthetic_input()

        ret, frame = self.capture.read()
        if not ret:
            return self._synthetic_input()

        return self._encode_frame(frame)

    def encode_image(self, image: np.ndarray) -> np.ndarray:
        """Encode an arbitrary image array."""
        return self._encode_frame(image)

    def _encode_frame(self, frame: np.ndarray) -> np.ndarray:
        """Process a frame into spike features."""
        if not HAS_CV2:
            return self._synthetic_input()

        # Resize to working resolution
        small = cv2.resize(frame, self.resolution)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

        features = np.zeros(self.feature_dim, dtype=np.float32)
        quarter = self.feature_dim // 4

        # Feature 1: Spatial intensity (downsampled)
        spatial = cv2.resize(gray, (int(np.sqrt(quarter)), int(np.sqrt(quarter))))
        features[:min(quarter, spatial.size)] = spatial.flatten()[:quarter]

        # Feature 2: Edge detection (Sobel)
        edges_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        edges_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        edge_mag = np.sqrt(edges_x**2 + edges_y**2)
        edge_pooled = cv2.resize(edge_mag, (int(np.sqrt(quarter)), int(np.sqrt(quarter))))
        features[quarter:2*quarter] = edge_pooled.flatten()[:quarter]

        # Feature 3: Motion detection (temporal difference)
        if self.prev_frame is not None:
            diff = np.abs(gray - self.prev_frame)
            motion_pooled = cv2.resize(diff, (int(np.sqrt(quarter)), int(np.sqrt(quarter))))
            features[2*quarter:3*quarter] = motion_pooled.flatten()[:quarter]

        # Feature 4: Color histogram (if color image)
        if len(frame.shape) == 3:
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist_h = cv2.calcHist([hsv], [0], None, [quarter], [0, 180]).flatten()
            hist_h = hist_h / (hist_h.max() + 1e-8)
            features[3*quarter:] = hist_h[:quarter]

        self.prev_frame = gray
        self.last_frame = frame

        # Normalize
        features = np.clip(features, 0, 1)
        return features

    def _synthetic_input(self) -> np.ndarray:
        """Generate synthetic visual input when no camera available."""
        t = np.random.random()
        features = np.zeros(self.feature_dim, dtype=np.float32)
        # Moving gradient pattern
        x = np.linspace(0, 2 * np.pi, self.feature_dim)
        features = (np.sin(x + t * 10) * 0.3 + 0.5).astype(np.float32)
        features += np.random.randn(self.feature_dim).astype(np.float32) * 0.05
        return np.clip(features, 0, 1)
