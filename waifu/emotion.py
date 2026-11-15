"""Emotional state machine: AWAKE / SLEEPING / HURT / HAPPY / LISTENING, plus mood and affection.

    AWAKE --idle long / "good night" / night time--> SLEEPING
    SLEEPING --click, gentle pat, voice ("wake up", her name), you return to the webcam--> AWAKE
    any --rough touch (hit, smack, poke in the eye)--> HURT (sulks; gentle touches or "sorry" heal her)
    AWAKE --lots of affection (mood >= 85)--> HAPPY (bouncy, for a while)
    AWAKE/HAPPY --music playing--> LISTENING (bobs to the beat)

Mood (0-100) is short-term and drifts back toward a baseline that grows with affection.
Affection (0-100) is long-term and only changes with how you treat her.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from .config import Config
from .database import Memory

AWAKE, SLEEPING, HURT, HAPPY, LISTENING = "awake", "sleeping", "hurt", "happy", "listening"
APOLOGY_WORDS = ("sorry", "apologize", "apologise", "my bad", "forgive me", "samawenna", "samaawenna",
                 "samawela", "gomen", "i didn't mean")
NICE_ZONES = {"head", "hand", "cheek"}
TOUCHY_ZONES = {"skirt", "chest"}


@dataclass
class Reaction:
    emotion: str
    line: str             # key into reactions.LINES
    mood: float = 0.0
    affection: float = 0.0
    woke: bool = False


class EmotionEngine(QObject):
    state_changed = Signal(str, str)     # old, new
    stats_changed = Signal(float, float)  # mood, affection

    def __init__(self, cfg: Config, memory: Memory):
        super().__init__()
        self.cfg = cfg
        self.mem = memory
        self.mood = float(memory.get("mood", 65.0))
        self.affection = float(memory.get("affection", cfg.affection))
        self.state = AWAKE
        self._until = 0.0
        self._hits: list[float] = []
        self._save_t = 0.0
        self.music_on = False
        self._label = ""
        self._label_until = 0.0
        self._taps: list[float] = []

    # ------------------------------------------------------------------ queries
    def baseline(self) -> float:
        return 50.0 + self.affection * 0.25

    def mood_word(self) -> str:
        m = self.mood
        return ("miserable" if m < 20 else "upset" if m < 40 else "a bit down" if m < 55 else
                "content" if m < 70 else "happy" if m < 85 else "overjoyed")

    def snapshot(self) -> dict:
        situation = {
            SLEEPING: "You were asleep until just now.",
            HURT: "You are hurt and sulking because they were rough with you.",
            HAPPY: "You are bubbly and extra happy right now.",
            LISTENING: "You are listening to music together.",
        }.get(self.state, "")
        label = self.mood_label()
        if label in ("shy", "flustered", "playful") and not situation:
            situation = f"Right now you feel {label}."
        return {"affection": self.affection, "mood": self.mood, "mood_word": self.mood_word(),
                "situation": situation, "state": self.state, "label": label}

    # ------------------------------------------------------------------ transitions
    def _set(self, new: str, seconds: float = 0.0) -> None:
        old = self.state
        self._until = time.monotonic() + seconds if seconds else 0.0
        if new != old:
            self.state = new
            self.state_changed.emit(old, new)

    def _adjust(self, mood: float = 0.0, affection: float = 0.0) -> None:
        self.mood = max(0.0, min(100.0, self.mood + mood))
        self.affection = max(0.0, min(100.0, self.affection + affection))
        self.cfg.affection = self.affection
        self.stats_changed.emit(self.mood, self.affection)

    def sleep(self) -> None:
        if self.state != SLEEPING:
            self.mem.event("sleep", "I took a nap.")
            self._set(SLEEPING)

    def wake(self, gentle: bool = True) -> None:
        if self.state == SLEEPING:
            self._set(AWAKE)
            if not gentle:
                self._adjust(mood=-5)

    def set_music(self, on: bool) -> None:
        self.music_on = on
        if on and self.state in (AWAKE, HAPPY):
            self._set(LISTENING)
        elif not on and self.state == LISTENING:
            self._set(AWAKE)

    def touch(self, zone: str, kind: str) -> Reaction:
        """kind: gentle | tap | rough | tug. Returns how she reacts."""
        now = time.monotonic()
        woke = False
        self.mem.bump(f"touch_{kind}")            # what you like to do with her (habit learning)
        if self.state == SLEEPING:
            woke = True
            self.wake(gentle=kind != "rough")
            if kind != "rough":
                return Reaction("happy", "wake_gentle", 3, 0.5, woke=True)
        if zone in TOUCHY_ZONES:
            # sensitive areas are context-aware:
            #  a gentle stroke (when she trusts you) -> sweet shy smile, affection up
            #  sudden taps / presses / hits         -> extreme blush, flustered recoil, scolding
            if kind == "pet" and self.affection >= 35:
                self._adjust(3, 1.5)
                self._flavour("shy", 20)
                return Reaction("shy", "sensitive_stroke", 3, 1.5, woke)
            close = self.affection >= 65
            self._adjust(-3 if close else -6, -1 if close else -3)
            self._flavour("flustered", 25)
            key = "sensitive_playful" if close and kind != "rough" and random.random() < 0.4 else "sensitive_scold"
            return Reaction("flustered", key, 0, 0, woke)
        if zone in ("eyes", "nose") and kind in ("gentle", "tap"):
            # poke in the eye / nose: instant blink, startled recoil, cute warning (doesn't hurt like a hit)
            self._adjust(-1.5, 0)
            return Reaction("surprised", f"{zone}_poke", -1.5, 0, woke)
        if kind == "squeeze":
            # squishing her (cheeks, body, hands...): cute and a little embarrassing
            gain, aff = (3.0, 1.5) if zone in NICE_ZONES | {"body", "mouth", "nose", "eyes"} else (2.0, 0.5)
            self._adjust(gain, aff)
            self.mem.bump("squeezes")
            self._flavour("playful" if self.affection >= 50 else "shy", 15)
            return Reaction("squeezed", f"{zone}_squeeze", gain, aff, woke)
        if kind == "move":
            # you're moving her head / arm / leg / body around
            self._adjust(0.5, 0.2)
            emo = {"hand": "shy", "legs": "flustered", "head": "surprised"}.get(zone, "surprised")
            return Reaction(emo, f"{zone}_move", 0.5, 0.2, woke)
        if kind == "rough":
            self._hits = [t for t in self._hits if now - t < 20] + [now]
            hurt = -12 if zone == "eyes" else -9
            self._adjust(hurt, -4 if zone == "eyes" else -3)
            self.mem.bump("hits")
            if len(self._hits) == 1:
                self.mem.event("hurt", f"They hit my {zone}.")
            self._set(HURT, 25 + 15 * len(self._hits))
            return Reaction("hurt", "too_hard" if len(self._hits) < 3 else "hurt_repeat", hurt, 0, woke)
        if kind == "tug" and zone in ("hair", "scarf"):
            # pulling her hair: a cute, slightly pouting "ouch, don't pull!"
            self._adjust(-3, -0.5)
            self._flavour("upset", 8)
            return Reaction("pout", f"{zone}_tug", -3, -0.5, woke)
        # pet (double click / stroking) > gentle (short click) > tap
        gain = {"pet": 6.0, "gentle": 3.0}.get(kind, 2.0)
        aff = (3.0 if kind == "pet" else 1.0 if kind in ("gentle", "tug") else 0.5) if zone in NICE_ZONES else \
            (1.0 if kind == "pet" else 0.0)
        if zone in ("eyes", "nose"):
            gain, aff = 1.0, 0.0
        self._adjust(gain, aff)
        if zone == "head":
            self.mem.bump("pats")
        if zone == "hand":
            self.mem.bump("hand_holds")
        if self.state == HURT:
            if self.mood >= 45:
                self._set(LISTENING if self.music_on else AWAKE)
                return Reaction("shy", "forgive", gain, aff, woke)
            return Reaction("hurt", "sulk_comforted", gain, aff, woke)
        if self.mood >= 85 and self.state in (AWAKE, LISTENING):
            self.mem.event("happy", "I felt really happy and loved.")
            self._set(HAPPY, 40)
            return Reaction("excited", "happy_burst", gain, aff, woke)
        emotion = {"head": "happy", "hand": "shy", "cheek": "shy", "mouth": "shy", "eyes": "surprised"}.get(zone, "happy")
        if kind == "pet":
            self._flavour("shy" if zone in ("cheek", "mouth", "hand") else "happy", 15)
            return Reaction("happy", f"{zone}_pet", gain, aff, woke)
        if kind in ("gentle", "tug") and zone in ("hand", "cheek", "mouth"):
            self._flavour("shy", 15)
        elif kind in ("tap", "gentle"):
            self._taps = [t for t in self._taps if now - t < 8] + [now]
            if len(self._taps) >= 3 and self.affection >= 45:
                self._flavour("playful", 20)     # lots of light boops -> she gets playful
                return Reaction("playful", "playful", gain, aff, woke)
        key = f"{zone}_{'gentle' if kind in ('gentle', 'tug') else 'tap'}"
        return Reaction(emotion, key, gain, aff, woke)

    # ------------------------------------------------------------------ mood labels
    def _flavour(self, label: str, seconds: float) -> None:
        self._label, self._label_until = label, time.monotonic() + seconds

    def mood_label(self) -> str:
        """Happy / Shy / Flustered / Sleepy / Upset / Playful / neutral, for the expression matrix."""
        if self.state == SLEEPING:
            return "sleepy"
        if self.state == HURT:
            return "upset"
        if self._label and time.monotonic() < self._label_until:
            return self._label
        if self.state in (HAPPY, LISTENING) or self.mood >= 78:
            return "happy"
        if self.mood < 38:
            return "upset"
        from datetime import datetime
        if datetime.now().hour < 6 or datetime.now().hour >= 23:
            return "sleepy"
        if self.affection >= 70 and self.mood >= 60:
            return "playful" if int(time.monotonic() / 90) % 3 == 0 else "happy"
        return "neutral"

    def struggle(self, limb: str) -> str:
        """Lifted off the ground (by a hand / leg / her body): she wriggles. Returns the line key."""
        if self.state == SLEEPING:
            self.wake(gentle=False)
        self._flavour("struggling", 3600)
        self.mem.bump("lifted")
        return "struggle_leg" if limb.startswith("leg") else "struggle_hand" if limb.startswith("hand") \
            else "struggle_body"

    def released(self) -> None:
        if self._label == "struggling":
            self._label, self._label_until = "flustered", time.monotonic() + 8

    def apology(self, text: str) -> bool:
        low = text.lower()
        if self.state == HURT and any(w in low for w in APOLOGY_WORDS):
            self._adjust(15, 1)
            self._set(LISTENING if self.music_on else AWAKE)
            self.mem.event("forgive", "They apologised and I forgave them.")
            return True
        return False

    def chatted(self) -> None:
        self._adjust(1.0, 0.3)

    def tick(self, dt: float) -> None:
        # mood drifts toward baseline (~1.5 points per minute)
        b = self.baseline()
        step = 1.5 / 60 * dt
        if self.music_on and self.state == LISTENING:
            b += 10
        self.mood += max(-step, min(step, b - self.mood))
        if self._until and time.monotonic() > self._until and self.state in (HURT, HAPPY):
            self._set(LISTENING if self.music_on else AWAKE)
        now = time.monotonic()
        if now - self._save_t > 30:
            self._save_t = now
            self.mem.set("mood", round(self.mood, 2))
            self.mem.set("affection", round(self.affection, 2))
            self.stats_changed.emit(self.mood, self.affection)
