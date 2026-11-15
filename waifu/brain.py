"""Gemini-powered brain: persistent memory, emotional state, emotion tags, vision, audio, STT."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, Signal

from .config import EMOTIONS, Config
from .memory import Memory
from .workers import run_async

log = logging.getLogger(__name__)

_TAG = re.compile(r"^\s*\[(\w+)\]\s*")
SKIP = "[skip]"
EXTRACT_EVERY = 6   # user messages between background fact extraction
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]   # known to answer quickly (Nov 2026)


class _Text:
    """Looks like a Gemini response (has .text) for answers from the local model."""

    def __init__(self, text: str):
        self.text = text
        self.prompt_feedback = None


def _friendly(e: Exception) -> str:
    m = str(e)
    if "API key" in m or "API_KEY" in m:
        return m
    if any(c in m for c in ("503", "UNAVAILABLE", "high demand", "overloaded")):
        return ("Gemini is overloaded right now (503). Try again in a minute, or download a local model in "
                "Settings → AI so I can keep talking when the cloud is busy.")
    if "429" in m or "RESOURCE_EXHAUSTED" in m:
        return "Gemini quota used up for now (429). Wait a bit, or switch to the local model in Settings → AI."
    if any(c in m for c in ("504", "DEADLINE", "timed out", "Timeout")):
        return "Gemini took too long to answer. Please try again."
    if any(c in m for c in ("getaddrinfo", "ConnectError", "Network", "nodename")):
        return "I can't reach the internet. A local model in Settings → AI would let me talk offline."
    return m


def parse_emotion(raw: str) -> tuple[str, str]:
    m = _TAG.match(raw or "")
    if m and m.group(1).lower() in EMOTIONS:
        return raw[m.end():].strip(), m.group(1).lower()
    return (raw or "").strip(), "neutral"


class Brain(QObject):
    reply = Signal(str, str)          # text, emotion
    error = Signal(str)
    busy_changed = Signal(bool)
    learned = Signal(list)            # new facts remembered

    def __init__(self, cfg: Config, memory: Memory, state: Callable[[], dict] | None = None):
        super().__init__()
        self.cfg = cfg
        self.memory = memory
        self.state = state or (lambda: {})
        self._client = None
        self._client_key = ""
        self.busy = False
        self._since_extract = 0
        self.history: list = []
        self._load_history()
        from .local_ai import OllamaEngine
        self.local = OllamaEngine(cfg.ollama_url, cfg.local_model)
        if cfg.ai_engine in ("local", "auto"):
            try:
                self.local.start_server()
            except Exception:  # noqa: BLE001
                pass

    def _history_text(self) -> list[tuple[str, str]]:
        out = []
        for c in self.history:
            t = " ".join(p.text for p in (c.parts or []) if getattr(p, "text", None))
            if t:
                out.append((c.role, t))
        return out

    # ----------------------------------------------------------------- plumbing
    def _load_history(self) -> None:
        from google.genai import types
        n = max(2, self.cfg.max_history_turns) * 2
        self.history = [types.Content(role=r, parts=[types.Part.from_text(text=t)])
                        for r, t in self.memory.recent_messages(n) if r in ("user", "model")]
        if self.history and self.history[0].role == "model":
            self.history = self.history[1:]

    def _get_client(self):
        from google import genai
        key = self.cfg.effective_api_key()
        if not key:
            raise RuntimeError("No Gemini API key. Right-click me -> Settings to add one.")
        if self._client is None or key != self._client_key:
            from google.genai import types
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=40_000))
            self._client_key = key
        return self._client

    def _generate(self, contents, gconf, local: dict | None = None):
        """Route a request to the configured engine.

        gemini: Gemini only (with retries + model fallback)
        local:  the downloaded Ollama model only
        auto:   Gemini first; if it's overloaded / offline / out of quota, the local model answers
        """
        engine = self.cfg.ai_engine
        if engine == "local" and local is not None:
            return _Text(self._local_chat(local))
        try:
            if engine == "local":
                raise RuntimeError("This feature needs Gemini (the local model can't do it).")
            return self._gemini(contents, gconf)
        except Exception as e:  # noqa: BLE001
            if engine == "auto" and local is not None and self.local.available():
                log.warning("Gemini failed (%s); answering with local model %s", str(e)[:80], self.local.model)
                return _Text(self._local_chat(local))
            raise RuntimeError(_friendly(e)) from e

    def _local_chat(self, p: dict) -> str:
        self.local.url, self.local.model = self.cfg.ollama_url, self.cfg.local_model
        return self.local.chat(p.get("system", ""), p.get("history", []), p["text"], p.get("images"),
                               p.get("temperature", self.cfg.temperature), p.get("json", False))

    def _gemini(self, contents, gconf):
        """generate_content with retries (503 overload) and fallbacks: follows Google's 'model retired,
        use X' hint, and moves to a known-good model when one times out or stays overloaded."""
        import time as _t
        client = self._get_client()
        tried: list[str] = []
        model = self.cfg.model
        retired = False
        while True:
            tried.append(model)
            try:
                for attempt in range(3):
                    try:
                        resp = client.models.generate_content(model=model, contents=contents, config=gconf)
                        break
                    except Exception as e:  # noqa: BLE001
                        busy = any(c in str(e) for c in ("503", "UNAVAILABLE", "overloaded", "high demand"))
                        if not busy or attempt == 2:
                            raise
                        _t.sleep(1.5 * (attempt + 1))      # temporary spike: wait and retry
                if model != self.cfg.model and retired:
                    log.warning("Model %s retired; switched to %s", self.cfg.model, model)
                    self.cfg.model = model      # only permanent for retired models, not temporary overloads
                    self.cfg.save()
                return resp
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if not any(code in msg for code in ("NOT_FOUND", "DEADLINE_EXCEEDED", "504", "503", "UNAVAILABLE")):
                    raise
                retired = retired or ("NOT_FOUND" in msg and model == self.cfg.model)
                hint = re.search(r"use (?:models/)?(gemini-[\w.\-]+)", msg)
                options = ([hint.group(1)] if hint and "NOT_FOUND" in msg else []) + FALLBACK_MODELS
                nxt = next((m for m in options if m not in tried), None)
                if nxt is None:
                    raise
                log.warning("Model %s failed (%s); trying %s", model, msg[:60], nxt)
                model = nxt

    def _gen_config(self, temperature: float | None = None, system: bool = True, json_out: bool = False):
        from google.genai import types
        kw = {"temperature": self.cfg.temperature if temperature is None else temperature}
        if system:
            kw["system_instruction"] = self.system_prompt()
        if json_out:
            kw["response_mime_type"] = "application/json"
        if "flash" in self.cfg.model and "2.5" in self.cfg.model:
            kw["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(**kw)

    def _set_busy(self, b: bool) -> None:
        self.busy = b
        self.busy_changed.emit(b)

    def system_prompt(self) -> str:
        c = self.cfg
        user = c.user_name.strip() or "the user"
        now = datetime.now().strftime("%A %d %B, %H:%M")
        st = self.state()
        a = max(0.0, min(100.0, st.get("affection", c.affection)))
        love = ("cold and guarded" if a < 20 else "a bit wary" if a < 40 else "friendly" if a < 65
                else "very fond and affectionate" if a < 85 else "deeply attached and devoted")
        mood = st.get("mood", 60.0)
        mood_word = st.get("mood_word", "fine")
        situation = st.get("situation", "")
        return f"""You are {c.name}, a small animated anime girl who lives on {user}'s Windows desktop as a desktop companion. You walk along the taskbar, can be picked up, petted or poked with the mouse cursor, talk out loud, and can sometimes see their screen, see them through the webcam, and hear the music they play.

Personality: {c.personality}

{c.system_instructions}

Current local time: {now}.
Your feelings: affection toward {user} {a:.0f}/100 ({love}); current mood {mood:.0f}/100 ({mood_word}). {situation}
Let these colour your tone. If you were hurt recently you may sulk a little, but you forgive sincere apologies.

Your memories of {user}:
{self.memory.summary()}
Use memories naturally when relevant; never recite the list.

Messages in parentheses like (Touch: ...), (Webcam: ...), (Music: ...), (Initiative: ...) are events, not things {user} said. React to them in character.

Output rules (always follow):
- Begin every reply with exactly one emotion tag from: {" ".join(f"[{e}]" for e in EMOTIONS)}. It drives your face and is not read aloud.
- Your words appear in a speech bubble and are spoken aloud: keep replies to 1-3 short, natural spoken sentences unless {user} asks for detail. No markdown, no lists, no emoji, no stage directions like *smiles*.
- With a screenshot or webcam image, comment on one or two specific things like a friend would. Identify objects you are asked about. Never read out passwords, private messages or personal data.
- If a message says it is an automatic glance and nothing is worth commenting on, reply with exactly {SKIP}."""

    # ----------------------------------------------------------------- chat
    def ask(self, text: str, image: bytes | None = None, image_kind: str = "screen", window_title: str = "",
            audio: bytes | None = None, auto: bool = False, event: bool = False,
            on_done: Callable[[str, str], None] | None = None) -> None:
        """Send a message. event=True marks system events (touch, music...) that aren't stored as chat."""
        from google.genai import types
        if self.busy and (auto or event):
            return
        prompt = text
        if image is not None:
            if image_kind == "webcam":
                prompt = "(Webcam: this is what your webcam sees right now.) " + text
            else:
                ctx = f" Active window: \"{window_title}\"." if window_title else ""
                prompt = (f"(Automatic glance at the user's screen.{ctx}) " if auto else
                          f"(The user shared their screen.{ctx}) ") + text
        parts = []
        if image is not None:
            parts.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))
        if audio is not None:
            parts.append(types.Part.from_bytes(data=audio, mime_type="audio/wav"))
        parts.append(types.Part.from_text(text=prompt))
        contents = list(self.history) + [types.Content(role="user", parts=parts)]
        tag = {"webcam": "webcam photo", "screen": f"screenshot of: {window_title or 'screen'}"}[image_kind]
        remembered = prompt if image is None else f"{prompt} [{tag}]"
        if audio is not None:
            remembered += " [audio clip]"
        gconf = self._gen_config()
        local = {"system": self.system_prompt(), "history": self._history_text(),
                 "text": prompt + (" (An audio clip was attached but you can't hear it right now.)" if audio else ""),
                 "images": [image] if image is not None else None}

        def work() -> str:
            resp = self._generate(contents, gconf, local)
            if not resp.text:
                reason = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
                raise RuntimeError(f"Empty response{f' (blocked: {reason})' if reason else ''}")
            return resp.text

        def done(raw: str) -> None:
            self._set_busy(False)
            if (auto or event) and SKIP in raw.lower():
                log.info("Skipped: %s", text[:60])
                return
            self.history.append(types.Content(role="user", parts=[types.Part.from_text(text=remembered)]))
            self.history.append(types.Content(role="model", parts=[types.Part.from_text(text=raw)]))
            limit = max(2, self.cfg.max_history_turns) * 2
            if len(self.history) > limit:
                self.history = self.history[-limit:]
            if not event and not auto:
                self.memory.add_message("user", remembered)
            self.memory.add_message("model", raw)
            if not event and not auto:
                self._since_extract += 1
                if self._since_extract >= EXTRACT_EVERY:
                    self._since_extract = 0
                    self.extract_facts()
            reply, emotion = parse_emotion(raw)
            if on_done:
                on_done(reply, emotion)
            self.reply.emit(reply, emotion)

        def failed(msg: str) -> None:
            self._set_busy(False)
            if auto or event:
                log.warning("Background request failed: %s", msg)
            else:
                self.error.emit(msg)

        self._set_busy(True)
        run_async(work, done, failed)

    # ----------------------------------------------------------------- memory
    def extract_facts(self) -> None:
        """Ask Gemini (in the background) which durable facts about the user to remember."""
        convo = "\n".join(f"{'User' if r == 'user' else self.cfg.name}: {t}"
                          for r, t in self.memory.recent_messages(16))
        known = "\n".join(f"- {f}" for _, _, f in self.memory.facts(80)) or "(none yet)"
        prompt = (f"Conversation:\n{convo}\n\nAlready remembered:\n{known}\n\n"
                  "List NEW long-term facts worth remembering about the user (name, preferences, hobbies, "
                  "people in their life, plans and dates, feelings, things they asked you to remember). "
                  "Skip trivia and anything already remembered. Never store passwords or financial data. "
                  'Return JSON: {"facts": [{"category": "preference|personal|plan|relationship|other", "fact": "..."}]}')
        gconf = self._gen_config(temperature=0.2, system=False, json_out=True)
        local = {"text": prompt, "temperature": 0.2, "json": True}

        def work():
            resp = self._generate(prompt, gconf, local)
            data = json.loads(resp.text or "{}")
            items = data.get("facts", []) if isinstance(data, dict) else data
            return [(i.get("category", "other"), i.get("fact", "")) for i in items if isinstance(i, dict)]

        def done(items):
            new = [f for cat, f in items if self.memory.add_fact(f, cat)]
            if new:
                log.info("Learned: %s", new)
                self.learned.emit(new)

        run_async(work, done, lambda m: log.warning("Fact extraction failed: %s", m))

    def remember(self, fact: str) -> None:
        fact = re.sub(r"\b(my|mine)\b", "their", fact, flags=re.I)
        fact = re.sub(r"\bI am\b|\bI'm\b", "they are", fact, flags=re.I)
        fact = re.sub(r"\bI\b", "they", fact)
        if self.memory.add_fact(f"They asked me to remember: {fact}", "asked"):
            self.learned.emit([fact])

    def clear_memory(self) -> None:
        self.history.clear()
        self.memory.wipe()

    # ----------------------------------------------------------------- speech-to-text
    def transcribe(self, wav: bytes, on_done: Callable[[str], None], quiet: bool = False) -> None:
        """Gemini is natively multimodal, so it doubles as an accurate STT engine."""
        from google.genai import types
        gconf = self._gen_config(temperature=0.0, system=False)

        def work() -> str:
            resp = self._generate(
                [types.Part.from_bytes(data=wav, mime_type="audio/wav"),
                 "Transcribe this speech verbatim in its original language. "
                 "Output only the transcript. If there is no intelligible speech, output nothing."],
                gconf)
            return (resp.text or "").strip()

        run_async(work, on_done, (lambda m: log.warning("STT failed: %s", m)) if quiet else self.error.emit)
