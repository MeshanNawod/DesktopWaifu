"""Speech bubble that floats above her head, with a typewriter effect."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

MAX_W, PAD, TAIL = 260, 12, 10


class SpeechBubble(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.font_ = QFont("Segoe UI", 10)
        self._full = self._shown = ""
        self._tail_x = 0.0
        self._anchor = QPoint()
        self._type = QTimer(self)
        self._type.timeout.connect(self._step)
        self._hide = QTimer(self, singleShot=True)
        self._hide.timeout.connect(self.hide)
        self._dots = QTimer(self)
        self._dots.timeout.connect(self._dot_step)
        self._dot_n = 0

    # ----------------------------------------------------------------- api
    def say(self, text: str, seconds: float) -> None:
        self._dots.stop()
        self._full, self._shown = text, ""
        self._layout()
        self._type.start(18)
        self._hide.start(int(max(seconds, len(text) / 13) * 1000))
        self.show()
        self.raise_()

    def thinking(self) -> None:
        self._type.stop()
        self._hide.stop()
        self._full = self._shown = "..."
        self._layout()
        self._dot_n = 0
        self._dots.start(350)
        self.show()

    def stop_thinking(self) -> None:
        if self._dots.isActive():
            self._dots.stop()
            self.hide()

    def follow(self, anchor: QPoint) -> None:
        self._anchor = anchor
        if self.isVisible():
            self._place()

    # ----------------------------------------------------------------- internals
    def _dot_step(self) -> None:
        self._dot_n = (self._dot_n + 1) % 4
        self._shown = "." * max(1, self._dot_n) or "."
        self.update()

    def _step(self) -> None:
        if len(self._shown) >= len(self._full):
            self._type.stop()
            return
        self._shown = self._full[: len(self._shown) + 2]
        self.update()

    def _text_rect(self) -> QRect:
        fm = QFontMetrics(self.font_)
        return fm.boundingRect(QRect(0, 0, MAX_W - 2 * PAD, 2000),
                               int(Qt.TextFlag.TextWordWrap), self._full)

    def _layout(self) -> None:
        r = self._text_rect()
        self.setFixedSize(max(48, r.width() + 2 * PAD + 2), r.height() + 2 * PAD + TAIL + 2)
        self._place()

    def _place(self) -> None:
        scr = self.screen().availableGeometry() if self.screen() else QRect(0, 0, 1920, 1080)
        x = self._anchor.x() - self.width() // 2
        x = max(scr.left() + 4, min(scr.right() - self.width() - 4, x))
        y = max(scr.top() + 4, self._anchor.y() - self.height() + 6)
        self._tail_x = max(16.0, min(self.width() - 16.0, float(self._anchor.x() - x)))
        self.move(x, y)

    def mousePressEvent(self, _e) -> None:
        self.hide()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(1, 1, self.width() - 2, self.height() - TAIL - 2)
        path = QPainterPath()
        path.addRoundedRect(body, 12, 12)
        tail = QPainterPath()
        tail.moveTo(self._tail_x - 8, body.bottom() - 1)
        tail.lineTo(self._tail_x, self.height() - 2)
        tail.lineTo(self._tail_x + 8, body.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(QColor("#ff8fb8"), 2))
        p.setBrush(QColor(255, 255, 255, 245))
        p.drawPath(path)
        p.setPen(QColor("#3b2a3f"))
        p.setFont(self.font_)
        p.drawText(body.adjusted(PAD, PAD - 1, -PAD + 2, -PAD + 2),
                   int(Qt.TextFlag.TextWordWrap), self._shown)
        p.end()
