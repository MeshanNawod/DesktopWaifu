"""Gesture listener: turns raw mouse presses into the interaction types she understands.

    CLICK   short press and release          -> gentle touch (soft smile, sweet greeting)
    HOLD    press and keep holding still      -> firm press / hit ("hayiyen gahanawa"): startled, hurt
    DOUBLE  double click                      -> petting ("athaganawa"): happy closed eyes, blush
    STROKE  press-and-rub on head/face/hair,  -> petting too (each ~220 px of rubbing counts as one stroke)
            or hovering back and forth
    LIFT    press on a hand/leg/body and drag -> she's lifted (by that limb) and struggles

The listener only classifies; the character window decides what each gesture does.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QTimer, Signal
from PySide6.QtWidgets import QApplication

CLICK, HOLD, DOUBLE, STROKE = "click", "hold", "double", "stroke"
LIMBS = {"hand_l", "hand_r", "hand_face", "hand_low", "leg_l", "leg_r"}
# pressing and moving on these zones strokes her instead of picking her up
STROKE_ZONES = {"head", "hair_l", "hair_r", "cheek_l", "cheek_r", "mouth", "eye_l", "eye_r"}
LIMB_ALIAS = {"hand_face": "hand_l", "hand_low": "hand_r"}

HOLD_SECONDS = 0.55
MOVE_PX = 6.0
STROKE_PX = 220.0


class GestureListener(QObject):
    gesture = Signal(str, str)          # (kind, zone)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zone = "body"
        self.pressed = False
        self.moved = False
        self.consumed = False            # a HOLD/DOUBLE already fired for this press
        self.mode: str | None = None     # "lift" or "stroke" once the cursor moved
        self._press = QPointF()
        self._last = QPointF()
        self._stroke = 0.0
        self._hold = QTimer(self, singleShot=True)
        self._hold.timeout.connect(self._on_hold)
        self._single = QTimer(self, singleShot=True)
        self._single.timeout.connect(lambda: self.gesture.emit(CLICK, self.zone))

    # ------------------------------------------------------------------ events from the window
    def press(self, zone: str, gpos: QPointF) -> None:
        self.zone, self.pressed, self.moved, self.consumed, self.mode = zone, True, False, False, None
        self._press = self._last = QPointF(gpos)
        self._stroke = 0.0
        self._hold.start(int(HOLD_SECONDS * 1000))

    def move(self, gpos: QPointF) -> str | None:
        """Returns "lift" or "stroke" once the press turned into a drag, else None."""
        if not self.pressed:
            return None
        if not self.moved and (gpos - self._press).manhattanLength() > MOVE_PX:
            self.moved = True
            self._hold.stop()
            self._single.stop()
            self.mode = "stroke" if self.zone in STROKE_ZONES else "lift"
        if self.moved and self.mode == "stroke":
            self._stroke += (gpos - self._last).manhattanLength()
            if self._stroke >= STROKE_PX:
                self._stroke = 0.0
                self.gesture.emit(STROKE, self.zone)
        self._last = QPointF(gpos)
        return self.mode if self.moved else None

    def release(self) -> None:
        if not self.pressed:
            return
        self.pressed = False
        self._hold.stop()
        if self.consumed or self.moved:
            return
        self._single.start(QApplication.doubleClickInterval())   # maybe the first half of a double click

    def double(self, zone: str) -> None:
        self._single.stop()
        self._hold.stop()
        self.zone, self.pressed, self.consumed, self.moved = zone, True, True, False
        self.gesture.emit(DOUBLE, zone)

    def hover_stroke(self, zone: str) -> None:
        self.gesture.emit(STROKE, zone)

    def _on_hold(self) -> None:
        if self.pressed and not self.moved:
            self.consumed = True
            self.gesture.emit(HOLD, self.zone)
