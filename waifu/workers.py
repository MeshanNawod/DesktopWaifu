"""Tiny helper to run blocking work (API calls, audio synthesis) off the UI thread."""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        # Created on the UI thread, so emits from the pool are queued back to it.
        self.signals = _Signals()

    def run(self) -> None:
        try:
            try:
                result = self.fn()
            except Exception as e:  # noqa: BLE001
                log.error("Background task failed:\n%s", traceback.format_exc())
                self.signals.failed.emit(f"{type(e).__name__}: {e}")
            else:
                self.signals.done.emit(result)
        except RuntimeError:
            pass  # app is shutting down and the receiver is gone


_live: set[_Task] = set()  # keep signal objects alive until delivered


def run_async(fn: Callable[[], Any],
              on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None) -> None:
    task = _Task(fn)
    task.setAutoDelete(False)
    _live.add(task)

    def _finish(*_):
        _live.discard(task)

    if on_done:
        task.signals.done.connect(on_done)
    if on_error:
        task.signals.failed.connect(on_error)
    task.signals.done.connect(_finish)
    task.signals.failed.connect(_finish)
    QThreadPool.globalInstance().start(task)
