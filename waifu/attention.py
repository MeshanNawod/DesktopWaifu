"""Attention-seeking that learns what works on you.

When you're at the PC (keyboard / mouse active) but haven't touched or talked to her for a
while, she tries to get your attention, escalating every couple of minutes:

    approach_wave   walks over under your cursor and waves
    hop_call        hops and calls out "Psst~ over here!"
    dance           does a little cheer / dance to look cute
    lonely_pout     sits down pouting: "you've been ignoring me..."
    ask_question    asks you something via Gemini (using what she remembers about you)

Every attempt is scored: if you respond (touch her, chat, open the chat) within 45 s it's a
success. Success rates are stored in the SQLite memory and the next tactic is chosen by
weighted chance, so over time she uses what *you* react to.
"""
from __future__ import annotations

import logging
import random
import time

from PySide6.QtCore import QObject, QTimer

from .reactions import line

log = logging.getLogger(__name__)
TACTICS = ["approach_wave", "hop_call", "dance", "lonely_pout", "ask_question"]
RESPONSE_WINDOW = 45.0
RETRY_MINUTES = 2.5
MAX_ATTEMPTS = 4


class AttentionEngine(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.mem = app.memory
        self.pending: tuple[str, float] | None = None
        self.attempts = 0
        self._last_try = 0.0

    # ------------------------------------------------------------------ learning
    def score(self, tactic: str) -> float:
        tries = float(self.mem.get(f"tactic_{tactic}_try", 0.0))
        ok = float(self.mem.get(f"tactic_{tactic}_ok", 0.0))
        return (ok + 1.0) / (tries + 2.0)          # Laplace-smoothed success rate

    def best(self) -> str | None:
        tried = [t for t in TACTICS if self.mem.get(f"tactic_{t}_try", 0.0)]
        return max(tried, key=self.score) if tried else None

    def engaged(self) -> None:
        """You responded to her (touch / chat). Credit the tactic she just tried, reset escalation."""
        if self.pending and time.monotonic() - self.pending[1] <= RESPONSE_WINDOW:
            name = self.pending[0]
            self.mem.bump(f"tactic_{name}_ok")
            log.info("Attention tactic %s worked (score %.2f)", name, self.score(name))
        self.pending = None
        self.attempts = 0

    # ------------------------------------------------------------------ loop (called every second)
    def tick(self, user_idle: float, ignored_for: float) -> None:
        app = self.app
        mins = app.cfg.attention_minutes
        if mins <= 0 or user_idle > 60:            # you're away: nothing to do
            return
        if app.emotion.state not in ("awake", "happy") or app.chat.isVisible() or app.brain.busy \
                or app.speaker.speaking or app.mic.recording or app.music.playing:
            return
        now = time.monotonic()
        if self.pending and now - self.pending[1] > RESPONSE_WINDOW:
            self.pending = None                     # that one didn't work
        if ignored_for < mins * 60 or self.attempts >= MAX_ATTEMPTS:
            return
        if now - self._last_try < RETRY_MINUTES * 60:
            return
        options = [t for t in TACTICS if t != "ask_question" or app.cfg.effective_api_key()]
        if self.attempts == 0:                      # start gently
            options = [t for t in options if t != "lonely_pout"]
        weights = [self.score(t) ** 2 for t in options]
        tactic = random.choices(options, weights)[0]
        self._last_try = now
        self.attempts += 1
        self.pending = (tactic, now)
        self.mem.bump(f"tactic_{tactic}_try")
        log.info("Attention attempt %d: %s", self.attempts, tactic)
        self.run(tactic, ignored_for)

    def run(self, tactic: str, ignored_for: float = 0.0) -> None:
        app, c = self.app, self.app.character
        s = c.sprites
        if tactic == "approach_wave":
            secs = c.walk_to_cursor()
            QTimer.singleShot(int((secs + 0.3) * 1000), lambda: (
                s.start_gesture("wave_l") if hasattr(s, "start_gesture") else c.hop(),
                app.say(line("attention"), "happy")))
        elif tactic == "hop_call":
            c.hop()
            app.say(line("attention"), "excited")
            QTimer.singleShot(900, c.hop)
        elif tactic == "dance":
            if hasattr(s, "start_gesture"):
                s.start_gesture("cheer")
            c.hop()
            app.say(random.choice(["Look, look! I'm dancing~", "Ta-da~! Am I cute?", "Hey~ watch this!"]), "playful")
        elif tactic == "lonely_pout":
            c.set_emotion("pout", 8)
            app.emotion._adjust(-2, 0)
            app.say(random.choice(["...You've been ignoring me. Hmph.", "I'm lonely, you know...",
                                   "Fine, I'll just sit here all alone..."]), "pout")
        elif tactic == "ask_question":
            mins = int(ignored_for // 60)
            user = app.cfg.user_name or "them"
            app.brain.ask(f"(Attention: {user} has been busy on the PC and hasn't paid attention to you for "
                          f"{mins} minutes. Playfully get their attention in one or two sentences: tease them, "
                          "ask about something you remember about them, or ask a question you don't know the "
                          "answer to yet.)", event=True)
