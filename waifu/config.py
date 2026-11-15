"""Persistent settings stored in %APPDATA%\\DesktopWaifu\\config.json."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "DesktopWaifu"
CONFIG_PATH = APP_DIR / "config.json"
CACHE_DIR = APP_DIR / "cache"

DEFAULT_PERSONALITY = (
    "Cheerful, playful and a little teasing, but genuinely caring. Gets flustered "
    "when complimented or petted. Curious about whatever the user is working on, "
    "loves games and anime, and gently nags them to take breaks and drink water."
)

DEFAULT_SYSTEM = (
    "You are a supportive companion, not an assistant reading a manual. Speak casually "
    "like a close friend. If the user asks for real help (code, writing, facts) give "
    "accurate, useful answers, but stay in character."
)

EMOTIONS = ["neutral", "happy", "excited", "shy", "flustered", "playful", "sad", "angry", "surprised",
            "thinking", "sleepy", "hurt", "struggling"]


@dataclass
class Config:
    # Character
    name: str = "Hana"
    user_name: str = ""
    personality: str = DEFAULT_PERSONALITY
    system_instructions: str = DEFAULT_SYSTEM

    # Gemini
    api_key: str = ""                      # GEMINI_API_KEY env var takes precedence
    model: str = "gemini-3.5-flash"
    temperature: float = 0.9
    ai_engine: str = "auto"                # gemini | local | auto (Gemini, falls back to local model)
    local_model: str = "gemma3:4b"         # Ollama model name
    ollama_url: str = "http://localhost:11434"
    max_history_turns: int = 20

    # Voice
    tts_enabled: bool = True
    tts_engine: str = "edge"               # edge | sapi
    tts_voice: str = "en-US-AnaNeural"
    tts_rate: str = "+5%"
    tts_pitch: str = "+15Hz"
    tts_volume: float = 0.9
    stt_silence_seconds: float = 1.2
    stt_max_seconds: float = 20.0
    mic_device: int = -1                   # -1 = system default

    # Vision
    vision_auto: bool = False
    vision_interval_min: float = 10.0
    vision_max_side: int = 1280
    vision_blocklist: list[str] = field(default_factory=lambda: [
        "password", "bank", "1password", "bitwarden", "keepass", "lastpass",
        "inprivate", "incognito", "private browsing", "authenticator",
    ])

    # Appearance / behaviour
    sprite_pack: str = ""                  # folder with sheet.json; empty = built-in
    builtin_style: str = "vrm"             # vrm (3D model) | image (live picture) | scarf | classic
    vrm_path: str = ""                     # .vrm file; empty = first file in assets/models
    image_pack: str = ""                   # folder with character.png/json; empty = bundled
    ai_touch_reactions: bool = True        # Gemini writes reactions to touches (with cooldown)

    # Senses & life
    webcam_enabled: bool = False           # she can see you (presence detection runs locally)
    webcam_index: int = 0
    webcam_greet: bool = True              # greet you (with a webcam photo to Gemini) when you come back
    music_enabled: bool = True             # listen to what your PC plays (beat analysis is local)
    music_send_audio: bool = False         # send a short music clip to Gemini so she can really hear it
    wake_by_voice: bool = False            # while asleep, the mic listens for her name / "wake up"
    initiative_minutes: float = 20.0       # she starts a conversation after this much silence (0 = never)
    sleep_after_min: float = 8.0           # naps when you've been idle this long
    affection: float = 50.0                # 0..100, grows with kindness
    scale: float = 1.0
    walk_speed: float = 90.0               # px / second
    activity: float = 0.5                  # 0 = lazy, 1 = hyperactive
    always_on_top: bool = True
    bubble_seconds: float = 7.0
    force_hit_mask: bool = False           # fallback if click-through misbehaves

    def effective_api_key(self) -> str:
        return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                or self.api_key).strip()

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in known:
                        setattr(cfg, k, v)
            except Exception:
                log.exception("Could not read config, using defaults")
        return cfg

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CONFIG_PATH)
