"""She hums and sings to herself.

* Humming / "la la la" is synthesised right here: a random cute pentatonic melody in a high, anime-girl
  register, sung by a tiny additive voice synth (harmonics shaped by vowel formants, legato glides,
  delayed vibrato and a little breath). It works offline and never repeats exactly.
* Sung lines are short original lyrics spoken by the TTS voice, each line on a different pitch so it
  sounds sing-song, slotted between the hummed phrases.

Everything plays through its own media player and drives her mouth for lip-sync.
"""
from __future__ import annotations

import io
import logging
import math
import random
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .config import CACHE_DIR, Config
from .workers import run_async

log = logging.getLogger(__name__)
SR = 24000

# anime-girl voice presets for edge-tts: (voice, rate, pitch)
VOICE_PRESETS = {
    "anime_cute": ("Anime girl: cute (English)", "en-US-AnaNeural", "+8%", "+25Hz"),
    "anime_jp": ("Anime girl: Japanese accent, high (Nanami)", "ja-JP-NanamiNeural", "+6%", "+32Hz"),
    "anime_nanami": ("Anime girl: soft Japanese (Nanami)", "ja-JP-NanamiNeural", "+2%", "+16Hz"),
    "imouto": ("Anime girl: little sister (Maisie)", "en-GB-MaisieNeural", "+6%", "+22Hz"),
    "anime_genki": ("Anime girl: genki / energetic", "en-US-AnaNeural", "+15%", "+38Hz"),
    "soft": ("Soft and gentle", "en-US-AvaNeural", "+0%", "+18Hz"),
}

# original little songs (lyrics written for her)
SONGS = [
    ["Sunshine on the taskbar, I'm dancing next to you~", "Click, click goes your mouse, and my heart goes doki doki~"],
    ["Don't forget your water, my hero~", "Stretch your arms up high, and smile for me~"],
    ["La la, today's a happy day~", "Let's play all day long, just you and me~"],
    ["When the stars come out on your screen~", "I'll hum a little lullaby, just for you~"],
    ["Mochi's fast asleep, purring like a song~", "And I'll keep you company, all night long~"],
    ["Even when you're busy, I'm right here~", "Humming just for you, so you won't be lonely~"],
    ["Pudding, cookies, strawberry cake~", "Let's have a snack, for goodness' sake~"],
    ["One, two, three, little steps on the bar~", "Wherever you go, I'll follow you that far~"],
]
HUMS = ["Hmm hmm hmm~ ♪", "Hmm~ hmm hm hmm~ ♪", "Fu fu fuun~ ♪", "La la la~ ♪", "Lu lu lu~ ♪", "Na na na~ ♪"]

# vocal-tract resonances: (freq Hz, bandwidth Hz, gain).  Negative gain = an anti-resonance (nasal zero).
TRACT = {
    "m": [(260, 70, 1.0), (750, 140, -0.75), (1050, 220, 0.25), (2250, 320, 0.12), (2900, 400, 0.06)],  # lips closed: nasal murmur
    "n": [(280, 80, 1.0), (1400, 260, -0.6), (1800, 250, 0.3), (2600, 330, 0.14)],
    "a": [(800, 90, 1.0), (1250, 110, 0.75), (2850, 170, 0.32), (3700, 250, 0.15)],                     # "la"
    "u": [(360, 75, 1.0), (840, 95, 0.55), (2600, 170, 0.14), (3500, 250, 0.07)],                       # "lu"
}


def make_melody(rng: random.Random, notes: int = 10) -> list[tuple[float, float]]:
    """[(midi, seconds)] -- a cute pentatonic phrase that ends home, in a normal girl's humming range."""
    root = rng.choice([62, 64, 65, 67])                 # D4 .. G4 (~290-390 Hz)
    scale = [0, 2, 4, 7, 9, 12, 14]
    beat = 60.0 / rng.uniform(100, 126)
    rhythms = [[1, 0.5, 0.5, 1, 1], [0.5, 0.5, 1, 0.5, 0.5, 1], [1.5, 0.5, 1, 1], [0.5, 0.5, 0.5, 0.5, 2]]
    out, i = [], rng.randrange(0, 4)
    while len(out) < notes - 1:
        for r in rng.choice(rhythms):
            i = max(0, min(len(scale) - 1, i + rng.choice([-2, -1, -1, 1, 1, 2, 0])))
            out.append((root + scale[i], r * beat))
            if len(out) >= notes - 1:
                break
    out.append((root + rng.choice([0, 4, 7]), 2.0 * beat))   # long final note
    return out


def _smooth_noise(rng, n: int, every: int) -> np.ndarray:
    """Slowly wandering noise in -1..1 (for jitter / shimmer)."""
    pts = rng.uniform(-1, 1, n // every + 3)
    return np.interp(np.arange(n) / every, np.arange(len(pts)), pts)


def _tract_response(freqs: np.ndarray, vowel: str) -> np.ndarray:
    h = np.ones_like(freqs)
    for F, bw, g in TRACT.get(vowel, TRACT["m"]):
        r = F * F / np.sqrt((F * F - freqs ** 2) ** 2 + (bw * freqs) ** 2)        # 2nd-order resonance
        if g >= 0:
            h *= 1 + g * (r - 1) * 0.6 if g < 1 else r
        else:                                                                       # nasal zero: a dip
            h /= 1 + (-g) * (r - 1) * 0.6
    if vowel in ("m", "n"):
        h /= 1 + (freqs / 1100.0) ** 2           # closed lips: high frequencies are muffled
    return h


def synth_phrase(melody: list[tuple[float, float]], vowel: str = "m", seed: int = 0) -> np.ndarray:
    """A small human-like voice humming / singing the melody on one syllable (source-filter model).

    Source: glottal pulses (Rosenberg shape) with natural pitch jitter, loudness shimmer, delayed vibrato,
    soft legato glides and breath noise. Filter: the vocal-tract resonances of the syllable (lips closed
    for "mm"), applied in the frequency domain. Returns float32 mono at SR."""
    rng = np.random.default_rng(seed)
    total = sum(d for _, d in melody) + 0.35
    n = int(total * SR)
    f = np.zeros(n)
    note_start = np.zeros(n)
    amp = np.zeros(n)
    t0 = 0.0
    for idx, (midi, dur) in enumerate(melody):
        a, b = int(t0 * SR), min(n, int((t0 + dur) * SR))
        f[a:b] = 440.0 * 2 ** ((midi - 69) / 12)
        note_start[a:b] = t0
        tt = np.arange(b - a) / SR
        att = 0.07 if vowel == "m" else 0.045
        last = idx == len(melody) - 1
        rel = min(0.16 if last else 0.06, dur * 0.4)
        env = np.minimum(1.0, tt / att) ** 0.7 * np.clip((dur - tt) / rel, 0, 1) ** 0.8
        if vowel == "m":                          # legato hum: notes run into each other
            env = np.maximum(env, 0.55) if not last else env
        else:                                      # "l" / "n" consonant at each syllable
            env *= 0.45 + 0.55 * np.minimum(1.0, tt / 0.07)
        amp[a:b] = env * (0.85 + 0.15 * (midi - 60) / 14)
        t0 += dur
    f[f == 0] = f[f > 0][-1] if np.any(f > 0) else 330
    # legato glides (~55 ms) in log-frequency, with a little scoop up into each note
    lf = np.log(f)
    k = int(0.055 * SR)
    lf = np.convolve(np.pad(lf, (k, k), mode="edge"), np.ones(k) / k, mode="same")[k:-k]
    t = np.arange(n) / SR
    since = t - note_start
    scoop = -0.012 * np.exp(-since / 0.05)
    vib = 0.008 * np.clip((since - 0.25) / 0.3, 0, 1) * np.sin(2 * np.pi * 5.3 * t + rng.uniform(0, 6))
    jitter = 0.006 * _smooth_noise(rng, n, int(SR * 0.012))
    freq = np.exp(lf) * (1 + vib + jitter + scoop)
    # glottal source: Rosenberg pulse per period, then lip/nose radiation (differentiation)
    ph = np.cumsum(freq) / SR
    p = ph - np.floor(ph)
    tp, tn = 0.42, 0.18
    g = np.where(p < tp, 0.5 * (1 - np.cos(np.pi * p / tp)),
                 np.where(p < tp + tn, np.cos(np.pi * (p - tp) / (2 * tn)), 0.0))
    shimmer = 1 + 0.05 * _smooth_noise(rng, n, int(SR * 0.02))
    src = np.diff(g, prepend=0.0) * shimmer
    breath = rng.standard_normal(n) * (0.10 if vowel == "m" else 0.07) * (0.4 + 0.6 * g)   # breathy, pulsed
    src = src / max(1e-9, np.abs(src).max()) + breath * 0.25
    # vocal tract filter (frequency domain)
    spec = np.fft.rfft(src * amp)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    out = np.fft.irfft(spec * _tract_response(freqs, vowel), n)
    # tiny room reflection so it isn't bone dry
    for d, gain in ((0.023, 0.12), (0.041, 0.08)):
        s_ = int(d * SR)
        out[s_:] += out[:-s_] * gain
    out *= np.clip(t / 0.02, 0, 1)                # no click at the start
    return (out / max(1e-9, float(np.abs(out).max())) * 0.6).astype(np.float32)


def wav_bytes(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


@dataclass
class Segment:
    path: Path
    text: str                      # what the bubble shows
    envelope: list[float] = field(default_factory=list)   # mouth openness per 20 ms (hum / la)
    sung: bool = False             # a TTS line (mouth flaps like singing words)


class Singer(QObject):
    mouth = Signal(float)
    line = Signal(str)             # show this in her speech bubble
    started = Signal()
    finished = Signal()

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._queue: list[Segment] = []
        self._cur: Segment | None = None
        self._gen = 0
        self.singing = False
        self._t0 = 0.0

    # ------------------------------------------------------------------ building a song
    def sing(self, kind: str = "song", user: str = "") -> None:
        """kind: 'hum' (just a hummed tune) or 'song' (hums + sung lines)."""
        self.stop()
        self._gen += 1
        gen = self._gen
        cfg = self.cfg
        rng = random.Random(time.time())

        def hum(vowel: str, notes: int) -> Segment:
            x = synth_phrase(make_melody(rng, notes), vowel, rng.randrange(1 << 30))
            path = CACHE_DIR / f"sing_{uuid.uuid4().hex}.wav"
            path.write_bytes(wav_bytes(x))
            step = int(SR * 0.02)
            env = [float(np.abs(x[i:i + step]).mean()) for i in range(0, len(x), step)]
            peak = max(env) or 1.0
            open_ = 0.25 if vowel == "m" else 0.85
            label = {"m": "Hmm hmm hmm~ ♪", "a": "La la la~ ♪", "u": "Lu lu lu~ ♪", "n": "Na na na~ ♪"}[vowel]
            return Segment(path, label, [open_ * v / peak for v in env])

        def sung(text: str, pitch: int) -> Segment | None:
            from .voice import synth_edge
            if cfg.local_only:              # the singing voice is an online service: hum instead
                return None
            voice, rate = cfg.tts_voice, "-6%"
            try:
                a = synth_edge(text, voice, rate, f"+{pitch}Hz")
                return Segment(a.path, f"♪ {text} ♪", sung=True)
            except Exception as e:
                log.info("Singing voice unavailable (%s); humming instead", e)
                return None

        def work() -> list[Segment]:
            segs = [hum(rng.choice("mmu"), rng.randint(7, 11))]
            if kind == "song":
                lyrics = list(rng.choice(SONGS))
                if user and rng.random() < 0.4:
                    lyrics[-1] = lyrics[-1].replace("you", user, 1)
                base = 25 if "ja-JP" not in cfg.tts_voice else 15
                for i, ln in enumerate(lyrics):
                    s = sung(ln, base + (12 if i % 2 else 0))
                    if s is not None:
                        segs.append(s)
                    segs.append(hum(rng.choice("aun" if i == 0 else "m"), rng.randint(6, 9)))
            else:
                segs.append(hum(rng.choice("mau"), rng.randint(8, 12)))
            return segs

        def ready(segs: list[Segment]) -> None:
            if gen != self._gen:
                for s in segs:
                    self._delete(s.path)
                return
            self._queue = segs
            self.singing = True
            self.started.emit()
            self._next()

        run_async(work, ready, lambda m: log.warning("Singing failed: %s", m))

    # ------------------------------------------------------------------ playback
    def _next(self) -> None:
        if self._cur is not None:
            self._delete(self._cur.path)
            self._cur = None
        if not self._queue:
            self.stop()
            return
        self._cur = self._queue.pop(0)
        self.line.emit(self._cur.text)
        self._audio.setVolume(max(0.0, min(1.0, self.cfg.tts_volume)) * (0.8 if not self._cur.sung else 1.0))
        self._player.setSource(QUrl.fromLocalFile(str(self._cur.path)))
        self._player.play()
        self._t0 = time.monotonic()
        self._timer.start(30)

    def _tick(self) -> None:
        s = self._cur
        if s is None:
            return
        pos = float(self._player.position()) or (time.monotonic() - self._t0) * 1000
        if s.envelope:
            i = int(pos // 20)
            v = s.envelope[i] if i < len(s.envelope) else 0.0
        else:
            v = 0.25 + 0.6 * abs(math.sin(pos / 1000 * math.tau * 2.6))   # slower than talking: sung syllables
        self.mouth.emit(v)

    def _on_status(self, status) -> None:
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            QTimer.singleShot(120, self._next)

    def stop(self) -> None:
        self._gen += 1
        self._timer.stop()
        was = self.singing
        self.singing = False
        self._player.stop()
        self._player.setSource(QUrl())
        for s in ([self._cur] if self._cur else []) + self._queue:
            self._delete(s.path)
        self._cur, self._queue = None, []
        self.mouth.emit(0.0)
        if was:
            self.finished.emit()

    @staticmethod
    def _delete(path: Path) -> None:
        def rm() -> None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        QTimer.singleShot(600, rm)
