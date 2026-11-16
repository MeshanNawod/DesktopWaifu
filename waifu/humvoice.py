"""A real human hum: her own (neural TTS) voice re-pitched onto a melody.

1. Once per voice, her voice says a long "Mmmmm" and "Aaaah" (edge-tts). The MP3 is decoded with Qt and
   the steady part is cached as raw audio.
2. To hum a tune, the voice's individual vocal-fold cycles are re-spaced to each note's pitch with
   TD-PSOLA (pitch-synchronous overlap-add). That changes the pitch but keeps the throat / mouth
   resonances, so it still sounds like *her* humming, not an instrument.
"""
from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

from .config import CACHE_DIR
from .workers import run_async

log = logging.getLogger(__name__)
SR = 24000
TEXTS = {"m": "Mmmmmmmmmmmm.", "a": "Aaaaaaaaaaah."}


def _key(voice: str, pitch: str) -> str:
    return hashlib.md5(f"{voice}|{pitch}".encode()).hexdigest()[:10]


def sample_path(voice: str, pitch: str, syl: str) -> Path:
    return CACHE_DIR / f"humvoice_{_key(voice, pitch)}_{syl}.npy"


def load_sample(voice: str, pitch: str, syl: str) -> np.ndarray | None:
    p = sample_path(voice, pitch, syl)
    if p.exists():
        try:
            return np.load(p)
        except Exception:
            return None
    return None


class HumVoice(QObject):
    """Records + caches her voice samples (needs the online voice once per voice preset)."""
    ready = Signal()

    def __init__(self):
        super().__init__()
        self._decoders: list[QAudioDecoder] = []

    def prepare(self, voice: str, pitch: str) -> None:
        missing = [s for s in TEXTS if not sample_path(voice, pitch, s).exists()]
        if not missing:
            return
        from .voice import synth_edge

        def work():
            return [(s, synth_edge(TEXTS[s], voice, "-25%", pitch).path) for s in missing]

        run_async(work, lambda files: [self._decode(voice, pitch, s, f) for s, f in files],
                  lambda m: log.info("Hum voice not available (%s); using the built-in hum", m))

    def _decode(self, voice: str, pitch: str, syl: str, mp3: Path) -> None:
        dec = QAudioDecoder(self)
        fmt = QAudioFormat()
        fmt.setSampleRate(SR)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        dec.setAudioFormat(fmt)
        dec.setSource(QUrl.fromLocalFile(str(mp3)))
        chunks: list[np.ndarray] = []

        def got():
            buf = dec.read()
            chunks.append(np.frombuffer(bytes(buf.constData()), dtype=np.int16).copy())

        def done():
            try:
                pcm = np.concatenate(chunks).astype(np.float32) / 32768.0 if chunks else None
                steady = _steady_part(pcm) if pcm is not None else None
                if steady is not None:
                    np.save(sample_path(voice, pitch, syl), steady)
                    log.info("Hum voice '%s' ready (%.2f s)", syl, len(steady) / SR)
                    self.ready.emit()
            finally:
                self._decoders.remove(dec)
                dec.deleteLater()
                try:
                    mp3.unlink(missing_ok=True)
                except OSError:
                    pass

        dec.bufferReady.connect(got)
        dec.finished.connect(done)
        dec.error.connect(lambda *_: done())
        self._decoders.append(dec)
        dec.start()


def _f0(seg: np.ndarray) -> tuple[float, float]:
    seg = seg - seg.mean()
    ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
    lo, hi = SR // 650, SR // 110
    k = lo + int(np.argmax(ac[lo:hi]))
    return SR / k, float(ac[k] / (ac[0] + 1e-9))


def _steady_part(x: np.ndarray) -> np.ndarray | None:
    """The clearly voiced, steady middle of the recording."""
    fr = 480
    good = []
    for i in range(0, len(x) - 1200, fr):
        f, clarity = _f0(x[i:i + 1200])
        good.append(clarity > 0.8 and 150 < f < 650 and np.sqrt(np.mean(x[i:i + 1200] ** 2)) > 0.02)
    best, cur, start = (0, 0), 0, 0
    for i, g in enumerate(good):
        if g:
            if cur == 0:
                start = i
            cur += 1
            if cur > best[1] - best[0]:
                best = (start, start + cur)
        else:
            cur = 0
    a, b = best
    if (b - a) * fr < SR * 0.35:
        return None
    seg = x[a * fr + fr: b * fr]            # trim the edges a little
    return seg / (np.abs(seg).max() + 1e-9)


def _pitch_marks(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Glottal pulse positions (one per vocal-fold cycle) and the local period at each."""
    marks, periods = [], []
    f, _ = _f0(x[:1200])
    p = SR / f
    i = int(np.argmax(np.abs(x[: int(p)])))
    while i + 2 * p < len(x):
        f, c = _f0(x[i:i + 1200]) if i + 1200 < len(x) else (SR / p, 1.0)
        if c > 0.5:
            p = 0.8 * p + 0.2 * (SR / f)
        marks.append(i)
        periods.append(p)
        lo, hi = int(i + p * 0.85), int(i + p * 1.15)
        if hi >= len(x):
            break
        i = lo + int(np.argmax(np.abs(x[lo:hi])))
    return np.array(marks), np.array(periods)


def psola_sing(sample, melody: list[tuple[float, float]], syllable: str = "m",
               seed: int = 0) -> np.ndarray:
    """A recorded voice re-pitched to sing / hum `melody` [(midi, seconds)].
    sample: a steady recording (np.ndarray), or a pool [(f0, grain)] from reference_grains()."""
    rng = np.random.default_rng(seed)
    if isinstance(sample, list):
        pool = sample
    else:
        marks, periods = _pitch_marks(sample)
        pool = []
        for m, p in zip(marks, periods):
            h = int(p)
            if m - h >= 0 and m + h < len(sample):
                pool.append((SR / p, sample[m - h: m + h] * np.hanning(2 * h)))
    if len(pool) < 8:
        raise ValueError("not enough voice cycles")
    pool_f = np.array([f_ for f_, _ in pool])
    grains = [g for _, g in pool]
    total = sum(d for _, d in melody) + 0.3
    n = int(total * SR)
    # target pitch + loudness curves
    f = np.zeros(n)
    amp = np.zeros(n)
    start = np.zeros(n)
    t0 = 0.0
    for idx, (midi, dur) in enumerate(melody):
        a, b = int(t0 * SR), min(n, int((t0 + dur) * SR))
        f[a:b] = 440.0 * 2 ** ((midi - 69) / 12)
        start[a:b] = t0
        tt = np.arange(b - a) / SR
        last = idx == len(melody) - 1
        env = np.minimum(1.0, tt / 0.05) * np.clip((dur - tt) / (0.18 if last else 0.05), 0, 1)
        floor = 0.45 if syllable == "m" else 0.15            # "mm-mm" vs "la la": how much it dips between notes
        amp[a:b] = floor + (1 - floor) * env if not last else env
        t0 += dur
    f[f == 0] = f[f > 0][-1] if np.any(f > 0) else 330
    lf = np.log(f)
    k = int(0.05 * SR)
    lf = np.convolve(np.pad(lf, (k, k), mode="edge"), np.ones(k) / k, mode="same")[k:-k]       # legato glides
    t = np.arange(n) / SR
    since = t - start
    vib = 0.009 * np.clip((since - 0.25) / 0.3, 0, 1) * np.sin(2 * np.pi * 5.4 * t + rng.uniform(0, 6))
    freq = np.exp(lf) * (1 + vib)
    out = np.zeros(n + 4096)
    pos, gi, step = 0.0, 0, 1
    cur_note = -1
    while pos < n:
        i = int(pos)
        note = int(start[i] * 1000)
        if note != cur_note:                                  # new note: jump to cycles sung near this pitch
            cur_note = note
            near = np.argsort(np.abs(np.log(pool_f / freq[i])))[:max(8, len(pool_f) // 12)]
            gi = int(rng.choice(near))
        g = grains[gi]
        a = i - len(g) // 2
        lo = max(0, -a)
        out[max(0, a): a + len(g)] += g[lo:] * amp[i]
        pos += SR / freq[i]
        gi += step                                            # ping-pong through the steady voice cycles
        if gi >= len(grains) - 1 or gi <= 0:
            step = -step
            gi = max(0, min(len(grains) - 1, gi))
    out = out[:n]
    for d, gain in ((0.021, 0.10), (0.037, 0.06)):          # a whisper of room
        s = int(d * SR)
        out[s:] += out[:-s] * gain
    out *= np.clip(t / 0.02, 0, 1)
    return (out / (np.abs(out).max() + 1e-9) * 0.62).astype(np.float32)


# ------------------------------------------------------------------ a real person humming (your recording)
REF_DIR = CACHE_DIR.parent / "voice"          # %APPDATA%/DesktopWaifu/voice/hum_reference.(mp3|wav)


def reference_file() -> Path | None:
    for ext in ("wav", "mp3", "m4a", "ogg"):
        p = REF_DIR / f"hum_reference.{ext}"
        if p.exists():
            return p
    return None


def _ref_cache() -> Path:
    return CACHE_DIR / "humref.npy"


def load_reference() -> np.ndarray | None:
    p = _ref_cache()
    src = reference_file()
    if src is None or not p.exists() or p.stat().st_mtime < src.stat().st_mtime:
        return None
    try:
        return np.load(p)
    except Exception:
        return None


def prepare_reference(owner: QObject) -> None:
    """Decode the hum recording once (Qt decoder) into a cached numpy array."""
    src = reference_file()
    if src is None or load_reference() is not None:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dec = QAudioDecoder(owner)
    fmt = QAudioFormat()
    fmt.setSampleRate(SR)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    dec.setAudioFormat(fmt)
    dec.setSource(QUrl.fromLocalFile(str(src)))
    chunks: list[np.ndarray] = []
    dec.bufferReady.connect(lambda: chunks.append(np.frombuffer(bytes(dec.read().constData()), dtype=np.int16).copy()))

    def done(*_):
        if chunks:
            x = np.concatenate(chunks).astype(np.float32) / 32768.0
            np.save(_ref_cache(), x / (np.abs(x).max() + 1e-9))
            log.info("Hum recording ready (%.1f s)", len(x) / SR)
        dec.deleteLater()

    dec.finished.connect(done)
    dec.error.connect(done)
    owner._ref_decoder = dec
    dec.start()


def voiced_runs(x: np.ndarray, min_len: float = 0.25) -> list[tuple[int, int]]:
    """Sample ranges where the recording is clearly hummed (not breaths or pauses)."""
    fr = 480
    flags = []
    for i in range(0, len(x) - 1200, fr):
        f, c = _f0(x[i:i + 1200])
        flags.append(c > 0.72 and 120 < f < 700 and np.sqrt(np.mean(x[i:i + 1200] ** 2)) > 0.03)
    runs, start = [], None
    for i, v in enumerate(flags + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if (i - start) * fr >= min_len * SR:
                runs.append((start * fr, i * fr + 600))
            start = None
    return runs


def natural_hum(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A stretch of the real hum, a touch higher or lower each time so it never sounds identical."""
    runs = voiced_runs(x)
    if runs:
        i = int(rng.integers(0, len(runs)))
        j = min(len(runs), i + int(rng.integers(2, 5)))
        a, b = max(0, runs[i][0] - 2400), min(len(x), runs[j - 1][1] + 4800)
        seg = x[a:b]
    else:
        seg = x
    k = 2 ** (rng.uniform(-1.0, 1.5) / 12)            # -1 .. +1.5 semitones (tempo moves with it, like a person)
    out = np.interp(np.arange(0, len(seg) - 1, k), np.arange(len(seg)), seg)
    fade = int(0.05 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return (out / (np.abs(out).max() + 1e-9) * 0.6).astype(np.float32)


def reference_grains(x: np.ndarray) -> list:
    """(f0, grain) for every vocal-fold cycle in the hummed parts of the recording."""
    out = []
    for a, b in voiced_runs(x):
        seg = x[a:b]
        try:
            marks, periods = _pitch_marks(seg)
        except Exception:
            continue
        for m, p in zip(marks, periods):
            h = int(p)
            if m - h >= 0 and m + h < len(seg):
                out.append((SR / p, seg[m - h: m + h] * np.hanning(2 * h)))
    return out
