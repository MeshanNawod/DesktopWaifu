"""Webcam awareness: local face/presence detection + on-demand snapshots for Gemini vision.

Only presence (face yes/no) is computed locally, about twice a second. Frames never
leave the PC unless she is asked to look ("look at me", "what is this?") or greets you
when you come back, and then a single JPEG goes to Gemini.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)
CASCADE = Path(__file__).resolve().parent.parent / "assets" / "models" / "haarcascade_frontalface_default.xml"


class CameraWatcher(QObject):
    presence_changed = Signal(bool)    # True when your face appears, False when you've been gone a while
    started = Signal()
    stopped = Signal()
    error = Signal(str)

    def __init__(self, index: int = 0, interval: float = 0.5, absent_after: float = 20.0):
        super().__init__()
        self.index = index
        self.interval = interval
        self.absent_after = absent_after
        self.present = False
        self.last_seen = 0.0
        self.faces = 0
        self._frame = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="webcam", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        with self._lock:
            self._frame = None

    def _loop(self) -> None:
        try:
            import cv2
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"OpenCV unavailable: {e}")
            return
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self.error.emit("Couldn't open the webcam (in use by another app, or blocked in Windows privacy settings).")
            return
        cascade = cv2.CascadeClassifier(str(CASCADE)) if CASCADE.exists() and hasattr(cv2, "CascadeClassifier") else None
        if cascade is not None and cascade.empty():
            cascade = None
        prev = None
        self.started.emit()
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.5)
                    continue
                with self._lock:
                    self._frame = frame
                small = cv2.resize(frame, (320, int(320 * frame.shape[0] / frame.shape[1])))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                if cascade is not None:
                    faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(40, 40))
                    seen = len(faces) > 0
                    self.faces = len(faces)
                else:  # fallback: noticeable motion = someone is there
                    seen = prev is not None and float(cv2.absdiff(gray, prev).mean()) > 4.0
                    prev = gray
                now = time.monotonic()
                if seen:
                    self.last_seen = now
                    if not self.present:
                        self.present = True
                        self.presence_changed.emit(True)
                elif self.present and now - self.last_seen > self.absent_after:
                    self.present = False
                    self.presence_changed.emit(False)
                self._stop.wait(self.interval)
        finally:
            cap.release()
            self.stopped.emit()

    def snapshot_jpeg(self, max_side: int = 768) -> bytes | None:
        import cv2
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
        if frame is None:
            return None
        h, w = frame.shape[:2]
        s = min(1.0, max_side / max(h, w))
        if s < 1:
            frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None
