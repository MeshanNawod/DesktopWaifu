"""Collapsible floating chat panel that docks next to the character."""
from __future__ import annotations

import html

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

STYLE = """
QWidget { color: #f3eefc; font-family: 'Segoe UI'; font-size: 10pt; }
QTextBrowser { background: transparent; border: none; }
QLineEdit { background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15);
            border-radius: 9px; padding: 6px 9px; selection-background-color: #ff8fb8; }
QLineEdit:focus { border-color: #ff8fb8; }
QPushButton { background: rgba(255,255,255,0.08); border: none; border-radius: 9px;
              padding: 6px 9px; min-width: 18px; }
QPushButton:hover { background: rgba(255,143,184,0.35); }
QPushButton:checked { background: #e5486f; }
QPushButton#send { background: #ff8fb8; color: #2a1830; font-weight: 600; }
QLabel#title { font-weight: 600; font-size: 11pt; }
QLabel#status { color: #b9aed0; font-size: 9pt; }
"""


class ChatPanel(QWidget):
    send_text = Signal(str)
    mic_clicked = Signal()
    look_clicked = Signal()
    camera_clicked = Signal()
    settings_clicked = Signal()
    closed = Signal()

    W, H = 340, 440

    def __init__(self, name: str):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)
        self.setStyleSheet(STYLE)
        self.detached = False
        self._drag_from: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.title = QLabel(objectName="title")
        self.status = QLabel("", objectName="status")
        look = QPushButton("👁", toolTip="Look at my screen")
        cam = QPushButton("📷", toolTip="Look at me / what am I holding? (webcam)")
        cog = QPushButton("⚙", toolTip="Settings")
        close = QPushButton("✕", toolTip="Close (Esc)")
        look.clicked.connect(self.look_clicked)
        cam.clicked.connect(self.camera_clicked)
        cog.clicked.connect(self.settings_clicked)
        close.clicked.connect(self.hide)
        head.addWidget(self.title)
        head.addWidget(self.status, 1)
        for b in (look, cam, cog, close):
            head.addWidget(b)
        root.addLayout(head)

        self.log = QTextBrowser(openExternalLinks=True)
        root.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.input = QLineEdit(placeholderText="Say something…")
        self.input.returnPressed.connect(self._submit)
        self.mic = QPushButton("🎤", toolTip="Talk (auto-stops when you pause)", checkable=True)
        self.mic.clicked.connect(lambda: self.mic_clicked.emit())
        send = QPushButton("➤", objectName="send")
        send.clicked.connect(self._submit)
        row.addWidget(self.input, 1)
        row.addWidget(self.mic)
        row.addWidget(send)
        root.addLayout(row)
        self.set_name(name)

    # ----------------------------------------------------------------- api
    def set_name(self, name: str) -> None:
        self.name = name
        self.title.setText(f"💬 {name}")

    def add_message(self, who: str, text: str, kind: str = "her") -> None:
        color = {"her": "#ff9fc4", "you": "#9fd0ff", "system": "#b9aed0"}[kind]
        body = html.escape(text).replace("\n", "<br>")
        if kind == "system":
            self.log.append(f'<p style="color:{color}; font-style:italic; margin:4px 0">{body}</p>')
        else:
            self.log.append(f'<p style="margin:5px 0"><b style="color:{color}">{html.escape(who)}</b>'
                            f'<br>{body}</p>')
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_recording(self, on: bool) -> None:
        self.mic.setChecked(on)
        if on:
            self.set_status("listening…")

    def open_near(self, char_rect: QRect) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self.dock(char_rect)

    def dock(self, char_rect: QRect) -> None:
        if self.detached or not self.isVisible():
            return
        scr = self.screen().availableGeometry() if self.screen() else QRect(0, 0, 1920, 1080)
        x = char_rect.right() + 4
        if x + self.width() > scr.right():
            x = char_rect.left() - self.width() - 4
        y = char_rect.bottom() - self.height()
        x = max(scr.left(), min(scr.right() - self.width(), x))
        y = max(scr.top(), min(scr.bottom() - self.height(), y))
        self.move(x, y)

    # ----------------------------------------------------------------- internals
    def _submit(self) -> None:
        t = self.input.text().strip()
        if t:
            self.input.clear()
            self.send_text.emit(t)

    def hideEvent(self, e) -> None:
        self.detached = False
        self.closed.emit()
        super().hideEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    # drag by the header to detach the panel from her
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 44:
            self._drag_from = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            self.detached = True
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag_from = None

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 16, 16)
        p.fillPath(path, QColor(32, 26, 48, 238))
        p.setPen(QColor(255, 143, 184, 120))
        p.drawPath(path)
        p.end()
