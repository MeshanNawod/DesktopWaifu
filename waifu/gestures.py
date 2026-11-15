"""Gesture listener: turns raw mouse input into the touches she understands.

    CLICK     short press and release                -> gentle touch: soft smile, sweet greeting
    SQUEEZE   press and hold still (0.3 s+)          -> squish that spot ("puni puni"), cute reaction on release
    HARD      keep squeezing too long (1.8 s+)       -> too hard: it hurts, she gets defensive
    DOUBLE    double click                           -> petting ("athaganawa"): happy closed eyes, blush
    STROKE    hovering / rubbing back and forth      -> petting too
    MOVE      press a part and drag a little         -> you move THAT part: head tilts, arm / leg follows
                                                        the cursor, body leans, skirt is pulled
    PULL      press her hair / scarf and drag        -> the hair follows your cursor: "don't pull my hair!"
    LIFT      drag a hand / leg / body far enough    -> she's lifted by it, dangles and struggles

The listener only classifies; the character window decides what each gesture does.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QTimer, Signal
from PySide6.QtWidgets import QApplication

CLICK, SQUEEZE, HARD, DOUBLE, STROKE, MOVE, PULL = "click", "squeeze", "hard", "double", "stroke", "move", "pull"
HOLD = HARD   # backwards-compatible name

LIMBS = {"hand_l", "hand_r", "hand_face", "hand_low", "leg_l", "leg_r"}
LIMB_ALIAS = {"hand_face": "hand_l", "hand_low": "hand_r"}
# dragging these pulls them (hair / cloth springs follow the cursor); they never lift her
PULL_ZONES = {"hair_l", "hair_r", "hair", "scarf"}
# dragging the face / head moves her head; it never lifts her
FACE_ZONES = {"head", "eye_l", "eye_r", "nose", "mouth", "cheek_l", "cheek_r"}
STROKE_ZONES = set()   # (kept for compatibility: stroking is done by hovering / double click)

SQUEEZE_SECONDS = 0.3
HARD_SECONDS = 1.8
MOVE_PX = 6.0
PULL_PX = 30.0
LIFT_PX = 140.0      # drag a limb / her body further than this and she's picked up


class GestureListener(QObject):
    gesture = Signal(str, str)          # (kind, zone)
    squeeze_started = Signal(str)       # zone: start the squish visual right away

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zone = "body"
        self.pressed = False
        self.moved = False
        self.consumed = False
        self.squeezing = False
        self.mode: str | None = None     # "move", "pull" or "lift"
        self._press = QPointF()
        self._last = QPointF()
        self._sq = QTimer(self, singleShot=True)
        self._sq.timeout.connect(self._on_squeeze)
        self._hard = QTimer(self, singleShot=True)
        self._hard.timeout.connect(self._on_hard)
        self._single = QTimer(self, singleShot=True)
        self._single.timeout.connect(lambda: self.gesture.emit(CLICK, self.zone))
        self._moved_emitted = False

    # ------------------------------------------------------------------ events from the window
    def press(self, zone: str, gpos: QPointF) -> None:
        self.zone, self.pressed, self.moved, self.consumed, self.mode = zone, True, False, False, None
        self.squeezing = False
        self._moved_emitted = False
        self._press = self._last = QPointF(gpos)
        self._sq.start(int(SQUEEZE_SECONDS * 1000))
        self._hard.start(int(HARD_SECONDS * 1000))

    def move(self, gpos: QPointF) -> str | None:
        """Returns "move", "pull" or "lift" once the press turned into a drag, else None."""
        if not self.pressed:
            return None
        dist = (gpos - self._press).manhattanLength()
        if not self.moved and dist > MOVE_PX and not self.squeezing:
            self.moved = True
            self._sq.stop()
            self._hard.stop()
            self._single.stop()
            self.mode = "pull" if self.zone in PULL_ZONES else "move"
        if self.moved and self.mode == "move" and dist >= LIFT_PX and self.zone not in FACE_ZONES:
            self.mode = "lift"
        if self.moved and not self._moved_emitted and dist >= PULL_PX:
            self._moved_emitted = True
            self.gesture.emit(PULL if self.mode == "pull" else MOVE, self.zone)
        self._last = QPointF(gpos)
        return self.mode if self.moved else None

    def release(self) -> None:
        if not self.pressed:
            return
        self.pressed = False
        self._sq.stop()
        self._hard.stop()
        if self.squeezing and not self.consumed:
            self.squeezing = False
            self.gesture.emit(SQUEEZE, self.zone)
            return
        self.squeezing = False
        if self.consumed or self.moved:
            return
        self._single.start(QApplication.doubleClickInterval())   # maybe the first half of a double click

    def double(self, zone: str) -> None:
        self._single.stop()
        self._sq.stop()
        self._hard.stop()
        self.zone, self.pressed, self.consumed, self.moved = zone, True, True, False
        self.squeezing = False
        self.gesture.emit(DOUBLE, zone)

    def hover_stroke(self, zone: str) -> None:
        self.gesture.emit(STROKE, zone)

    def _on_squeeze(self) -> None:
        if self.pressed and not self.moved:
            self.squeezing = True
            self.squeeze_started.emit(self.zone)

    def _on_hard(self) -> None:
        if self.pressed and not self.moved:
            self.consumed = True
            self.squeezing = False
            self.gesture.emit(HARD, self.zone)
