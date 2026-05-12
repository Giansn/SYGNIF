"""
Screen Observer v2 - ACTUALLY watches and reads your screen.

v1 just did 64x48 motion detection. v2:
- Full-resolution screen capture (mss or PIL)
- OCR via pytesseract to READ actual text on screen
- Active window title detection
- Feeds extracted TEXT to the brain via TextEncoder (not just pixel blobs)
- Every screen capture becomes a searchable knowledge entry
- Drives real STDP learning from real content

Optional dependencies (graceful fallback if missing):
- pytesseract + Tesseract binary: enables OCR
- mss: faster screen capture (10x faster than PIL)
- pygetwindow: active window title detection
"""

import time
import threading
import hashlib
import numpy as np

# Core capture
try:
    from PIL import ImageGrab, Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Fast capture (optional)
try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

# OCR (optional but highly recommended)
try:
    import pytesseract
    # Test that tesseract binary is available
    try:
        pytesseract.get_tesseract_version()
        HAS_OCR = True
    except Exception:
        HAS_OCR = False
except ImportError:
    HAS_OCR = False

# Active window detection (optional; package may raise NotImplementedError on Linux)
try:
    import pygetwindow as gw
    HAS_WINDOW = True
except (ImportError, NotImplementedError, OSError):
    HAS_WINDOW = False


class ScreenObserver:
    """
    Captures screenshots, runs OCR, and feeds actual screen content to the brain.
    The text extracted from your screen drives real STDP learning.
    """

    def __init__(self, feature_dim=256, capture_interval=2.0,
                 brain=None, text_encoder=None, knowledge_store=None):
        self.feature_dim = feature_dim
        self.capture_interval = capture_interval
        self.active = False
        self._thread = None

        # Refs to feed data into the brain + knowledge store
        self.brain = brain
        self.text_encoder = text_encoder
        self.knowledge_store = knowledge_store

        # Visual features (fallback / supplement to OCR)
        self._last_features = np.zeros(feature_dim, dtype=np.float32)
        self._last_capture_time = 0
        self._prev_frame = None
        self._motion_level = 0.0
        self._capture_count = 0

        # OCR state
        self._last_text = ""
        self._last_text_hash = ""
        self._last_window_title = ""
        self._text_changes = 0
        self._total_text_extracted = 0

        # mss capture context (created per-thread)
        self._mss_sct = None

    def attach_brain(self, brain, text_encoder, knowledge_store):
        """Wire up the brain so OCR text flows into neural input + memory."""
        self.brain = brain
        self.text_encoder = text_encoder
        self.knowledge_store = knowledge_store

    def start(self):
        """Start screen observation in background thread."""
        if not HAS_PIL and not HAS_MSS:
            print("[SCREEN] No capture backend available - install Pillow or mss")
            return False
        if self.active:
            return True
        self.active = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        backend = "mss" if HAS_MSS else "PIL"
        ocr = "OCR enabled" if HAS_OCR else "OCR disabled (install pytesseract + Tesseract for text reading)"
        wintitle = "window titles enabled" if HAS_WINDOW else "window titles disabled"
        print(f"[SCREEN] Observation started ({backend} capture, {ocr}, {wintitle})")
        return True

    def stop(self):
        """Stop screen observation."""
        self.active = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._mss_sct:
            try:
                self._mss_sct.close()
            except Exception:
                pass
            self._mss_sct = None
        print("[SCREEN] Screen observation stopped")

    def _capture_loop(self):
        """Background loop: capture, OCR, feed brain, store knowledge."""
        if HAS_MSS:
            self._mss_sct = mss.mss()

        while self.active:
            try:
                self._capture_and_process()
                self._capture_count += 1
            except Exception as e:
                print(f"[SCREEN] Capture error: {e}")
            time.sleep(self.capture_interval)

    def _capture_screen(self):
        """Capture full-resolution screen. Returns PIL Image."""
        if HAS_MSS and self._mss_sct:
            monitor = self._mss_sct.monitors[1]  # Primary monitor
            shot = self._mss_sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        elif HAS_PIL:
            return ImageGrab.grab()
        return None

    def _get_window_title(self):
        """Get active window title if available."""
        if not HAS_WINDOW:
            return ""
        try:
            w = gw.getActiveWindow()
            if w:
                return w.title
        except Exception:
            pass
        return ""

    def _capture_and_process(self):
        """Full pipeline: capture → OCR → visual features → brain input → knowledge."""
        screenshot = self._capture_screen()
        if screenshot is None:
            return

        # --- 1. Extract visual features (for motion detection / general activity) ---
        small = screenshot.resize((64, 48), Image.LANCZOS)
        pixels = np.array(small, dtype=np.float32) / 255.0
        gray = np.mean(pixels, axis=2)

        features = np.zeros(self.feature_dim, dtype=np.float32)
        # Spatial grid
        grid = self._downsample_grid(gray, 16, 12)
        n = min(192, self.feature_dim)
        features[:n] = grid.flatten()[:n]

        # Motion
        if self._prev_frame is not None:
            diff = np.abs(gray - self._prev_frame)
            self._motion_level = float(np.mean(diff))
        self._prev_frame = gray.copy()

        # Normalize visual features
        if features.max() > 0:
            features /= features.max()
        self._last_features = features

        # --- 2. OCR - read actual text from screen ---
        ocr_text = ""
        if HAS_OCR:
            try:
                # Downscale a bit for OCR speed (but keep readable)
                w, h = screenshot.size
                if w > 1920:
                    scale = 1920 / w
                    ocr_img = screenshot.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                else:
                    ocr_img = screenshot
                ocr_text = pytesseract.image_to_string(ocr_img, timeout=3)
                ocr_text = self._clean_ocr_text(ocr_text)
            except Exception as e:
                # OCR can timeout on complex screens - that's OK
                pass

        # --- 3. Active window title for context ---
        window_title = self._get_window_title()

        # --- 4. Detect changes and feed to brain / knowledge ---
        full_context = f"{window_title}\n{ocr_text}".strip()
        text_hash = hashlib.md5(full_context.encode("utf-8", errors="ignore")).hexdigest()

        if text_hash != self._last_text_hash and full_context:
            # Screen content CHANGED — this is the real signal
            self._text_changes += 1
            self._last_text = ocr_text
            self._last_text_hash = text_hash
            self._last_window_title = window_title
            self._total_text_extracted += len(ocr_text)

            # Feed the extracted text directly into the brain
            if self.brain and self.text_encoder and ocr_text.strip():
                try:
                    # Only feed chunks of real text - ignore tiny fragments
                    if len(ocr_text.strip()) >= 10:
                        # Send first 500 chars to text encoder
                        text_to_feed = ocr_text[:500]
                        text_features = self.text_encoder.encode(text_to_feed)
                        if text_features.size > 0:
                            self.brain.inject_sensory_input("text", text_features)
                        # Also inject visual features
                        self.brain.inject_sensory_input("vision", features)
                        # Boost acetylcholine (attention) when screen changes
                        self.brain.neuromodulators["acetylcholine"] = min(
                            1.0, self.brain.neuromodulators.get("acetylcholine", 0.5) + 0.08
                        )
                        # Boost norepinephrine (arousal) for new stimulus
                        self.brain.neuromodulators["norepinephrine"] = min(
                            1.0, self.brain.neuromodulators.get("norepinephrine", 0.3) + 0.05
                        )
                except Exception as e:
                    print(f"[SCREEN] Brain injection error: {e}")

            # Store in knowledge DB as searchable memory
            if self.knowledge_store and ocr_text.strip():
                try:
                    # Only store meaningful chunks (not single words)
                    if len(ocr_text.strip()) >= 30:
                        tags = ["screen", "observation"]
                        if window_title:
                            tags.append(self._window_to_tag(window_title))
                        self.knowledge_store.store(
                            text=ocr_text.strip()[:5000],  # Cap at 5KB per entry
                            source="screen_observer",
                            tags=tags,
                            metadata={
                                "window": window_title,
                                "motion": round(self._motion_level, 4),
                                "capture_num": self._capture_count,
                            }
                        )
                except Exception as e:
                    print(f"[SCREEN] Knowledge store error: {e}")

        self._last_capture_time = time.time()

    def _clean_ocr_text(self, text):
        """Clean OCR noise."""
        if not text:
            return ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        # Remove lines that are clearly OCR garbage (no vowels, mostly symbols)
        cleaned = []
        for line in lines:
            # Too short, skip
            if len(line) < 3:
                continue
            # Mostly symbols? skip
            alpha_count = sum(1 for c in line if c.isalnum())
            if alpha_count < len(line) * 0.3:
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    def _window_to_tag(self, title):
        """Extract clean tag from window title."""
        # Common pattern: "Document - App Name" → take app name
        if " - " in title:
            parts = title.split(" - ")
            title = parts[-1]
        return title.lower().replace(" ", "_")[:30]

    def _downsample_grid(self, img, cols, rows):
        """Downsample image to a grid of average intensities."""
        h, w = img.shape
        cell_h, cell_w = h // rows, w // cols
        grid = np.zeros((rows, cols), dtype=np.float32)
        for r in range(rows):
            for c in range(cols):
                cell = img[r * cell_h:(r + 1) * cell_h, c * cell_w:(c + 1) * cell_w]
                grid[r, c] = np.mean(cell)
        return grid

    def get_features(self):
        """Get latest visual features for brain input."""
        return self._last_features.copy()

    def get_state(self):
        """Get observer state for API."""
        return {
            "active": self.active,
            "available": HAS_PIL or HAS_MSS,
            "ocr_available": HAS_OCR,
            "window_detection": HAS_WINDOW,
            "capture_backend": "mss" if HAS_MSS else ("PIL" if HAS_PIL else "none"),
            "capture_count": self._capture_count,
            "text_changes": self._text_changes,
            "total_chars_extracted": self._total_text_extracted,
            "motion_level": round(self._motion_level, 4),
            "last_window": self._last_window_title,
            "last_text_preview": self._last_text[:200] if self._last_text else "",
            "last_capture_age": round(time.time() - self._last_capture_time, 1) if self._last_capture_time > 0 else -1,
        }
