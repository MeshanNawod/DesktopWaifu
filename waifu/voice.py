"""Voice: text-to-speech with lip-sync, and microphone recording with silence detection."""
from __future__ import annotations

import io
import logging
import math
import re
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .config import CACHE_DIR, Config
from .workers import run_async

log = logging.getLogger(__name__)

_STRIP = re.compile(r"\*[^*]{0,60}\*|\[[a-z]+\]|[\U00010000-\U0010FFFF]")


def clean_for_speech(text: str) -> str:
    return re.sub(r"\s+", " ", _STRIP.sub("", text)).strip()


@dataclass
class SpeechAudio:
    path: Path
    spans: list[tuple[float, float]] = field(default_factory=list)   # (start_ms, end_ms) of words
    envelope: list[float] | None = None                              # loudness per ENV_STEP ms
    ENV_STEP = 20


# ----------------------------------------------------------------------------- engines
def synth_edge(text: str, voice: str, rate: str, pitch: str) -> SpeechAudio:
    import asyncio
    import edge_tts

    path = CACHE_DIR / f"tts_{uuid.uuid4().hex}.mp3"

    async def go() -> list[tuple[float, float]]:
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
        except TypeError:  # edge-tts < 7
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        spans = []
        with open(path, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    start = chunk["offset"] / 10_000  # 100ns ticks -> ms
                    spans.append((start, start + chunk["duration"] / 10_000))
        return spans

    spans = asyncio.run(go())
    return SpeechAudio(path, spans=spans)


def synth_sapi(text: str) -> SpeechAudio:
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except ImportError:
        pass
    import pyttsx3

    path = CACHE_DIR / f"tts_{uuid.uuid4().hex}.wav"
    eng = pyttsx3.init()
    for v in eng.getProperty("voices"):
        if any(n in v.name.lower() for n in ("zira", "haruka", "ayumi", "female")):
            eng.setProperty("voice", v.id)
            break
    eng.setProperty("rate", 185)
    eng.save_to_file(text, str(path))
    eng.runAndWait()
    eng.stop()
    return SpeechAudio(path, envelope=wav_envelope(path.read_bytes()))


def wav_envelope(data: bytes) -> list[float]:
    import numpy as np
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        return []
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    step = max(1, int(sr * SpeechAudio.ENV_STEP / 1000))
    n = len(a) // step
    if n == 0:
        return []
    rms = np.sqrt((a[: n * step].reshape(n, step) ** 2).mean(axis=1))
    peak = float(np.percentile(rms, 95)) or 1.0
    return [min(1.0, float(v) / peak) for v in rms]


# ----------------------------------------------------------------------------- player
class Speaker(QObject):
    mouth = Signal(float)
    started = Signal()
    finished = Signal()
    error = Signal(str)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for old in CACHE_DIR.glob("tts_*"):
            try:
                old.unlink()
            except OSError:
                pass
        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(lambda _e, msg: self._fail(msg))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._gen = 0
        self._current: SpeechAudio | None = None
        self.speaking = False

    def speak(self, text: str) -> None:
        if not self.cfg.tts_enabled:
            return
        text = clean_for_speech(text)
        if not text:
            return
        self.stop()
        self._gen += 1
        gen = self._gen
        cfg = self.cfg

        def work() -> SpeechAudio:
            if cfg.tts_engine == "edge":
                try:
                    return synth_edge(text, cfg.tts_voice, cfg.tts_rate, cfg.tts_pitch)
                except Exception as e:  # offline, bad voice name...
                    log.warning("edge-tts failed (%s); falling back to SAPI", e)
            return synth_sapi(text)

        run_async(work, lambda audio: self._play(gen, audio), self.error.emit)

    def _play(self, gen: int, audio: SpeechAudio) -> None:
        if gen != self._gen:            # superseded by newer speech
            self._delete(audio.path)
            return
        self._current = audio
        self._audio.setVolume(max(0.0, min(1.0, self.cfg.tts_volume)))
        self._player.setSource(QUrl.fromLocalFile(str(audio.path)))
        self._player.play()
        self._t0 = time.monotonic()
        self._timer.start(30)
        self.speaking = True
        self.started.emit()

    def _tick(self) -> None:
        a = self._current
        if a is None:
            return
        pos = float(self._player.position())
        if pos <= 0:  # backend not reporting yet
            pos = (time.monotonic() - self._t0) * 1000
        syll = abs(math.sin(pos / 1000 * math.tau * 4.2))  # ~8 mouth flaps per second
        if a.envelope:
            i = int(pos // SpeechAudio.ENV_STEP)
            v = a.envelope[i] if i < len(a.envelope) else 0.0
            v = v if v > 0.12 else 0.0
        elif a.spans:
            v = 0.0
            for s, e in a.spans:
                if s <= pos <= e:
                    v = 0.3 + 0.7 * syll
                    break
        else:
            v = 0.2 + 0.8 * syll
        self.mouth.emit(v)

    def _on_status(self, status) -> None:
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            self._finish()

    def _fail(self, msg: str) -> None:
        if msg:
            log.warning("Playback error: %s", msg)
        self._finish()

    def _finish(self) -> None:
        if self.speaking:
            self.stop()  # emits finished

    def stop(self) -> None:
        self._timer.stop()
        was = self.speaking
        self.speaking = False
        self._player.stop()
        self._player.setSource(QUrl())
        if self._current:
            self._delete(self._current.path)
            self._current = None
        self.mouth.emit(0.0)
        if was:
            self.finished.emit()

    @staticmethod
    def _delete(path: Path) -> None:
        def rm() -> None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # still locked by the media backend; cleaned on next start
        QTimer.singleShot(500, rm)


# ----------------------------------------------------------------------------- microphone
class MicRecorder(QObject):
    """Push-to-talk style recorder: start(), auto-stops after a pause in speech."""
    started = Signal()
    stopped = Signal()
    level = Signal(float)
    audio_ready = Signal(bytes)       # 16 kHz mono WAV
    nothing_heard = Signal()
    error = Signal(str)

    RATE = 16000

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._stream = None
        self._chunks: list = []
        self._rms = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def toggle(self) -> None:
        self.stop() if self.recording else self.start()

    def start(self) -> None:
        if self.recording:
            return
        try:
            import numpy as np
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Microphone unavailable: {e}")
            return
        self._np = np
        self._chunks = []
        self._noise: list[float] = []
        self._heard = False
        self._t0 = self._last_voice = time.monotonic()

        def cb(indata, _frames, _t, _status):
            self._chunks.append(indata.copy())
            self._rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))

        try:
            dev = None if self.cfg.mic_device < 0 else self.cfg.mic_device
            self._stream = sd.InputStream(samplerate=self.RATE, channels=1, dtype="int16",
                                          callback=cb, device=dev, blocksize=800)
            self._stream.start()
        except Exception as e:  # noqa: BLE001
            self._stream = None
            self.error.emit(f"Could not open microphone: {e}")
            return
        self._timer.start(50)
        self.started.emit()

    def _tick(self) -> None:
        now = time.monotonic()
        el = now - self._t0
        if el < 0.35:                       # calibrate background noise
            self._noise.append(self._rms)
            return
        floor = (sum(self._noise) / len(self._noise)) if self._noise else 200.0
        thresh = max(350.0, floor * 2.5)
        self.level.emit(min(1.0, self._rms / (thresh * 3)))
        if self._rms > thresh:
            self._heard = True
            self._last_voice = now
        if self._heard and now - self._last_voice > self.cfg.stt_silence_seconds:
            self.stop()
        elif el > self.cfg.stt_max_seconds or (not self._heard and el > 7):
            self.stop()

    def stop(self) -> None:
        if not self._stream:
            return
        self._timer.stop()
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        self.stopped.emit()
        if not self._heard or not self._chunks:
            self.nothing_heard.emit()
            return
        pcm = self._np.concatenate(self._chunks).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(pcm)
        self.audio_ready.emit(buf.getvalue())
