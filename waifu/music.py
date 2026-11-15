"""Listen to music with you: WASAPI loopback capture of what your PC is playing.

* level / beat detection (bass-energy onsets) so she bobs her head on the beat
* playing / stopped detection
* the last few seconds as a WAV clip, so Gemini can actually hear the song and comment on it
* now-playing title from Spotify / YouTube / VLC window titles
"""
from __future__ import annotations

import ctypes
import io
import logging
import os
import threading
import time
import warnings
import wave
from collections import deque
from ctypes import wintypes

import numpy as np
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)
RATE = 44100
BLOCK = 1024
CLIP_RATE = 16000


class MusicListener(QObject):
    beat = Signal(float)            # strength 0..1
    level = Signal(float)
    playing_changed = Signal(bool)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.playing = False
        self.bpm = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._clip: deque = deque(maxlen=int(CLIP_RATE * 10 / (BLOCK * CLIP_RATE / RATE)) + 1)
        self._clip_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="music", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        if self.playing:
            self.playing = False
            self.playing_changed.emit(False)

    def _loop(self) -> None:
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except ImportError:
                pass
            import soundcard as sc
            warnings.filterwarnings("ignore", module="soundcard")
            spk = sc.default_speaker()
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Can't listen to system audio: {e}")
            return
        hist: deque[float] = deque(maxlen=43)       # ~1 s of bass energy
        beats: deque[float] = deque(maxlen=16)
        last_beat = 0.0
        loud_since = quiet_since = None
        win = np.hanning(BLOCK).astype(np.float32)
        freqs = np.fft.rfftfreq(BLOCK, 1 / RATE)
        bass = (freqs > 30) & (freqs < 180)
        try:
            with mic.recorder(samplerate=RATE, channels=2, blocksize=BLOCK) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=BLOCK)
                    if data.size == 0:
                        continue
                    mono = data.mean(axis=1).astype(np.float32)
                    rms = float(np.sqrt(np.mean(mono ** 2)))
                    spec = np.abs(np.fft.rfft(mono[:BLOCK] * win[: len(mono[:BLOCK])], n=BLOCK))
                    e = float(spec[bass].sum())
                    now = time.monotonic()
                    # beat = bass energy jumps well above the recent average
                    if len(hist) > 10:
                        avg = sum(hist) / len(hist)
                        if e > avg * 1.45 and rms > 0.01 and now - last_beat > 0.28:
                            last_beat = now
                            beats.append(now)
                            if len(beats) >= 4:
                                gaps = np.diff(np.array(beats))
                                gaps = gaps[(gaps > 0.28) & (gaps < 1.2)]
                                if gaps.size:
                                    self.bpm = 60.0 / float(np.median(gaps))
                            self.beat.emit(min(1.0, e / (avg * 3 + 1e-6)))
                    hist.append(e)
                    self.level.emit(min(1.0, rms * 8))
                    # rolling 10 s clip at 16 kHz for Gemini
                    n_out = int(len(mono) * CLIP_RATE / RATE)
                    if n_out:
                        x = np.interp(np.linspace(0, len(mono) - 1, n_out), np.arange(len(mono)), mono)
                        with self._clip_lock:
                            self._clip.append((np.clip(x, -1, 1) * 32767).astype(np.int16))
                    # playing / stopped with hysteresis
                    if rms > 0.008:
                        quiet_since = None
                        loud_since = loud_since or now
                        if not self.playing and now - loud_since > 3.0:
                            self.playing = True
                            self.playing_changed.emit(True)
                    else:
                        loud_since = None
                        quiet_since = quiet_since or now
                        if self.playing and now - quiet_since > 6.0:
                            self.playing = False
                            self.playing_changed.emit(False)
        except Exception as e:  # noqa: BLE001
            log.exception("Music listener stopped")
            self.error.emit(f"Music listening stopped: {e}")

    def clip_wav(self, seconds: float = 8.0) -> bytes | None:
        with self._clip_lock:
            chunks = list(self._clip)
        if not chunks:
            return None
        pcm = np.concatenate(chunks)[-int(CLIP_RATE * seconds):]
        if float(np.abs(pcm).max()) < 200:
            return None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(CLIP_RATE)
            w.writeframes(pcm.tobytes())
        return buf.getvalue()


# --------------------------------------------------------------------------- now playing
def _windows() -> list[tuple[str, str]]:
    """(process exe name, window title) for visible top-level windows."""
    if os.name != "nt":
        return []
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    out: list[tuple[str, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            pb = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(h, 0, pb, ctypes.byref(size)):
                exe = os.path.basename(pb.value).lower()
            kernel32.CloseHandle(h)
        out.append((exe, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


def now_playing() -> str | None:
    """Best-effort song/video title from Spotify, YouTube (any browser) or VLC."""
    try:
        wins = _windows()
    except Exception:  # noqa: BLE001
        return None
    for exe, title in wins:
        if exe == "spotify.exe" and " - " in title:
            return title
    for exe, title in wins:
        if " - YouTube" in title:
            return title.split(" - YouTube")[0].strip()
        if exe == "vlc.exe" and title.endswith(" - VLC media player"):
            return title[: -len(" - VLC media player")]
    return None
