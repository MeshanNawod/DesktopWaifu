"""The 64-expression matrix: 8 eye gazes x 8 facial expressions, chosen from her mood.

Gazes (traditional eye movements):
  direct       looks right at you (follows the cursor)
  side_glance  a sideways glance away (left or right)
  downcast     shy, lowered eyes
  upward       dreamy / thinking, eyes up
  half_lidded  sleepy or sultry half-closed eyes
  closed       peaceful closed eyes / ^^ smile
  sparkle      wide, joyful sparkling eyes
  wink         one eye closed, playful

Faces (micro-expressions):
  calm, gentle_smile, beaming, teasing_smirk, pout, blush, startled, sorrowful

Every combination is a named expression ("downcast+blush", "wink+teasing_smirk", ...).
Each mood has weighted preferences over gazes and faces; ExpressionEngine drifts between
compatible combinations every few seconds, which gives natural micro-expression changes,
and events (touches, replies) snap it to a fitting one immediately.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

GAZES = ["direct", "side_glance", "downcast", "upward", "half_lidded", "closed", "sparkle", "wink"]
FACES = ["calm", "gentle_smile", "beaming", "teasing_smirk", "pout", "blush", "startled", "sorrowful"]
EXPRESSIONS = [f"{g}+{f}" for g in GAZES for f in FACES]          # the 64 expressions
MOODS = ["neutral", "happy", "shy", "flustered", "sleepy", "upset", "playful"]

MOOD_PREFS: dict[str, tuple[dict[str, float], dict[str, float]]] = {
    "neutral":   ({"direct": 5, "side_glance": 2, "upward": 1}, {"calm": 4, "gentle_smile": 4}),
    "happy":     ({"direct": 4, "sparkle": 3, "closed": 2, "side_glance": 1}, {"gentle_smile": 4, "beaming": 4, "blush": 1}),
    "shy":       ({"downcast": 5, "side_glance": 4, "direct": 1}, {"blush": 6, "gentle_smile": 3, "pout": 1}),
    "flustered": ({"side_glance": 4, "downcast": 3, "direct": 2, "sparkle": 1}, {"blush": 4, "startled": 3, "pout": 3}),
    "sleepy":    ({"half_lidded": 6, "closed": 3, "downcast": 1}, {"calm": 6, "gentle_smile": 3}),
    "upset":     ({"downcast": 4, "side_glance": 4, "half_lidded": 1}, {"sorrowful": 5, "pout": 5}),
    "playful":   ({"direct": 3, "side_glance": 3, "wink": 2, "sparkle": 2}, {"teasing_smirk": 6, "beaming": 3, "gentle_smile": 1}),
    "struggling": ({"side_glance": 3, "closed": 2, "direct": 2, "upward": 1}, {"startled": 5, "pout": 4, "sorrowful": 1}),
}
# what a Gemini emotion tag / touch emotion means for her mood
EMOTION_MOOD = {
    "happy": "happy", "excited": "happy", "shy": "shy", "flustered": "flustered", "surprised": "flustered",
    "sleepy": "sleepy", "sad": "upset", "angry": "upset", "hurt": "upset", "playful": "playful",
    "thinking": "neutral", "neutral": "neutral", "struggling": "struggling",
}

# gaze -> (look offset x, y in -1..1, eyelid 0..1, extra)
GAZE_PARAMS = {
    "direct":      ((0.0, 0.0), 0.0),
    "side_glance": ((0.85, 0.0), 0.1),     # x sign is randomised per glance
    "downcast":    ((0.15, -0.8), 0.35),
    "upward":      ((0.3, 0.75), 0.0),
    "half_lidded": ((0.0, -0.2), 0.55),
    "closed":      ((0.0, 0.0), 1.0),
    "sparkle":     ((0.0, 0.1), 0.0),
    "wink":        ((0.2, 0.0), 0.0),
}
# face -> VRM expression weights, blush, head roll
FACE_PARAMS = {
    "calm":          ({}, 0.0, 0.0),
    "gentle_smile":  ({"happy": 0.35}, 0.15, 2.0),
    "beaming":       ({"happy": 1.0, "aa": 0.2}, 0.3, 0.0),
    "teasing_smirk": ({"happy": 0.35, "ih": 0.25}, 0.2, 8.0),
    "pout":          ({"angry": 0.25, "ou": 0.55}, 0.2, -4.0),
    "blush":         ({"relaxed": 0.45, "happy": 0.15}, 0.9, 6.0),
    "startled":      ({"surprised": 0.8, "aa": 0.3}, 0.4, 0.0),
    "sorrowful":     ({"sad": 0.75}, 0.0, -3.0),
}


@dataclass
class Expr:
    gaze: str = "direct"
    face: str = "calm"
    side: float = 1.0                       # which way a side glance goes
    look: tuple[float, float] = (0.0, 0.0)  # added to cursor gaze
    lid: float = 0.0                        # 0 open .. 1 closed
    wink: bool = False
    sparkle: bool = False
    happy_eyes: bool = False                # ^^ closed smile
    weights: dict = field(default_factory=dict)
    blush: float = 0.0
    roll: float = 0.0                       # head tilt in degrees
    follow: float = 1.0                     # how much the eyes still follow the cursor

    @property
    def name(self) -> str:
        return f"{self.gaze}+{self.face}"


def build(gaze: str, face: str, side: float = 1.0) -> Expr:
    (lx, ly), lid = GAZE_PARAMS[gaze]
    w, blush, roll = FACE_PARAMS[face]
    e = Expr(gaze, face, side, (lx * side, ly), lid, gaze == "wink", gaze == "sparkle",
             gaze == "closed" and face in ("gentle_smile", "beaming", "blush", "teasing_smirk"),
             dict(w), blush, roll * side)
    e.follow = {"direct": 1.0, "sparkle": 1.0, "wink": 0.8, "side_glance": 0.15, "downcast": 0.3,
                "upward": 0.2, "half_lidded": 0.6, "closed": 0.0}[gaze]
    if gaze == "sparkle":
        e.weights["surprised"] = max(e.weights.get("surprised", 0.0), 0.15)
    return e


def _pick(prefs: dict[str, float]) -> str:
    items = list(prefs.items())
    r = random.uniform(0, sum(w for _, w in items))
    for k, w in items:
        r -= w
        if r <= 0:
            return k
    return items[-1][0]


class ExpressionEngine:
    def __init__(self):
        self.mood = "neutral"
        self.current = build("direct", "calm")
        self._left = 0.0

    def set_mood(self, mood: str) -> None:
        if mood in MOOD_PREFS and mood != self.mood:
            self.mood = mood
            self._left = 0.0    # react right away

    def snap(self, mood: str) -> Expr:
        """An event happened: jump to a fitting expression for this mood now."""
        self.set_mood(mood)
        self._choose()
        return self.current

    def _choose(self) -> None:
        gazes, faces = MOOD_PREFS.get(self.mood, MOOD_PREFS["neutral"])
        self.current = build(_pick(gazes), _pick(faces), random.choice((-1.0, 1.0)))
        # side glances and winks are brief; settled looks last longer
        base = 1.2 if self.current.gaze in ("side_glance", "wink", "sparkle") else 3.0
        self._left = random.uniform(base, base * 2.2)

    def update(self, dt: float, mood: str, emotion: str | None = None) -> Expr:
        target = EMOTION_MOOD.get(emotion or "", None) if emotion and emotion != "neutral" else None
        self.set_mood(target or mood)
        self._left -= dt
        if self._left <= 0:
            self._choose()
        return self.current
