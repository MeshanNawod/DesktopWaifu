"""She learns the tunes of the music you play, then hums them later.

Everything here runs locally on the PC: a short clip of what's playing is analysed for its main
melody (no audio or text leaves the computer). The result is just a list of (note, seconds).

Melody extraction, numpy only:
  1. short-time spectrum (128 ms windows, 20 ms hop), whitened so loud bass / timbre doesn't dominate
  2. harmonic summation: for every candidate pitch in the singing range (E3..C6, 1/3-semitone grid)
     add up the energy at its first harmonics -> "salience"; the best candidate per frame is the melody
  3. voicing: frames whose winner isn't clearly above the rest are treated as no-melody
  4. smoothing + note segmentation, octave-jump cleanup, then the clearest 4-8 s phrase is kept
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SR = 16000
FRAME, HOP = 2048, 320
MIDI_LO, MIDI_HI = 52, 84


def _salience(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = 1 + (len(x) - FRAME) // HOP
    if n <= 4:
        return np.zeros((0,)), np.zeros((0,)), np.zeros((0,))
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(FRAME)[None, :]
    mag = np.abs(np.fft.rfft(frames, axis=1))
    # spectral whitening: divide by a smooth envelope across frequency
    k = 31
    env = np.apply_along_axis(lambda r: np.convolve(r, np.ones(k) / k, mode="same"), 1, mag) + 1e-6
    white = np.sqrt(mag / env)
    freqs_bin = SR / FRAME
    cand = np.arange(MIDI_LO * 3, MIDI_HI * 3 + 1) / 3.0
    f0 = 440.0 * 2 ** ((cand - 69) / 12)
    H = 6
    w = 0.84 ** np.arange(H)
    bins = (f0[:, None] * np.arange(1, H + 1)[None, :]) / freqs_bin            # (C, H)
    bins = np.clip(bins, 0, white.shape[1] - 1.001)
    lo = np.floor(bins).astype(int)
    fr = bins - lo
    s = white[:, lo] * (1 - fr) + white[:, lo + 1] * fr                          # (N, C, H)
    sal = (s * w).sum(axis=2)
    best = sal.argmax(axis=1)
    conf = sal.max(axis=1) / (sal.mean(axis=1) + 1e-9)
    band = (np.arange(mag.shape[1]) * freqs_bin > 150) & (np.arange(mag.shape[1]) * freqs_bin < 2500)
    energy = mag[:, band].mean(axis=1)
    return cand[best], conf, energy


def extract_melody(pcm: np.ndarray, sr: int = SR) -> tuple[list[tuple[int, float]], float]:
    """pcm: mono float or int16 audio. Returns ([(midi, seconds)], quality 0..1)."""
    x = pcm.astype(np.float32)
    if x.dtype != np.float32 or np.abs(x).max() > 2:
        x = x / 32768.0
    if sr != SR:
        x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * SR / sr)), np.arange(len(x)), x)
    if len(x) < SR * 2 or float(np.abs(x).max()) < 0.01:
        return [], 0.0
    pitch, conf, energy = _salience(x)
    if pitch.size == 0:
        return [], 0.0
    voiced = (conf > max(1.55, float(np.percentile(conf, 40)))) & (energy > np.percentile(energy, 25))
    midi = np.round(pitch).astype(int)
    # median-smooth the pitch track (5 frames) to kill single-frame jumps
    pad = np.pad(midi, 2, mode="edge")
    midi = np.median(np.stack([pad[i:i + len(midi)] for i in range(5)]), axis=0).astype(int)
    dt = HOP / SR
    notes: list[list[float]] = []          # [midi, start, end, conf]
    for i, (m, v) in enumerate(zip(midi, voiced)):
        t = i * dt
        if not v:
            continue
        if notes and notes[-1][0] == m and t - notes[-1][2] < 0.07:
            notes[-1][2] = t + dt
            notes[-1][3] += conf[i]
        else:
            notes.append([m, t, t + dt, conf[i]])
    notes = [n for n in notes if n[2] - n[1] >= 0.09]
    if len(notes) < 5:
        return [], 0.0
    # fold octave errors toward the median pitch
    med = float(np.median([n[0] for n in notes]))
    for n in notes:
        while n[0] - med > 7:
            n[0] -= 12
        while med - n[0] > 7:
            n[0] += 12
    # merge repeated neighbours that the folding made equal
    merged = [notes[0]]
    for n in notes[1:]:
        if n[0] == merged[-1][0] and n[1] - merged[-1][2] < 0.1:
            merged[-1][2] = n[2]
            merged[-1][3] += n[3]
        else:
            merged.append(n)
    notes = merged
    # pick the clearest phrase of 4..8 seconds
    best, best_score = (0, len(notes)), -1.0
    for a in range(len(notes)):
        for b in range(a + 5, len(notes) + 1):
            span = notes[b - 1][2] - notes[a][1]
            if span > 8.0:
                break
            if span < 3.5:
                continue
            seg = notes[a:b]
            covered = sum(n[2] - n[1] for n in seg) / span
            variety = len({n[0] for n in seg})
            score = covered * min(1.0, variety / 5) * min(1.0, len(seg) / 10)
            if score > best_score:
                best, best_score = (a, b), score
    seg = notes[best[0]:best[1]]
    out: list[tuple[int, float]] = []
    for i, n in enumerate(seg):
        nxt = seg[i + 1][1] if i + 1 < len(seg) else n[2]
        out.append((int(n[0]), float(min(1.2, max(0.12, nxt - n[1])))))
    quality = max(0.0, min(1.0, best_score)) * min(1.0, float(voiced.mean()) * 2)
    return out, quality


# Krumhansl-Kessler key profiles (major / minor)
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
MOODS = ("energetic", "happy", "calm", "sad", "intense")


def analyze_mood(pcm: np.ndarray, bpm: float = 0.0, sr: int = SR) -> dict:
    """What a song *feels* like, from the sound alone: tempo, loudness, brightness and major / minor key.
    Returns {"mood": energetic|happy|calm|sad|intense, "major": bool, "energy": 0..1, "bpm": float}."""
    x = pcm.astype(np.float32)
    if np.abs(x).max() > 2:
        x = x / 32768.0
    if len(x) < sr * 2:
        return {"mood": "happy", "major": True, "energy": 0.5, "bpm": bpm}
    n = 1 + (len(x) - 4096) // 2048
    idx = np.arange(4096)[None, :] + 2048 * np.arange(n)[:, None]
    mag = np.abs(np.fft.rfft(x[idx] * np.hanning(4096)[None, :], axis=1)).mean(axis=0)
    freqs = np.fft.rfftfreq(4096, 1 / sr)
    sel = (freqs > 80) & (freqs < 2000)
    pcs = (np.round(12 * np.log2(freqs[sel] / 440.0)) + 9) % 12            # pitch class of every bin (A = 9)
    chroma = np.bincount(pcs.astype(int), weights=mag[sel], minlength=12)
    chroma = chroma / (chroma.max() + 1e-9)
    best_major = max(np.corrcoef(np.roll(_MAJOR, k), chroma)[0, 1] for k in range(12))
    best_minor = max(np.corrcoef(np.roll(_MINOR, k), chroma)[0, 1] for k in range(12))
    major = best_major >= best_minor - 0.02
    rms = float(np.sqrt(np.mean(x ** 2)))
    energy = float(min(1.0, rms * 6))
    centroid = float((freqs * mag).sum() / (mag.sum() + 1e-9))
    bright = min(1.0, centroid / 2500.0)
    fast = bpm >= 118 if bpm else energy > 0.6
    slow = 0 < bpm < 92 if bpm else energy < 0.3
    if not major and (fast or energy > 0.65) and bright > 0.45:
        mood = "intense"
    elif fast and energy > 0.4:
        mood = "energetic" if major or bright > 0.5 else "intense"
    elif not major and (slow or energy < 0.45):
        mood = "sad"
    elif slow or energy < 0.3:
        mood = "calm"
    else:
        mood = "happy" if major else "calm"
    return {"mood": mood, "major": bool(major), "energy": energy, "bpm": bpm}


def fit_to_voice(notes: list[tuple[int, float]], centre: int = 66) -> list[tuple[int, float]]:
    """Move the tune into her comfortable humming range (keeps the melody's shape)."""
    if not notes:
        return notes
    med = float(np.median([m for m, _ in notes]))
    shift = int(round((centre - med) / 12.0)) * 12
    if abs(med + shift - centre) > 4:
        shift += int(round(centre - (med + shift)))
    return [(m + shift, d) for m, d in notes]


class TuneMemory:
    """The tunes she picked up from your music (most recent first)."""

    def __init__(self, path: Path, persist: bool = True):
        self.path = path
        self.persist = persist
        self.tunes: list[dict] = []
        if persist and path.exists():
            try:
                self.tunes = json.loads(path.read_text(encoding="utf-8"))[:12]
            except Exception:
                self.tunes = []

    def learn(self, key: str, title: str, notes: list[tuple[int, float]], quality: float, bpm: float) -> bool:
        """Keep the best phrase per song. Returns True if this is a new / better tune."""
        for t in self.tunes:
            if t["key"] == key:
                if quality <= t["quality"]:
                    return False
                t.update(notes=notes, quality=quality, bpm=bpm, t=time.time())
                self._save()
                return True
        self.tunes.insert(0, {"key": key, "title": title, "notes": notes, "quality": quality, "bpm": bpm,
                              "t": time.time(), "hummed": 0})
        del self.tunes[12:]
        self._save()
        return True

    def pick(self) -> dict | None:
        if not self.tunes:
            return None
        # favour recent, good, not-yet-hummed-much tunes
        w = np.array([(t["quality"] + 0.2) / (1 + t.get("hummed", 0)) * (0.85 ** i) for i, t in enumerate(self.tunes)])
        t = self.tunes[int(np.random.choice(len(self.tunes), p=w / w.sum()))]
        t["hummed"] = t.get("hummed", 0) + 1
        self._save()
        return t

    def forget(self) -> None:
        self.tunes = []
        self._save()

    def _save(self) -> None:
        if not self.persist:
            return
        try:
            self.path.write_text(json.dumps(self.tunes), encoding="utf-8")
        except OSError:
            pass
