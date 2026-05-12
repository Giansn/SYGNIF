"""
Video Recorder - Records the screen to .mp4 files while the brain watches.

Uses mss for fast capture and OpenCV for H.264 encoding. Records in 10-minute
segments to keep file sizes manageable. Auto-cleans old recordings to respect
disk space.

Recordings are stored in brain_state/recordings/ and can be played back later.
The brain can also "watch" recordings to learn from them (replay training).
"""

import os
import sys
import time
import threading
import numpy as np
from datetime import datetime


def _app_root():
    """Project/exe directory for storing recordings/ next to the app."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import ImageGrab
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class VideoRecorder:
    """Records screen to timestamped .mp4 files."""

    def __init__(self, output_dir=None, fps=10, segment_minutes=10,
                 max_disk_mb=5000, max_segments=50):
        """
        Args:
            output_dir: Where to save .mp4 files (default: brain_state/recordings/)
            fps: Frames per second (default: 10 - balances quality vs. size)
            segment_minutes: Auto-split recordings into this many-minute chunks
            max_disk_mb: Max total disk space for recordings (default 5GB)
            max_segments: Max number of files to keep (deletes oldest first)
        """
        if output_dir is None:
            brain_state_dir = os.path.join(_app_root(), "brain_state")
            output_dir = os.path.join(brain_state_dir, "recordings")
        os.makedirs(output_dir, exist_ok=True)

        self.output_dir = output_dir
        self.fps = max(1, min(fps, 60))
        self.segment_seconds = segment_minutes * 60
        self.max_disk_mb = max_disk_mb
        self.max_segments = max_segments

        self.active = False
        self._thread = None
        self._writer = None
        self._current_file = None
        self._segment_start = 0
        self._frames_written = 0
        self._total_recordings = 0
        self._current_size_bytes = 0
        self._mss_sct = None
        self._frame_size = None

    def start(self):
        """Start recording in background thread."""
        if not HAS_CV2:
            print("[VIDEO] OpenCV not available - install opencv-python-headless")
            return False
        if not (HAS_MSS or HAS_PIL):
            print("[VIDEO] No screen capture available - install mss or Pillow")
            return False
        if self.active:
            return True

        # Enforce disk limits before starting
        self._enforce_limits()

        self.active = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()
        backend = "mss" if HAS_MSS else "PIL"
        print(f"[VIDEO] Recording started ({backend} capture, {self.fps}fps, "
              f"{self.segment_seconds // 60}min segments)")
        return True

    def stop(self):
        """Stop recording and close current file."""
        self.active = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._close_writer()
        if self._mss_sct:
            try:
                self._mss_sct.close()
            except Exception:
                pass
            self._mss_sct = None
        print(f"[VIDEO] Recording stopped ({self._total_recordings} files, "
              f"{self._frames_written} total frames)")

    def _record_loop(self):
        """Background loop that captures frames and writes to video."""
        if HAS_MSS:
            self._mss_sct = mss.mss()

        frame_interval = 1.0 / self.fps
        self._open_new_segment()

        while self.active:
            start = time.time()
            try:
                frame = self._capture_frame()
                if frame is not None and self._writer is not None:
                    self._writer.write(frame)
                    self._frames_written += 1

                    # Check if we need to start a new segment
                    elapsed = time.time() - self._segment_start
                    if elapsed >= self.segment_seconds:
                        self._open_new_segment()
            except Exception as e:
                print(f"[VIDEO] Frame error: {e}")

            # Sleep to maintain target FPS
            elapsed = time.time() - start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _capture_frame(self):
        """Capture one screen frame as a BGR numpy array for OpenCV."""
        if HAS_MSS and self._mss_sct:
            monitor = self._mss_sct.monitors[1]
            shot = self._mss_sct.grab(monitor)
            # mss returns BGRA - convert to BGR for OpenCV
            img = np.array(shot, dtype=np.uint8)
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        elif HAS_PIL:
            img = ImageGrab.grab()
            arr = np.array(img, dtype=np.uint8)
            # PIL gives RGB - convert to BGR
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        return None

    def _open_new_segment(self):
        """Close current file and open a new timestamped segment."""
        self._close_writer()

        # Check disk space before starting new segment
        self._enforce_limits()

        # Sample a frame to get dimensions
        frame = self._capture_frame()
        if frame is None:
            print("[VIDEO] Cannot get frame dimensions")
            return

        h, w = frame.shape[:2]
        self._frame_size = (w, h)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screen_{timestamp}.mp4"
        filepath = os.path.join(self.output_dir, filename)

        # Use mp4v codec (widely compatible, no external deps)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self._writer = cv2.VideoWriter(filepath, fourcc, self.fps, self._frame_size)

        if not self._writer.isOpened():
            print(f"[VIDEO] Failed to open writer for {filepath}")
            self._writer = None
            return

        self._current_file = filepath
        self._segment_start = time.time()
        self._total_recordings += 1
        print(f"[VIDEO] New segment: {filename} ({w}x{h} @ {self.fps}fps)")

    def _close_writer(self):
        """Close current video writer if open."""
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
            self._writer = None

    def _enforce_limits(self):
        """Delete oldest recordings if we exceed disk or count limits."""
        try:
            files = []
            total_bytes = 0
            for f in os.listdir(self.output_dir):
                if not f.endswith(".mp4"):
                    continue
                full = os.path.join(self.output_dir, f)
                if os.path.isfile(full):
                    size = os.path.getsize(full)
                    files.append((os.path.getmtime(full), full, size))
                    total_bytes += size
            files.sort()  # Oldest first

            # Enforce count limit
            while len(files) > self.max_segments:
                _, path, size = files.pop(0)
                try:
                    os.remove(path)
                    total_bytes -= size
                    print(f"[VIDEO] Removed old segment (count limit): {os.path.basename(path)}")
                except Exception:
                    pass

            # Enforce disk limit
            max_bytes = self.max_disk_mb * 1024 * 1024
            while files and total_bytes > max_bytes:
                _, path, size = files.pop(0)
                try:
                    os.remove(path)
                    total_bytes -= size
                    print(f"[VIDEO] Removed old segment (disk limit): {os.path.basename(path)}")
                except Exception:
                    pass

            self._current_size_bytes = total_bytes
        except Exception as e:
            print(f"[VIDEO] Limit enforcement error: {e}")

    def list_recordings(self):
        """List all video files with metadata."""
        try:
            files = []
            for f in os.listdir(self.output_dir):
                if not f.endswith(".mp4"):
                    continue
                full = os.path.join(self.output_dir, f)
                if os.path.isfile(full):
                    stat = os.stat(full)
                    files.append({
                        "name": f,
                        "size_mb": round(stat.st_size / 1024 / 1024, 2),
                        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "path": full,
                    })
            files.sort(key=lambda x: x["created"], reverse=True)
            return files
        except Exception as e:
            print(f"[VIDEO] List error: {e}")
            return []

    def delete_recording(self, name):
        """Delete a specific recording by filename."""
        path = os.path.join(self.output_dir, name)
        if not os.path.exists(path):
            return False
        if not path.endswith(".mp4"):
            return False
        try:
            os.remove(path)
            print(f"[VIDEO] Deleted: {name}")
            return True
        except Exception as e:
            print(f"[VIDEO] Delete error: {e}")
            return False

    def get_state(self):
        """Current recorder state for API."""
        total_bytes = 0
        file_count = 0
        try:
            for f in os.listdir(self.output_dir):
                if f.endswith(".mp4"):
                    full = os.path.join(self.output_dir, f)
                    if os.path.isfile(full):
                        total_bytes += os.path.getsize(full)
                        file_count += 1
        except Exception:
            pass

        current_file = os.path.basename(self._current_file) if self._current_file else None

        return {
            "active": self.active,
            "available": HAS_CV2 and (HAS_MSS or HAS_PIL),
            "fps": self.fps,
            "segment_minutes": self.segment_seconds // 60,
            "current_file": current_file,
            "frames_written": self._frames_written,
            "total_recordings": self._total_recordings,
            "total_disk_mb": round(total_bytes / 1024 / 1024, 2),
            "file_count": file_count,
            "max_disk_mb": self.max_disk_mb,
            "max_segments": self.max_segments,
            "output_dir": self.output_dir,
        }
