"""Application controller: wires character, emotions, memory, brain, voice, senses and UI together."""
from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime

from PySide6.QtCore import QObject, QPoint, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import vision
from .gemini_brain import Brain
from .camera import CameraWatcher
from .waifu_core import CharacterWindow
from .attention import AttentionEngine
from .config import Config
from .companions import LABELS as COMPANION_LABELS, NAMES as COMPANION_NAMES, PETS, TOYS, Playground
from .emotion import ANGRY, AWAKE, HAPPY, HURT, LISTENING, SLEEPING, EmotionEngine
from .poses import HELD as HELD_POSES, parse_command, pop_pose_tag
from .singing import HUMS, VOICE_PRESETS, Singer
from .tunes import TuneMemory, analyze_mood, extract_melody
from .wellbeing import Wellbeing, describe as describe_time, parse_reminder
from .config import APP_DIR
from .database import Memory
from .music import MusicListener, now_playing
from .reactions import line
from .sprites import default_vrm, list_characters
from .ui.bubble import SpeechBubble
from .ui.chat import ChatPanel
from .ui.settings import SettingsDialog
from .voice import MicRecorder, Speaker
from .workers import run_async

log = logging.getLogger(__name__)

SCREEN_WORDS = ("my screen", "look at this", "what am i doing", "see this", "what do you see", "look at my")
CAMERA_WORDS = ("look at me", "can you see me", "see me", "how do i look", "what am i holding", "what is this",
                "what's this", "whats this", "what am i wearing", "do you see", "webcam", "camera")
SLEEP_WORDS = ("go to sleep", "good night", "goodnight", "time to sleep", "take a nap", "sleep now")
WAKE_WORDS = ("wake up", "wakey", "good morning", "hey", "hello", "oi", "nagitinna", "naginna")
REMEMBER_RE = re.compile(r"^\s*(?:please\s+)?remember(?: that)?\s+(.+)$", re.I)

ZONE_GROUP = {"eye_l": "eyes", "eye_r": "eyes", "cheek_l": "cheek", "cheek_r": "cheek",
              "hand_l": "hand", "hand_r": "hand", "hand_face": "hand", "hand_low": "hand",
              "hair_l": "hair", "hair_r": "hair", "leg_l": "legs", "leg_r": "legs", "nose": "nose"}
TOUCH_DESC = {
    ("head", "gentle"): "gently stroked your head", ("head", "tap"): "patted your head",
    ("head", "rough"): "hit you on the head", ("hand", "gentle"): "is holding your hand",
    ("hand", "tap"): "touched your hand", ("hand", "rough"): "slapped your hand",
    ("hand", "tug"): "gently pulled you by the hand", ("mouth", "gentle"): "softly touched your lips",
    ("mouth", "tap"): "booped your mouth", ("mouth", "rough"): "hit you on the mouth",
    ("eyes", "gentle"): "covered your eyes", ("eyes", "tap"): "poked near your eye",
    ("eyes", "rough"): "poked you hard in the eye", ("cheek", "gentle"): "stroked your cheek",
    ("cheek", "tap"): "poked your cheek", ("cheek", "rough"): "slapped your cheek",
    ("hair", "gentle"): "ran their cursor through your hair", ("hair", "tug"): "tugged your hair",
    ("hair", "rough"): "yanked your hair", ("skirt", "any"): "touched your skirt",
    ("chest", "any"): "touched your chest", ("body", "rough"): "pressed hard into your side",
    ("head", "pet"): "is lovingly stroking your head", ("hand", "pet"): "is gently stroking your hand",
    ("cheek", "pet"): "is softly stroking your cheek", ("mouth", "pet"): "gently traced your lips",
    ("hair", "pet"): "is stroking your hair", ("body", "pet"): "is petting you",
    ("legs", "pet"): "is stroking your leg", ("legs", "rough"): "pressed hard on your leg",
    ("legs", "gentle"): "lightly touched your leg",
    ("head", "squeeze"): "is squishing your head", ("cheek", "squeeze"): "is squishing your cheeks",
    ("body", "squeeze"): "is squeezing you in a hug", ("hand", "squeeze"): "is squeezing your hand",
    ("head", "move"): "is turning your head with their cursor", ("hand", "move"): "is moving your arm around",
    ("legs", "move"): "is lifting your leg", ("body", "move"): "is pushing you so you lean",
    ("hair", "tug"): "is pulling your hair",
    ("nose", "gentle"): "poked your nose", ("nose", "rough"): "jabbed your nose hard",
    ("nose", "pet"): "gently booped your nose", ("eyes", "gentle"): "poked you in the eye",
    ("chest", "pet"): "gently stroked you near your chest", ("skirt", "pet"): "gently stroked your skirt",
}


class WaifuApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.qapp = app
        self.cfg = Config.load()
        self.memory = Memory()
        self.emotion = EmotionEngine(self.cfg, self.memory)
        self.last_user_msg = time.monotonic()
        self.last_exchange = time.monotonic()
        self._last_quip = 0.0
        self._last_ai_touch = 0.0
        self._last_greet = 0.0
        self._away_since: float | None = None
        self._last_song = ""
        self._last_song_t = 0.0
        self._wake_listening = False

        self.character = CharacterWindow(self.cfg)
        self.bubble = SpeechBubble()
        self.chat = ChatPanel(self.cfg.name)
        self.brain = Brain(self.cfg, self.memory, self.emotion.snapshot)
        self.speaker = Speaker(self.cfg)
        self.mic = MicRecorder(self.cfg)
        self.fg = vision.ForegroundTracker()
        self.camera = CameraWatcher(self.cfg.webcam_index)
        self.music = MusicListener()
        self.singer = Singer(self.cfg)
        self.tunes = TuneMemory(APP_DIR / "tunes.json", persist=self.cfg.can("memory"))
        from . import humvoice
        self.humvoice = humvoice.HumVoice()
        humvoice.prepare_reference(self)                   # your hum recording -> her humming voice
        if self.cfg.can("cloud") and self.cfg.tts_engine == "edge":
            QTimer.singleShot(8000, lambda: self.humvoice.prepare(self.cfg.tts_voice, self.cfg.tts_pitch))
        self._music_t0 = 0.0
        self._last_learn = 0.0
        self._last_mood_t = 0.0
        self._song_mood = ""
        self._earworm_at = 0.0
        self.well = Wellbeing(self.cfg)
        self._next_work_comment = 0.0
        self.memory.private = not self.cfg.can("memory")
        self.fg.enabled = self.cfg.can("titles")
        self._next_song = time.monotonic() + 120

        c = self.character
        c.moved.connect(self._follow)
        c.touched.connect(self.on_touch)
        c.picked_up.connect(self._on_pickup)
        c.cute.connect(lambda name: (setattr(self, "_last_quip", 0.0),
                                     self._quip(f"cute_{name}", "happy", chance=0.85)))
        c.knocked.connect(lambda: (setattr(self, "_last_quip", 0.0), self._quip("knocked", "hurt")))
        c.got_up.connect(lambda: (setattr(self, "_last_quip", 0.0), self._quip("get_up", "pout")))
        c.broke_free.connect(lambda: (self.emotion.released(), setattr(self, "_last_quip", 0.0),
                                      self._quip("broke_free", "playful")))
        c.dropped.connect(self._on_drop)
        c.double_clicked.connect(self.toggle_chat)
        c.menu_requested.connect(self._show_menu)

        self.chat.send_text.connect(self.on_user_text)
        self.chat.mic_clicked.connect(self.toggle_mic)
        self.chat.look_clicked.connect(lambda: self.look_at_screen("What do you think of what I'm doing?"))
        self.chat.camera_clicked.connect(lambda: self.look_at_me("Look at me! What do you see?"))
        self.chat.settings_clicked.connect(self.open_settings)
        self.chat.closed.connect(lambda: c.set_busy(False))

        self.brain.reply.connect(self.on_reply)
        self.brain.error.connect(self.on_error)
        self.brain.busy_changed.connect(lambda b: self.chat.set_status("thinking…" if b else self._status()))
        self.brain.learned.connect(self._on_learned)

        self.emotion.state_changed.connect(self._on_state)
        self.emotion.stats_changed.connect(lambda *_: self._update_status())

        self.speaker.mouth.connect(c.set_mouth)
        self.speaker.started.connect(lambda: c.set_talking(True))
        self.speaker.finished.connect(lambda: c.set_talking(False))
        self.speaker.error.connect(lambda m: log.warning("TTS error: %s", m))

        self.mic.started.connect(self._mic_started)
        self.mic.stopped.connect(lambda: self.chat.set_recording(False))
        self.mic.audio_ready.connect(self._on_audio)
        self.mic.nothing_heard.connect(self._on_nothing_heard)
        self.mic.error.connect(self.on_error)

        self.camera.presence_changed.connect(self._on_presence)
        self.camera.error.connect(self.on_error)
        self.camera.started.connect(lambda: self._update_status())
        self.camera.stopped.connect(lambda: self._update_status())
        self.singer.mouth.connect(c.set_mouth)
        self.singer.started.connect(lambda: c.set_singing(True))
        self.singer.finished.connect(lambda: (c.set_singing(False), self.bubble.say("", 0.1)))
        self.singer.line.connect(lambda t: (self.bubble.follow(c.head_anchor()), self.bubble.say(t, 9)))
        c.sick.connect(self._on_sick)
        self.music.beat.connect(lambda s: c.set_music(self.music.playing, s) if self.music.playing else None)
        self.music.playing_changed.connect(self._on_music)
        self.music.error.connect(lambda m: log.warning(m))

        self.attention = AttentionEngine(self)
        self.vision_timer = QTimer(self)
        self.vision_timer.timeout.connect(self._auto_glance)
        self.life = QTimer(self)
        self.life.timeout.connect(self._life_tick)
        self.life.start(1000)
        self._life_n = 0

        self._build_tray()
        self._apply_senses()
        c.show()
        self.play = Playground(c, self.cfg)
        self.play.event.connect(self._on_play)
        self.play.menu_requested.connect(self._show_menu)
        QTimer.singleShot(1200, self.play.restore)

        if not self.cfg.effective_api_key():
            QTimer.singleShot(1500, self._first_run)
        else:
            QTimer.singleShot(1500, self._greet_on_start)

    # ----------------------------------------------------------------- talking
    def say(self, text: str, emotion: str | None = None, speak: bool = True) -> None:
        if emotion:
            self.character.set_emotion(emotion)
        self.bubble.follow(self.character.head_anchor())
        self.bubble.say(text, self.cfg.bubble_seconds)
        if speak:
            if self.singer.singing:
                self.singer.stop()
            self.speaker.speak(text)

    def _quip(self, key: str, emotion: str | None = None, chance: float = 1.0) -> None:
        now = time.monotonic()
        if now - self._last_quip < 2.5 or random.random() > chance or self.brain.busy:
            return
        self._last_quip = now
        self.say(line(key), emotion)

    def _greet_on_start(self) -> None:
        hour = datetime.now().hour
        part = "morning" if 5 <= hour < 12 else "afternoon" if hour < 18 else "evening"
        if self.memory.message_count() == 0:
            self.say(f"Hi! I'm {self.cfg.name}~ Double-click me to chat!", "happy")
        else:
            self.brain.ask(f"(Event: you just started up for the {part}. Greet {self.cfg.user_name or 'them'} "
                           "warmly in one sentence; you may mention something you remember.)", event=True)

    def on_user_text(self, text: str) -> None:
        self.attention.engaged()
        self.memory.bump("chat_msgs")
        now = time.monotonic()
        self.last_user_msg = self.last_exchange = now
        self.chat.add_message(self.cfg.user_name or "You", text, "you")
        if self.emotion.state == SLEEPING:
            self.emotion.wake(gentle=True)
        self.emotion.chatted()
        self.emotion.apology(text)
        low = text.lower()
        m = REMEMBER_RE.match(text)
        if m:
            self.brain.remember(m.group(1).strip())
        r = parse_reminder(text)
        if r:
            self.cfg.reminders.append(r)
            self.cfg.save()
            self.say(f"Okay! I'll remind you {describe_time(r['at'])} to {r['text']}"
                     + (" — every day!" if r["repeat"] else "~"), "happy")
            return
        if re.search(r"\b(watch|see) (me|my) (work|working|code|coding)\b", low):
            self._toggle("watch_work", True)
            self.say(line("watch_on"), "happy")
            return
        if re.search(r"\b(stop|don'?t) (watching|peeking|looking at my work)\b", low):
            self._toggle("watch_work", False)
            self.say(line("watch_off"), "pout")
            return
        if re.search(r"\b(tea|break) (with me|time)\b|\blet'?s (have|drink) tea\b", low):
            self.tea_break()
            return
        cmd = parse_command(text)
        if cmd and self.do_command(cmd):
            return
        if any(w in low for w in SLEEP_WORDS):
            self.say(line("fall_asleep"), "sleepy")
            QTimer.singleShot(2500, self.emotion.sleep)
            return
        if any(w in low for w in CAMERA_WORDS) and self.camera.running:
            self.look_at_me(text)
            return
        if any(w in low for w in SCREEN_WORDS):
            self.look_at_screen(text)
            return
        self.bubble.follow(self.character.head_anchor())
        self.bubble.thinking()
        self.character.set_emotion("thinking", 30)
        self.brain.ask(text)

    # ----------------------------------------------------------------- singing
    def sing(self, kind: str = "song") -> None:
        self.speaker.stop()
        self._next_song = time.monotonic() + max(1.0, self.cfg.sing_minutes) * 60 * random.uniform(0.7, 1.4)
        self.bubble.follow(self.character.head_anchor())
        self.bubble.say(random.choice(HUMS), 3)
        self.singer.sing(kind, self.cfg.user_name)
        if hasattr(self, "play") and random.random() < 0.5:
            self.play.party_t = 8.0                       # the pets bop along

    # ----------------------------------------------------------------- looking after you
    def _care_tick(self, idle: float) -> None:
        c = self.character
        for kind, data in self.well.tick(5.0, idle):
            if kind == "reminder":
                self._remind(data)
            elif self.emotion.state == SLEEPING or self.chat.isVisible() and kind not in ("watch", "unwatch"):
                continue
            elif kind == "break":
                self._last_quip = 0.0
                self._quip("break", "happy")
                if hasattr(c.sprites, "start_gesture"):
                    c.sprites.start_gesture("stretch")
                QTimer.singleShot(5000, lambda: self.play.start_activity("tea"))
            elif kind in ("water", "eyes", "night", "welcome_break"):
                self._last_quip = 0.0
                self._quip(kind, {"night": "sleepy", "eyes": "shy"}.get(kind, "happy"))
                if kind == "water":
                    c.hold("tea", 6.0)
                if kind == "welcome_break":
                    self.well.took_break()
            elif kind == "tea":
                self.tea_break()
            elif kind == "watch":
                c.watching = data
                self._next_work_comment = time.monotonic() + random.uniform(150, 300)
                self._last_quip = 0.0
                self._quip(f"watch_{data}", "happy", chance=0.8)
            elif kind == "unwatch":
                c.watching = ""
        if c.watching and time.monotonic() > self._next_work_comment and not self.brain.busy:
            self._next_work_comment = time.monotonic() + random.uniform(240, 480)
            self._quip(f"help_{c.watching}", "thinking" if c.watching == "coding" else "happy", chance=0.7)

    def _remind(self, r: dict) -> None:
        c = self.character
        name = self.cfg.user_name or ""
        msg = f"⏰ {name + ', ' if name else ''}it's time to {r['text']}!"
        if self.emotion.state == SLEEPING:
            self.emotion.wake(gentle=True)
        self._last_quip = 0.0
        self.say(msg, "excited")
        if hasattr(self, "tray"):
            self.tray.showMessage(f"{self.cfg.name} reminds you", f"Time to {r['text']}", self.tray.icon(), 15000)
        c.walk_to_cursor()
        QTimer.singleShot(1500, lambda: c.do_cute("wave"))

    def tea_break(self) -> None:
        self._last_quip = 0.0
        self._quip("tea_invite", "happy")
        self.well.took_break()
        QTimer.singleShot(1200, lambda: self.play.start_activity("tea", force=True))

    def add_reminder_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(None, "New reminder", "What should I remind you about?\n"
                                        "e.g.  at 5:30 pm to call mom  ·  in 20 minutes to stretch  ·  every day at 8am to "
                                        "take my medicine")
        if ok and text.strip():
            t = text.strip()
            r = parse_reminder(t if t.lower().startswith("remind") else "remind me " + t)
            if r:
                self.cfg.reminders.append(r)
                self.cfg.save()
                self.say(f"Got it! I'll remind you {describe_time(r['at'])} to {r['text']}~", "happy", speak=False)
            else:
                self.say("Hmm, I didn't catch the time... try 'at 5pm to ...' or 'in 10 minutes to ...'", "thinking",
                         speak=False)

    def _hum_learned(self, earworm: bool = False) -> bool:
        """Hum a melody she picked up from your music."""
        c = self.character
        if (self.singer.singing or self.speaker.speaking or self.brain.busy or self.music.playing
                or self.emotion.state not in (AWAKE, HAPPY) or c.ctx.angry > 0.3):
            return False
        t = self.tunes.pick()
        if t is None:
            return False
        name = t.get("title", "")
        short = (name.split(" - ")[0][:28] + "…") if len(name) > 30 else name
        label = f"Hmm hmm~ ♪ ({short})" if short else "Hmm hmm~ ♪ (that song you played~)"
        self.speaker.stop()
        self._next_song = time.monotonic() + max(1.0, self.cfg.sing_minutes) * 60 * random.uniform(0.7, 1.4)
        self.bubble.follow(c.head_anchor())
        self.bubble.say("That song is stuck in my head~ ♪" if earworm else label, 3)
        self.singer.sing("hum", self.cfg.user_name, tune=t["notes"], label=label)
        return True

    def _maybe_sing(self) -> None:
        c = self.character
        if not self.cfg.sing_randomly or self.singer.singing or time.monotonic() < self._next_song:
            return
        if (self.emotion.state not in (AWAKE, HAPPY) or self.chat.isVisible() or self.speaker.speaking
                or self.brain.busy or self.mic.recording or self.music.playing or c.state not in ("idle", "sit", "walk")
                or c.ctx.angry > 0.3):
            self._next_song = time.monotonic() + 45
            return
        if self.tunes.tunes and self.cfg.learn_tunes and random.random() < 0.55 and self._hum_learned():
            return
        self.sing(random.choice(["hum", "song", "song"]))

    def _on_sick(self, what: str) -> None:
        self._last_quip = 0.0
        if what == "splash":
            self.play.add_mess(self.character._splash_x)
            return
        if what == "queasy":
            self._quip("queasy", "struggling")
        elif what == "puke":
            self.emotion._adjust(-6, -1)
            self.memory.event("sick", "They shook me so hard I threw up.")
            self._quip("puke", "hurt")
        else:
            QTimer.singleShot(300, lambda: (setattr(self, "_last_quip", 0.0), self._quip("puke_after", "pout")))
            QTimer.singleShot(2200, self.play.clean_up)          # she looks at the mess... and cleans it up

    def set_voice(self, key: str) -> None:
        _label, voice, rate, pitch = VOICE_PRESETS[key]
        self.cfg.voice_preset, self.cfg.tts_engine = key, "edge"
        self.cfg.tts_voice, self.cfg.tts_rate, self.cfg.tts_pitch = voice, rate, pitch
        self.cfg.save()
        if self.cfg.can("cloud"):
            self.humvoice.prepare(voice, pitch)
        self.speaker.stop()
        was = self.cfg.tts_enabled
        self.cfg.tts_enabled = True                       # let her say something so you can hear it
        self.say(f"Hi~! Is this voice cute? I'm {self.cfg.name}!", "happy")
        self.cfg.tts_enabled = was

    def set_privacy(self, on: bool) -> None:
        self.cfg.privacy_mode = on
        self.cfg.save()
        self._apply_senses()
        self._last_quip = 0.0
        self.say(line("privacy_on" if on else "privacy_off"), "shy" if on else "happy", speak=False)

    def do_command(self, name: str, quiet: bool = False) -> bool:
        """Do a pose / action you asked for. Returns True if it was handled."""
        c = self.character
        if self.emotion.state == SLEEPING:
            self.emotion.wake(gentle=True)
        angry = self.emotion.state in (ANGRY, HURT)
        if name in HELD_POSES or name == "stand":
            if not c.do_pose(name):
                self.say(line("pose_cant"), "surprised")
                return True
        elif name == "turn_around":
            c.rotate_by(180)
        elif name == "face_me":
            c.face_me()
        elif name == "sleep":
            QTimer.singleShot(1500, self.emotion.sleep)
        elif name in ("sing", "hum"):
            self.sing("song" if name == "sing" else "hum")
            return True
        elif name in ("plant", "plant_trees"):
            ok = self.play.plant_trees(3) if name == "plant_trees" else self.play.start_activity("plant", species="flower")
            if not ok:
                self.say(line("pose_cant"), "surprised")
            return True
        else:
            if c.user_pose:
                c.stand_up()
            c.do_cute(name)
        if not quiet:
            self._last_quip = 0.0
            key = "pose_angry" if angry else {"stand": "stand_up", "sit": "sit_down"}.get(name, "pose_ok")
            self.say(line(key), "pout" if angry else "happy")
        return True

    def on_reply(self, text: str, emotion: str) -> None:
        m = re.search(r"\[mood:(\w+)\]", text, re.I)
        if m:
            text = (text[:m.start()] + text[m.end():]).strip()
            mood = m.group(1).lower()
            if mood in ("energetic", "happy", "calm", "sad", "intense") and self.music.playing:
                self._song_mood = ""
                self._on_song_mood({"mood": mood, "bpm": self.music.bpm})
        text, pose = pop_pose_tag(text)
        if pose:
            self.do_command(pose, quiet=True)
        self.last_exchange = time.monotonic()
        self.bubble.stop_thinking()
        self.chat.add_message(self.cfg.name, text, "her")
        self.say(text, emotion)

    def on_error(self, msg: str) -> None:
        log.error(msg)
        self.bubble.stop_thinking()
        self.chat.add_message("", msg, "system")
        key_problem = "API key" in msg or "API_KEY" in msg
        busy = "overloaded" in msg or "quota" in msg or "too long" in msg
        self.say("I need a Gemini API key first!" if key_problem else
                 "My cloud brain is super busy right now... try again in a moment?" if busy else
                 "Ah... my brain glitched. Check the chat for details.", "sad" if not busy else "sleepy", speak=False)
        if key_problem:
            self.open_settings()

    def _on_learned(self, facts: list) -> None:
        self.chat.add_message("", "Remembered: " + "; ".join(facts), "system")

    # ----------------------------------------------------------------- touch & emotions
    def on_touch(self, event: str) -> None:
        self.attention.engaged()
        zone, _, kind = event.partition(":")
        if kind == "turn":                      # you rotated her (right-drag)
            self._quip("turned", "surprised", chance=0.5)
            return
        group = ZONE_GROUP.get(zone, zone)
        was_asleep = self.emotion.state == SLEEPING
        r = self.emotion.touch(group, kind or "tap")
        self.character.set_mood(self.emotion.mood_label())
        self.character.set_emotion(r.emotion, 5.0)
        if was_asleep:
            self.character.wake()
            self._quip("wake_rough" if kind == "rough" else "wake_gentle", r.emotion)
            return
        now = time.monotonic()
        desc = TOUCH_DESC.get((group, kind)) or TOUCH_DESC.get((group, "any")) or \
            TOUCH_DESC.get(("body", kind)) or f"touched your {group}"
        use_ai = (self.cfg.ai_touch_reactions and self.cfg.effective_api_key() and not self.brain.busy
                  and now - self._last_ai_touch > 30 and kind in ("pet", "rough", "squeeze"))
        if use_ai:
            self._last_ai_touch = self._last_quip = now
            user = self.cfg.user_name or "the user"
            feel = ("painful, a hard press" if kind == "rough" else "loving and gentle" if kind == "pet"
                    else "sudden, teasing and embarrassing" if group in ("skirt", "chest") else "nice")
            self.brain.ask(f"(Touch: {user} {desc}. It felt {feel}. "
                           "React in one short line, with a cute voice.)", event=True)
            return
        if now - self._last_quip < (0.6 if kind in ("rough", "gentle", "tug", "move", "squeeze") else 1.5):
            return
        self._last_quip = now
        self.say(line(r.line), r.emotion)

    def _on_play(self, what: str, kind: str) -> None:
        name = COMPANION_NAMES.get(kind, kind)
        self.attention.engaged()
        if what == "pet_hurt":
            key = self.emotion.angered(name)
            self._last_quip = 0.0
            self.say(line(key), "angry")
            return
        if what in ("plant_destroyed", "plant_removed"):
            self.emotion.angered(f"{kind}" if kind else "flower")
            self._last_quip = 0.0
            self.say(line(what), "angry")
            return
        if what in ("plant_start", "tree_start", "plant_done", "water", "plant_bloom", "plant_poked", "plant_loved"):
            if what in ("plant_bloom", "plant_loved"):
                self.emotion._adjust(2.0, 0.4)
            self._last_quip = 0.0 if what in ("plant_bloom", "plant_start") else self._last_quip
            self._quip(what, "excited" if what == "plant_bloom" else "happy", chance=0.8)
            return
        if what == "bonk":
            self._last_quip = 0.0
            self._quip("bonk", "angry")
            return
        if what == "arrived":
            self._last_quip = 0.0
            self._quip("pet_arrived" if kind in PETS else "toy_arrived", "excited")
            return
        if what == "gift":
            self.emotion._adjust(3, 1.0)
            self._last_quip = 0.0
            self._quip("gift_teddy" if kind == "teddy" else "gift_pet" if kind in PETS else "gift_ball", "happy")
            return
        if what in ("clean_eww", "clean_spray", "clean_mop", "clean_done", "mess_poked"):
            self._last_quip = 0.0
            self._quip(what, {"clean_eww": "sad", "clean_done": "happy"}.get(what, "neutral"))
            return
        if what in ("her_cuddles", "throw", "treat", "pet_ate", "party", "read", "tea", "exercise", "yarn_play"):
            self.emotion._adjust(1.0, 0.1)
            self._last_quip = 0.0 if what in ("party", "throw", "treat") else self._last_quip
            self._quip(what, "happy", chance=0.75)
            return
        if what in ("her_pets", "her_hugs", "pet_fetched"):
            self.emotion._adjust(1.5, 0.2)
            self._quip(what, "happy", chance=0.8)
            return
        if what in ("kick", "angry_kick"):
            self._quip(what, "angry" if what == "angry_kick" else "playful", chance=0.5)
            return
        if what == "pet_petted":
            self.emotion._adjust(1.0, 0.3)
            self._quip("pet_petted", "happy", chance=0.35)
            return
        if what in ("pet_boop", "pet_nuzzle"):
            self._quip(what, "happy", chance=0.3)

    def _on_pickup(self, limb: str = "body") -> None:
        key = self.emotion.struggle(limb)
        self.character.set_mood("struggling")
        self.character.set_emotion("struggling", 4.0)
        self._last_quip = 0.0
        self._quip(key, "struggling")

    def _on_drop(self, impact: float) -> None:
        self.emotion.released()
        if self.character._broke_free:      # she jumped out of your hand: her "I'm free!" line stands
            return
        self.character.set_mood(self.emotion.mood_label())
        self._last_quip = 0.0
        self._quip("drop_hard" if impact > 900 else "put_down", "sad" if impact > 900 else "flustered", chance=0.7)

    def _on_state(self, old: str, new: str) -> None:
        log.info("Emotion state %s -> %s", old, new)
        c = self.character
        if new == SLEEPING:
            self.speaker.stop()
            c.go_to_sleep()
            self._start_wake_listening()
        elif old == SLEEPING:
            self._stop_wake_listening()
            c.wake()
        if new == HURT:
            c.set_sulk(True)
            c.set_emotion("hurt", 30)
        elif old == HURT:
            c.set_sulk(False)
        if new == ANGRY:
            c.set_angry(True)
        elif old == ANGRY:
            c.set_angry(False)
            if new in (AWAKE, LISTENING):
                QTimer.singleShot(400, lambda: (setattr(self, "_last_quip", 0.0), self._quip("calm_down", "pout", 0.6)))
        if new == HAPPY:
            c.set_emotion("excited", 10)
            if hasattr(c.sprites, "start_gesture"):
                c.sprites.start_gesture("cheer")
        c.set_music(new == LISTENING and self.music.playing)
        self._update_status()

    # ----------------------------------------------------------------- voice input / voice wake
    def toggle_mic(self) -> None:
        self._stop_wake_listening()
        if not self.mic.recording:
            self.speaker.stop()
        self.mic.toggle()
        self.chat.set_recording(self.mic.recording)

    def _mic_started(self) -> None:
        if self._wake_listening:
            return
        self.chat.set_recording(True)
        if self.emotion.state == SLEEPING:
            self.emotion.wake(gentle=True)
        self.say("I'm listening~", "happy", speak=False)

    def _on_audio(self, wav: bytes) -> None:
        if self._wake_listening:
            self.brain.transcribe(wav, self._wake_check, quiet=True)
            return
        self.bubble.thinking()
        self.chat.set_status("transcribing…")

        def got(text: str) -> None:
            self.chat.set_status(self._status())
            if text:
                self.on_user_text(text)
            else:
                self.bubble.stop_thinking()
                self.say("Sorry, I couldn't catch that.", "sad")

        self.brain.transcribe(wav, got)

    def _on_nothing_heard(self) -> None:
        if self._wake_listening:
            QTimer.singleShot(300, self._listen_for_wake)
        else:
            self.say("Hm? I didn't hear anything.", "thinking")

    def _start_wake_listening(self) -> None:
        if self.cfg.can("mic") and self.cfg.can("cloud") and self.cfg.effective_api_key() and not self.mic.recording:
            self._wake_listening = True
            self._listen_for_wake()

    def _listen_for_wake(self) -> None:
        if self._wake_listening and self.emotion.state == SLEEPING and not self.mic.recording:
            self.mic.start()

    def _stop_wake_listening(self) -> None:
        if self._wake_listening:
            self._wake_listening = False
            if self.mic.recording:
                self.mic.stop()

    def _wake_check(self, text: str) -> None:
        low = text.lower()
        name = self.cfg.name.lower()
        if text and (name in low or any(w in low for w in WAKE_WORDS)):
            self._wake_listening = False
            self.emotion.wake(gentle=True)
            self.say(line("wake_voice"), "happy")
            if len(text.split()) > 3:   # they said more than just her name: answer it
                QTimer.singleShot(1500, lambda: self.on_user_text(text))
        else:
            QTimer.singleShot(300, self._listen_for_wake)

    # ----------------------------------------------------------------- vision: screen + webcam
    def look_at_screen(self, prompt: str, auto: bool = False) -> None:
        if not self.cfg.can("screen"):
            if not auto:
                self.say(line("privacy_blocked"), "shy")
            return
        title = self.fg.title
        if vision.is_blocked(title, self.cfg.vision_blocklist):
            if not auto:
                self.say("That looks private, so I'll cover my eyes!", "shy")
            return
        if not auto:
            self.bubble.follow(self.character.head_anchor())
            self.bubble.thinking()
            self.character.set_emotion("thinking", 30)
        side = self.cfg.vision_max_side
        run_async(lambda: vision.capture_screen(side),
                  lambda img: self.brain.ask(prompt, image=img, window_title=title, auto=auto),
                  self.on_error if not auto else (lambda m: log.warning("capture failed: %s", m)))

    def look_at_me(self, prompt: str) -> None:
        if not self.cfg.can("webcam") and self.cfg.privacy_mode:
            self.say(line("privacy_blocked"), "shy")
            return
        if not self.camera.running:
            self.say("Turn on my webcam eyes first! Right-click me, then 'Let her see me'.", "thinking")
            return
        img = self.camera.snapshot_jpeg()
        if img is None:
            self.say("My camera is still waking up... try again in a second!", "thinking")
            return
        self.bubble.follow(self.character.head_anchor())
        self.bubble.thinking()
        self.character.set_emotion("thinking", 30)
        self.brain.ask(prompt + " (If they're showing you an object, identify it and chat about it.)",
                       image=img, image_kind="webcam")

    def _on_presence(self, present: bool) -> None:
        now = time.monotonic()
        if not present:
            self._away_since = now
            return
        away = now - self._away_since if self._away_since else 0.0
        self._away_since = None
        if away < 60:
            return
        self.memory.event("presence", f"They came back after {int(away // 60)} min away.")
        if self.emotion.state == SLEEPING:
            self.emotion.wake(gentle=True)
        if now - self._last_greet < 600:
            return
        self._last_greet = now
        img = self.camera.snapshot_jpeg()
        if self.cfg.webcam_greet and img and self.cfg.effective_api_key() and not self.brain.busy:
            self.brain.ask(f"(Webcam: {self.cfg.user_name or 'they'} just came back to the PC after "
                           f"{int(away // 60)} minutes away. Greet them warmly; you may mention something you notice.)",
                           image=img, image_kind="webcam", event=True)
        else:
            self._quip("welcome_back", "happy")

    def _auto_glance(self) -> None:
        if self.brain.busy or self.mic.recording or self.speaker.speaking or self.emotion.state == SLEEPING:
            return
        if time.monotonic() - self.last_user_msg < 90:
            return
        self.look_at_screen("Take a quick look and make one short, natural comment if something is interesting.",
                            auto=True)

    # ----------------------------------------------------------------- music
    def _on_music(self, playing: bool) -> None:
        self.emotion.set_music(playing)
        self.character.set_music(playing)
        if playing:
            self._music_t0 = time.monotonic()
            self._song_mood = ""
            if self.singer.singing:
                self.singer.stop()
            if self.emotion.state != SLEEPING:
                QTimer.singleShot(6000, self._feel_the_song)
                QTimer.singleShot(2000, self._comment_on_music)
        else:
            self.character.ctx.groove = ""
            self._quip("music_stop", "neutral", chance=0.3)
            if self.tunes.tunes and self.cfg.learn_tunes and random.random() < 0.85:
                self._earworm_at = time.monotonic() + random.uniform(12, 45)   # the song is stuck in her head

    # ----------------------------------------------------------------- feeling the music (local analysis)
    def _clip_pcm(self, seconds: float = 10.0):
        import numpy as np
        wav = self.music.clip_wav(seconds)
        if not wav:
            return None
        return np.frombuffer(wav[44:], dtype=np.int16)

    def _feel_the_song(self) -> None:
        """Work out the song's mood from the sound (on this PC) and dance / react to match."""
        if not self.music.playing or not self.cfg.can("music"):
            return
        pcm = self._clip_pcm(10)
        if pcm is None:
            return
        bpm = self.music.bpm
        run_async(lambda: analyze_mood(pcm, bpm), self._on_song_mood, lambda m: log.info("mood failed: %s", m))

    def _on_song_mood(self, info: dict) -> None:
        mood = info.get("mood", "happy")
        c = self.character
        c.bpm = info.get("bpm") or self.music.bpm
        first = not self._song_mood
        if mood == self._song_mood and not first:
            return
        self._song_mood = mood
        self._last_mood_t = time.monotonic()
        c.ctx.groove = mood
        c.set_emotion({"energetic": "excited", "happy": "happy", "calm": "shy", "sad": "sad",
                       "intense": "excited"}[mood], 12.0)
        if mood == "energetic" and hasattr(self, "play"):
            self.play.party_t = 10.0                         # the pets join in
        if not self.brain.busy and (not self.cfg.can("cloud") or not self.cfg.can("titles") or not first
                                    or not self.cfg.effective_api_key()):
            self._last_quip = 0.0
            self._quip(f"song_{mood}", None, chance=0.8)

    def _maybe_learn_tune(self) -> None:
        if not (self.music.playing and self.cfg.learn_tunes and self.cfg.can("music")):
            return
        now = time.monotonic()
        if now - self._music_t0 < 12 or now - self._last_learn < 25:
            return
        self._last_learn = now
        pcm = self._clip_pcm(10)
        if pcm is None:
            return
        title = (now_playing() or "") if self.cfg.can("titles") else ""
        key = title or f"song@{int(self._music_t0)}"
        bpm = self.music.bpm

        def done(res):
            notes, q = res
            if notes and q > 0.12 and self.tunes.learn(key, title, notes, q, bpm):
                log.info("Learned a tune (%d notes, quality %.2f)", len(notes), q)
        run_async(lambda: extract_melody(pcm, 16000), done, lambda m: log.info("tune failed: %s", m))

    def _comment_on_music(self, changed: bool = False) -> None:
        if not self.music.playing or self.emotion.state in (SLEEPING, HURT):
            return
        title = (now_playing() or "") if self.cfg.can("titles") else ""
        now = time.monotonic()
        if title and title == self._last_song:
            return
        if not title and now - self._last_song_t < 600:
            return
        self._last_song, self._last_song_t = title, now
        self.memory.event("music", f"We listened to {title or 'some music'} together.")
        user = self.cfg.user_name or "they"
        if self.cfg.effective_api_key() and not self.brain.busy:
            clip = self.music.clip_wav(8) if self.cfg.can("music_audio") and self.cfg.can("cloud") else None
            what = f"'{title}'" if title else "some music"
            hear = " A short clip of it is attached so you can hear it." if clip else ""
            bpm = f" It's around {self.music.bpm:.0f} BPM." if self.music.bpm else ""
            self.brain.ask(f"(Music: {user} {'switched to' if changed else 'is playing'} {what} and you're listening "
                           f"together.{bpm}{hear} Think about what this song is about and how it makes you feel, then "
                           "share a short, genuine reaction in your own words (never quote lyrics). After the emotion "
                           "tag add [mood:NAME] with NAME one of energetic, happy, calm, sad, intense.)",
                           audio=clip, event=True)
        else:
            self._quip("music_start", "happy")

    # ----------------------------------------------------------------- life loop (1 Hz)
    def _life_tick(self) -> None:
        self._life_n += 1
        self.emotion.tick(1.0)
        self.character.set_mood(self.emotion.mood_label())
        st = self.emotion.state
        idle = vision.user_idle_seconds()
        if st in (AWAKE, LISTENING) and not self.chat.isVisible() and not self.speaker.speaking:
            night = datetime.now().hour < 6
            limit = self.cfg.sleep_after_min * 60 * (0.4 if night else 1.0)
            since_her = time.monotonic() - self.character.last_interaction
            away = self._away_since and time.monotonic() - self._away_since > 300
            if limit > 0 and ((idle > limit and since_her > limit) or away) and not self.music.playing:
                self._quip("fall_asleep", "sleepy")
                QTimer.singleShot(2000, self.emotion.sleep)
        if self._life_n % 15 == 0 and self.music.playing:
            if self.cfg.can("titles") and (now_playing() or "") != self._last_song:
                self._comment_on_music(changed=True)
        if self._life_n % 30 == 0:
            self._maybe_initiate(idle)
        if self._life_n % 5 == 0:
            self._care_tick(idle)
        self._maybe_learn_tune()
        if self.music.playing and time.monotonic() - self._last_mood_t > 40 and self._song_mood:
            self._last_mood_t = time.monotonic()
            self._feel_the_song()
        if self._earworm_at and time.monotonic() > self._earworm_at and not self.music.playing:
            self._earworm_at = 0.0
            self._hum_learned(earworm=True)
        self._maybe_sing()
        if self._life_n % 300 == 0 and idle < 120:      # learn when you're usually at the PC
            self.memory.bump(f"active_h{datetime.now().hour}")
        ignored = time.monotonic() - max(self.character.last_interaction, self.last_exchange)
        self.attention.tick(idle, ignored)

    def _maybe_initiate(self, idle: float) -> None:
        mins = self.cfg.initiative_minutes
        if mins <= 0 or not self.cfg.effective_api_key() or self.brain.busy:
            return
        if self.emotion.state not in (AWAKE, HAPPY, LISTENING) or self.speaker.speaking or self.mic.recording:
            return
        present = idle < 90 or (self.camera.running and self.camera.present)
        if not present or time.monotonic() - self.last_exchange < mins * 60:
            return
        if random.random() > 0.5:
            return
        self.last_exchange = time.monotonic()
        user = self.cfg.user_name or "them"
        self.brain.ask(f"(Initiative: you haven't talked with {user} for a while and they're at the PC. Start a "
                       "short conversation: ask them something you don't know about them yet, follow up on something "
                       "you remember, or share a thought. One or two sentences.)", event=True)

    # ----------------------------------------------------------------- senses on/off
    def _apply_senses(self) -> None:
        can = self.cfg.can
        self.memory.private = not can("memory")
        self.fg.enabled = can("titles")
        if not self.fg.enabled:
            self.fg.title = ""
        if can("webcam") and not self.camera.running:
            self.camera.index = self.cfg.webcam_index
            self.camera.start()
        elif not can("webcam") and self.camera.running:
            self.camera.stop()
        if can("music") and not self.music.running:
            self.music.start()
        elif not can("music") and self.music.running:
            self.music.stop()
        if not can("mic") and self._wake_listening:
            self._stop_wake_listening()
        self.vision_timer.stop()
        if self.cfg.vision_auto and can("screen"):
            self.vision_timer.start(int(max(1.0, self.cfg.vision_interval_min) * 60_000))
        self._update_status()

    def _status(self) -> str:
        lock = "🔒 " if self.cfg.privacy_mode or self.cfg.local_only else ""
        icons = lock + ({SLEEPING: "💤", HURT: "😢", HAPPY: "✨", LISTENING: "🎵"}.get(self.emotion.state, "") or \
            {"shy": "☺", "flustered": "😳", "playful": "😜", "sleepy": "🥱"}.get(self.emotion.mood_label(), ""))
        cam = " 📷" if self.camera.running else ""
        return f"{icons} ♥{self.emotion.affection:.0f}{cam}".strip()

    def _update_status(self) -> None:
        self.chat.set_status(self._status())
        if hasattr(self, "tray"):
            self.tray.setToolTip(f"{self.cfg.name} — {self.emotion.state}, mood {self.emotion.mood:.0f}, "
                                 f"affection {self.emotion.affection:.0f}"
                                 + (" — webcam ON" if self.camera.running else ""))

    # ----------------------------------------------------------------- windows
    def _follow(self) -> None:
        self.bubble.follow(self.character.head_anchor())
        self.chat.dock(self.character.global_rect())

    def toggle_chat(self) -> None:
        self.attention.engaged()
        if self.chat.isVisible():
            self.chat.hide()
        else:
            if self.emotion.state == SLEEPING:
                self.emotion.wake(gentle=True)
                self._quip("wake_gentle", "happy")
            self.character.set_busy(True)
            self.chat.open_near(self.character.global_rect())
            self._update_status()

    def open_settings(self) -> None:
        if getattr(self, "_settings", None) and self._settings.isVisible():
            self._settings.raise_()
            return
        dlg = SettingsDialog(self.cfg, self.memory)
        dlg.test_voice.connect(lambda: self.say(f"Hi, I'm {self.cfg.name}! Do I sound cute?", "happy"))
        dlg.clear_memory.connect(lambda: (self.brain.clear_memory(),
                                          self.chat.add_message("", "All memories erased.", "system")))
        dlg.accepted.connect(self._settings_saved)
        self._settings = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _settings_saved(self) -> None:
        self.chat.set_name(self.cfg.name)
        self.character.apply_config()
        self._apply_senses()
        self.tray.setIcon(_app_icon())
        self.say("Settings saved!", "happy", speak=False)

    def _first_run(self) -> None:
        self.say(f"Hi, I'm {self.cfg.name}! I need a Gemini API key to think. Let's set it up!", "excited")
        QTimer.singleShot(1200, self.open_settings)

    # ----------------------------------------------------------------- menus / tray
    def _menu(self) -> QMenu:
        m = QMenu()
        m.addAction("💬 Chat", self.toggle_chat)
        m.addAction("🎤 Talk", self.toggle_mic)
        m.addAction("👁 Look at my screen", lambda: self.look_at_screen("What do you think of what I'm doing?"))
        m.addAction("📷 Look at me (webcam)", lambda: self.look_at_me("Look at me! What do you see?"))
        if self.emotion.state == SLEEPING:
            m.addAction("☀ Wake up", lambda: (self.emotion.wake(True), self._quip("wake_gentle", "happy")))
        else:
            m.addAction("💤 Go to sleep", lambda: (self._quip("fall_asleep", "sleepy"),
                                                   QTimer.singleShot(1500, self.emotion.sleep)))
        pose = m.addMenu("🧸 Pose & cute")
        c = self.character
        sitting = c.user_pose == "sit"
        pose.addAction("🧍 Stand up" if sitting else "🪑 Sit down",
                       (lambda: (c.stand_up(), self._quip("stand_up", "happy"))) if sitting else
                       (lambda: (c.sit_down(), self._quip("sit_down", "happy"))))
        pose.addAction("⬆ Jump!", lambda: c.do_cute("jump"))
        pose.addAction("🐰 Hop hop", lambda: c.do_cute("hop_hop"))
        pose.addAction("💃 Twirl", lambda: c.do_cute("twirl"))
        pose.addAction("🐱 Nya~ (cat paws)", lambda: c.do_cute("nya"))
        pose.addAction("💖 Heart", lambda: c.do_cute("heart"))
        pose.addAction("💋 Blow a kiss", lambda: c.do_cute("kiss"))
        pose.addAction("👋 Wave", lambda: c.do_cute("wave"))
        held = pose.addMenu("🧘 Hold a pose")
        for nm, lbl in (("lie", "🛌 Lie down"), ("kneel", "🙇 Kneel (seiza)"), ("squat", "Squat"), ("bow", "🙇 Bow"),
                        ("hands_up", "🙌 Hands up!"), ("salute", "🫡 Salute"), ("peace", "✌ Peace sign"),
                        ("think", "🤔 Thinking"), ("arms_crossed", "Arms crossed"), ("hands_on_hips", "Hands on hips"),
                        ("hug_self", "Hug herself"), ("shy", "Shy pose"), ("dance", "💃 Dance"), ("t_pose", "T-pose")):
            held.addAction(lbl, lambda n=nm: self.do_command(n))
        held.addSeparator()
        held.addAction("🧍 Stand normally", lambda: self.do_command("stand"))
        pose.addSeparator()
        pose.addAction("↻ Turn around", lambda: (c.rotate_by(180), self._quip("turned", "surprised")))
        pose.addAction("↺ Turn a little", lambda: c.rotate_by(45))
        pose.addAction("🙂 Face me", c.face_me)
        pt = m.addMenu("🐾 Pets & toys")
        out = self.play.kinds() if hasattr(self, "play") else []
        for kinds in (PETS, TOYS):
            for k in kinds:
                act = QAction(COMPANION_LABELS[k], pt, checkable=True, checked=k in out)
                act.triggered.connect(lambda _=False, kk=k: self.play.toggle(kk))
                pt.addAction(act)
            pt.addSeparator()
        pt.addSeparator()
        do = pt.addMenu("✨ Ask her to…")
        has_pet = any(k in out for k in PETS)
        for nm, lbl, ok in (("pet", "🤲 Pet her pet", has_pet), ("cuddle", "🤗 Cuddle her pet", has_pet),
                            ("treat", "🍪 Give her pet a treat", has_pet), ("throw", "⚾ Play fetch", "ball" in out),
                            ("yarn", "🧶 Play with the kitten", "yarn" in out and "cat" in out),
                            ("party", "🎉 Dance party with the pets", has_pet), ("read", "📖 Read a book", True),
                            ("tea", "🍵 Have some tea", True), ("exercise", "🤸 Exercise", True),
                            ("plant", "🌱 Plant a flower", len(self.play.plants()) < 8),
                            ("trees", "🌳 Plant a row of trees (one by one)", len(self.play.plants()) < 8),
                            ("water", "💧 Water the flowers", bool(self.play.plants()))):
            act = do.addAction(lbl, lambda n=nm: (self.play.plant_trees(3) if n == "trees" else
                                                  self.play.start_activity(n, species="flower" if n == "plant" else ""))
                               or self.say("Hang on, I can't right now~", "surprised", speak=False))
            act.setEnabled(ok)
        if "teddy" in out:
            pt.addAction("🧸 Give her the teddy", lambda: self.play._hug(self.play.get("teddy")))
        if self.play.plants():
            pt.addAction(f"🥀 Remove her flowers ({len(self.play.plants())})… she'll be upset", self.play.remove_plants)
        if out:
            pt.addAction("Put them all away", self.play.clear)
        chars = m.addMenu("👗 Character")
        cur_path = (self.cfg.vrm_path or default_vrm()).lower()
        for label, style, path in list_characters():
            active = style == self.cfg.builtin_style and (style != "vrm" or path.lower() == cur_path)
            act = QAction(label, chars, checkable=True, checked=active)
            act.triggered.connect(lambda _=False, s=style, p=path: self.choose_character(s, p))
            chars.addAction(act)
        sing = m.addMenu("🎵 Sing & voice")
        sing.addAction("🎤 Sing a song", lambda: self.sing("song"))
        sing.addAction("🎶 Hum a tune", lambda: self.sing("hum"))
        if self.tunes.tunes:
            sing.addAction(f"🎧 Hum a song you heard ({len(self.tunes.tunes)} learned)",
                           lambda: self._hum_learned() or self.say("Hang on~", "thinking", speak=False))
        lt = QAction("Learn the tunes of my music", sing, checkable=True, checked=self.cfg.learn_tunes)
        lt.toggled.connect(lambda on: self._toggle("learn_tunes", on))
        sing.addAction(lt)
        if self.singer.singing:
            sing.addAction("⏹ Stop singing", self.singer.stop)
        act = QAction("Sing and hum on her own", sing, checkable=True, checked=self.cfg.sing_randomly)
        act.toggled.connect(lambda on: self._toggle("sing_randomly", on))
        sing.addAction(act)
        sing.addSeparator()
        for key, (lbl, *_rest) in VOICE_PRESETS.items():
            a = QAction("🎀 " + lbl, sing, checkable=True, checked=self.cfg.voice_preset == key)
            a.triggered.connect(lambda _=False, k=key: self.set_voice(k))
            sing.addAction(a)
        care = m.addMenu("🩺 Care & reminders")
        care.addAction("⏰ Add a reminder…", self.add_reminder_dialog)
        for r in sorted(self.cfg.reminders, key=lambda r: r["at"])[:10]:
            sub = care.addMenu(f"   {describe_time(r['at'])}: {r['text'][:40]}" + (" (daily)" if r.get("repeat") else ""))
            sub.addAction("✕ Delete", lambda rr=r: (self.cfg.reminders.remove(rr), self.cfg.save()))
        care.addSeparator()
        care.addAction("☕ Tea break with her", self.tea_break)
        for label, attr in (("Remind me to take breaks, drink water, rest my eyes", "health_reminders"),
                            ("Watch me work (coding / heavy work) — program names only", "watch_work")):
            a = QAction(label, care, checkable=True, checked=getattr(self.cfg, attr))
            a.toggled.connect(lambda on, at=attr: self._toggle(at, on))
            care.addAction(a)
        priv = m.addMenu("🔒 Privacy")
        lock = QAction("🔒 Privacy mode: she won't look at or listen to anything", priv, checkable=True,
                       checked=self.cfg.privacy_mode)
        lock.toggled.connect(self.set_privacy)
        priv.addAction(lock)
        mp = QAction("Privacy mode still lets her hear my music (stays on this PC)", priv, checkable=True,
                     checked=self.cfg.music_in_privacy)
        mp.toggled.connect(lambda on: (self._toggle("music_in_privacy", on), on and self._toggle("music_enabled", True)))
        priv.addAction(mp)
        priv.addSeparator()
        for label, attr in (("Look at my screen (screenshots)", "allow_screen"),
                            ("Read window titles (apps, song names)", "allow_window_titles"),
                            ("Glance at my screen by herself", "vision_auto"),
                            ("Webcam", "webcam_enabled"), ("Listen for her name while asleep (mic)", "wake_by_voice"),
                            ("Listen to the music my PC plays", "music_enabled"),
                            ("Send music clips to Gemini", "music_send_audio"),
                            ("Remember our chats and learn about me", "remember_chats"),
                            ("Local AI only: send nothing to the cloud", "local_only")):
            a = QAction(label, priv, checkable=True, checked=getattr(self.cfg, attr))
            a.setEnabled(not (self.cfg.privacy_mode and attr not in ("remember_chats", "local_only")))
            a.toggled.connect(lambda on, at=attr: self._toggle(at, on))
            priv.addAction(a)
        priv.addSeparator()
        priv.addAction("🧹 Forget everything about me", lambda: (self.brain.clear_memory(), self.tunes.forget(),
                       self.say("...Okay. Everything's forgotten. Nice to meet you~?", "shy", speak=False)))
        m.addSeparator()
        for label, attr in (("Voice replies", "tts_enabled"), ("Let her see me (webcam)", "webcam_enabled"),
                            ("Listen to music with me", "music_enabled"),
                            ("Glance at screen periodically", "vision_auto"),
                            ("Wake her by voice while asleep", "wake_by_voice")):
            act = QAction(label, m, checkable=True, checked=getattr(self.cfg, attr))
            act.toggled.connect(lambda on, a=attr: self._toggle(a, on))
            m.addAction(act)
        m.addSeparator()
        m.addAction("⚙ Settings", self.open_settings)
        m.addAction("Reset position", self.character.reset_position)
        m.addAction("Quit", self.quit)
        return m

    def choose_character(self, style: str, path: str) -> None:
        self.cfg.builtin_style = style
        if path:
            self.cfg.vrm_path = path
        self.cfg.save()
        self.character.apply_config()
        self.tray.setIcon(_app_icon())
        self._follow()
        self.say("Ta-da! How do I look?", "happy")

    def _toggle(self, attr: str, on: bool) -> None:
        setattr(self.cfg, attr, on)
        if attr == "tts_enabled" and not on:
            self.speaker.stop()
        self.cfg.save()
        self._apply_senses()
        if attr == "webcam_enabled":
            self.say("Hi! I can see you now~" if on else "Okay, eyes closed!", "happy" if on else "neutral")

    def _show_menu(self, pos: QPoint) -> None:
        self._ctx_menu = self._menu()
        self._ctx_menu.popup(pos)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_app_icon(), self)
        self._tray_menu = QMenu()
        self._tray_menu.aboutToShow.connect(self._refresh_tray_menu)
        self.tray.setContextMenu(self._tray_menu)
        self.tray.activated.connect(
            lambda r: self.toggle_chat() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self._update_status()

    def _refresh_tray_menu(self) -> None:
        fresh = self._menu()
        self._tray_fresh = fresh   # keep submenus (Character) alive
        self._tray_menu.clear()
        for a in fresh.actions():
            a.setParent(self._tray_menu)
            self._tray_menu.addAction(a)

    def quit(self) -> None:
        self.speaker.stop()
        self._stop_wake_listening()
        if self.mic.recording:
            self.mic.stop()
        self.camera.stop()
        self.music.stop()
        self.memory.set("mood", round(self.emotion.mood, 2))
        self.memory.set("affection", round(self.emotion.affection, 2))
        self.cfg.save()
        self.tray.hide()
        self.qapp.quit()


def _app_icon() -> QIcon:
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
    return QIcon(str(p)) if p.exists() else QIcon()


def run(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("DesktopWaifu")
    app.setWindowIcon(_app_icon())
    try:                                       # own taskbar identity (so Windows shows her icon, not Python's)
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeshanNawod.DesktopWaifu")
    except Exception:
        pass
    app.setQuitOnLastWindowClosed(False)
    w = WaifuApp(app)  # noqa: F841  (keep a reference)
    return app.exec()
