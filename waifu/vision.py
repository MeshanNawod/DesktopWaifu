"""Screen awareness: screenshots of the monitor you're using + active window tracking."""
from __future__ import annotations

import ctypes
import io
import os
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer

_user32 = ctypes.windll.user32 if os.name == "nt" else None


def _window_title(hwnd) -> str:
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


class ForegroundTracker(QObject):
    """Remembers the last foreground window that isn't one of ours.

    When you click "look at my screen" in the chat panel, the foreground window is the
    chat panel itself, so we need the window you were using just before.
    """

    def __init__(self):
        super().__init__()
        self.title = ""
        self._pid = os.getpid()
        if _user32:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._poll)
            self._timer.start(1000)

    def _poll(self) -> None:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != self._pid:
            t = _window_title(hwnd)
            if t:
                self.title = t


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def user_idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input anywhere on the PC."""
    if not _user32:
        return 0.0
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)


def is_blocked(title: str, blocklist: list[str]) -> bool:
    low = title.lower()
    return any(k.strip() and k.strip().lower() in low for k in blocklist)


def capture_screen(max_side: int = 1280, quality: int = 70) -> bytes:
    """Grab the monitor under the mouse cursor, downscale, return JPEG bytes.

    Safe to call from a worker thread (mss handles are created per call).
    """
    import mss
    from PIL import Image

    with mss.mss() as sct:
        mon = sct.monitors[1]
        if _user32:
            pt = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            for m in sct.monitors[1:]:
                if m["left"] <= pt.x < m["left"] + m["width"] and m["top"] <= pt.y < m["top"] + m["height"]:
                    mon = m
                    break
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()
