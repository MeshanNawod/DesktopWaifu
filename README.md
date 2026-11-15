# DesktopWaifu

An animated desktop companion for Windows. She walks along your taskbar, can be picked up and
thrown, likes headpats, chats and talks out loud, and can glance at your screen. Her brain is Google Gemini.

## Quick start

1. Install **Python 3.12 or 3.13** from python.org (tick "Add to PATH" and keep the `py` launcher).
2. Double-click **`setup.bat`**. It creates `.venv` and installs the dependencies.
3. Get a free API key at <https://aistudio.google.com/apikey>.
4. Double-click **`run.bat`**. On first launch the Settings window opens so you can paste the key.
   You can also set the `GEMINI_API_KEY` environment variable instead.

Use `run.bat debug` to keep a console with live logs. Logs are also written to
`%APPDATA%\DesktopWaifu\desktopwaifu.log`.

## Controls

| Action | What happens |
|---|---|
| Drag her | She dangles and tilts with your motion. Let go to throw her, and gravity drops her onto the taskbar. |
| Click or rub her head | Headpats: hearts, a blush and a happy reaction |
| Click her body | A poke, which she reacts to |
| Double-click | Opens or closes the chat panel. Drag its header to detach it from her. |
| Right-click, or the tray icon | Menu: Chat, Talk, Look at my screen, voice and glance toggles, Settings, Quit |
| 🎤 in chat | Talk into the mic. Recording stops by itself when you pause. |
| 👁 in chat | She looks at your current screen and comments |

Phrases like "look at my screen" or "what am I doing" in a chat message also attach a screenshot.

## Gestures (`waifu/gestures.py`)

| Mouse | Meaning | She… |
|---|---|---|
| Short click | gentle touch | smiles softly and greets you |
| Long press / hold (0.55 s) | firm press, a hit ("hayiyen gahanawa") | is startled or hurt and gets defensive; mood drops |
| Double click, or stroking back and forth over her head / face / hair | petting ("athaganawa") | closes her eyes happily and blushes; big affection boost |
| Press a **hand or leg** and drag | lifted by that limb | dangles from it (by a leg = upside down), swings with your motion and **struggles / wriggles**: kicking, flailing, head shaking, sweat drops |
| Press her body and drag | picked up | swings, struggles, falls with gravity when released |
| Middle click / tray icon / right-click → Chat | | opens the chat |

Hitboxes: head, face (cheeks), eyes, mouth, hands, legs, body (plus hair, chest, skirt).

## Characters

Right-click her, then **👗 Character**, to switch at any time:

| Character | Type | Notes |
|---|---|---|
| **Futuristic Girl** | 3D FBX (`assets/models/FuturisticGirl/`) | Detailed static mesh. She's animated procedurally in the shader: head and gaze tracking, relaxed T-pose arms with shoulder joints (they lift when dragged and reach when you pull a hand), breathing, and a spring wobble of the whole body. |
| **Seed-san** | 3D VRM (`assets/models/Seed-san.vrm`) | Fully rigged: walking legs, sitting, spring-bone hair and cloth, facial blend shapes |
| **Seed-san 2** | Same VRM, realistic + interactive mode | Soft wrap lighting, rim light, specular highlights, 8× antialiasing and a contact shadow. Contrapposto weight-shift idle, curled fingers, eye saccades and a natural walk with bent elbows. She waves when your cursor comes near, gives a high five when you tap her hand, and does mood-based idle gestures (stretch, look around, hair touch, shy hands, hands behind back, cheer) plus talking hand gestures. Every `.vrm` gets a "2" version. |
| Live picture | 2D image warp | Scarf-girl picture with hair, scarf and skirt physics |
| Drawn girls | 2D vector | Lightweight, with no assets needed |

To add more, drop a `.vrm` into `assets/models/`, or put a `.fbx` (plus textures) in its own folder with an optional
`character.json` such as `{"name": "...", "front": "+x", "tpose": true, "arm_down_deg": 76}`.

## 64-expression matrix (`waifu/expressions.py`)

**8 eye gazes × 8 faces = 64 named expressions.**

- **Gazes:** direct, side glance, shy downcast, upward, half-lidded, closed (^^), sparkle, wink.
- **Faces:** calm, gentle smile, beaming, teasing smirk, pout, blush, startled, sorrowful.

Each **mood** (Happy, Shy, Flustered, Sleepy, Upset, Playful, Neutral) weights which gazes and faces she uses. Every few seconds she drifts to another fitting cell (micro-expressions), and touches or Gemini's emotion tags snap her to one immediately. Gaze offsets blend with cursor tracking: a side glance looks away while a direct gaze follows you.

| Touch | Mood it gives |
|---|---|
| Gentle on head or hands | Happy / Shy, affection goes up |
| Teasing / sensitive (skirt, chest) | **Flustered**: defensive at low affection, shy or playful when you're close |
| Lots of light boops | **Playful** |
| Rough (smack, rapid hits, scrubbing) | **Upset / Hurt**, mood drops, she sulks until comforted or you apologise |

## Project structure

```
main.py                    entry point
waifu/waifu_core.py        transparent overlay window: physics, walking, limb lifting + struggling, hit-testing
waifu/gestures.py          gesture listener: click / hold / double-click / stroke / lift
waifu/gemini_brain.py      google-genai chat, vision, audio, STT, fact extraction, retries + model fallback
waifu/database.py          SQLite memory: %APPDATA%\DesktopWaifu\waifu_memory.db
waifu/local_ai.py          optional offline brain via Ollama
waifu/app.py               controller: wires everything, menus, tray, life loop (sleep, initiative, music)
waifu/emotion.py           state machine (awake/sleeping/hurt/happy/listening) + moods incl. struggling + affection
waifu/expressions.py       64-expression matrix and micro-expression engine
waifu/camera.py, music.py, vision.py, voice.py     senses (webcam, music loopback, screen, mic/TTS)
waifu/vrm/ , waifu/fbx/    3D loaders + OpenGL renderer;  vrm_character.py / mesh_character.py
waifu/live_image.py, sprites.py                     2D characters
```

**Gemini model:** `gemini-2.5-flash` is retired for new API keys (it returns 404). The default is
`gemini-3.5-flash`. On a 503 overload she retries twice, then falls back to `gemini-3.5-flash-lite`.
A retired model (404) is replaced permanently. You can pick any model in Settings → AI.

### Local, downloadable AI (offline brain), `waifu/local_ai.py`
Settings → AI → **AI engine**:
- **Gemini online, local model when Gemini is busy or offline** (default)
- **Gemini online only**
- **Local downloaded model only**: offline and private, and no API key needed

The local engine is [Ollama](https://ollama.com). Click **Install Ollama…** (uses winget), pick a model, and click
**Download model**. Suggested models: `gemma3:4b` (vision, ~3.3 GB), `gemma3:12b`, `qwen2.5vl:7b`, `llama3.2:3b`.
Vision models can also see screenshots and webcam photos. Speech-to-text still uses Gemini.

## Feelings, memory and senses

### Emotional state machine (`waifu/emotion.py`)
`AWAKE`, `SLEEPING`, `HURT` (sulking), `HAPPY` and `LISTENING` (music), plus **mood** (short-term)
and **affection** (long-term). Both are shown in the tray tooltip and the chat header, and Gemini sees them.

| From | To | How |
|---|---|---|
| Awake | **Sleeping** | You've been idle `sleep_after_min` minutes (sooner at night), or you say "good night" / "go to sleep", or use the menu |
| Sleeping | **Awake** | You click or pat her, pick her up, open the chat, call her name / say "wake up" (if *Wake by voice* is on), or walk back in front of the webcam |
| any | **Hurt** | You're rough (see below). She sits, turns away, cries a little and is colder in chat |
| Hurt | Awake | Time passes, gentle touches comfort her, or you say sorry ("sorry", "samawenna", "forgive me"…) |
| Awake | **Happy** | Mood reaches 85 or more from affection |
| Awake | **Listening** | Your PC plays music |

### Gentle vs rough touch (`waifu/character.py`)
Zones: **head (oluwa), hands (atha), mouth (kata), eyes (aha)**, cheeks, hair, plus skirt and chest.

| Gesture | Counts as |
|---|---|
| Slow stroke over a zone, long soft press (≥0.45 s), holding her hand | **gentle**: blush, hearts, cute line, mood and affection go up |
| Normal click | **tap** |
| Cursor slammed in fast (≥2200 px/s) when you click, 3+ clicks within 1 s, frantic scrubbing | **rough**: she flinches (>< eyes, tears, head knocked back), says a pain line, mood and affection drop, and she becomes **Hurt** |

### Memory (`waifu/memory.py`, `%APPDATA%\DesktopWaifu\memory.db`)
- Every chat line is saved, and the last *Memory (turns)* lines are reloaded at startup.
- Every 6 messages, Gemini pulls durable **facts** about you into the database (likes, plans, people, dates). "Remember that …" saves a fact straight away.
- She also counts **relationship stats** (days together, headpats, hand-holds, hits) and logs **moments** (first meeting, forgiving you, songs you shared).
- You can view or delete facts, or wipe everything, in **Settings → Memory**.

### Senses
- **Mouse tracking:** her head and eyes follow your cursor everywhere on the desktop.
- **Webcam** (`waifu/camera.py`, off by default): face presence runs locally on 2 frames per second, and nothing is uploaded for that. When you return after a while she wakes and greets you, sending one photo to Gemini if *greet* is on. "Look at me", "what am I holding?", "what is this?" or the 📷 button sends one frame so she can identify objects and chat about them.
- **Screen:** 👁 or "look at my screen"; optional periodic glances.
- **Music** (`waifu/music.py`): WASAPI loopback hears what your PC plays. Bass-beat detection makes her bob her head and sway, with floating ♪ notes, and her mood rises. She reads the song title from Spotify, YouTube or VLC and comments on it. With *Let her really hear songs* on, an 8-second clip goes to Gemini so she can react to the actual sound.
- **Initiative:** after `initiative_minutes` of silence while you're at the PC, she starts a conversation, often asking about something she doesn't know about you yet.

## 3D VRM character (default)

She's a full-body 3D model rendered with OpenGL inside the transparent overlay:

- **Physics:** the model's own spring bones move her hair, skirt and accessories. They swing when you drag, throw or walk her.
- **Animation:** relaxed arms, breathing, a walk cycle with real leg motion, sitting on the taskbar edge with her legs dangling, dangling when dragged, flailing when she falls, a shy shrug when petted, and her head and eyes following your cursor.
- **Face:** the model's own expressions handle blinking, emotions (happy, angry, sad, surprised, relaxed) and lip-sync while she talks.
- **Touch zones:** eyes, cheeks, both hands (hold and pull: her arm reaches for your cursor), head, chest, skirt, and hair (grab and pull a strand).

**Get a model that looks like her:** make one for free in **VRoid Studio** (long dark-brown hair,
red-brown eyes, black outfit, red scarf) and use *Export → VRM*. Then pick the file in
Settings → Appearance → VRM model file, or drop it into `assets/models/`.
VRM 0.x and 1.0 are both supported. The bundled `Seed-san.vrm` is the official VRM sample, there for testing.

## Live picture character

She is your own picture (`assets/characters/scarf_girl/`), animated without any rigging:

- **Physics:** her hair, scarf and skirt are soft-body regions on springs. They swing when you drag, throw or walk her, sway in a light breeze, and bounce back after you pull them.
- **Face:** she breathes, blinks, sleeps with closed eyes, and makes ^^ happy eyes or >< squeezed-shut eyes. Her eyes and head follow your cursor. Blush, an anger mark, a sweat drop and tears appear with her emotions.
- **Touch zones:** each part of her has its own reaction.

| Touch | Reaction |
|---|---|
| Eyes | Hovering makes her squint. A click squeezes her eyes shut: "Not my eyes!" |
| Cheeks | A click squishes the cheek and she blushes. Rubbing works too. |
| Hands | A click or hold makes her shy and leaves hearts. **Hold and pull** a hand and it follows the cursor. |
| Head | Pat or rub for headpats |
| Scarf / hair | A click flicks it. **Hold and pull** to tug it, and it springs back when you let go. |
| Skirt / chest | She scolds you, and her affection drops |
| Pull past ~70 px | She gets dragged along by whatever you're holding |

- **Affection (0–100):** this goes up when you're kind (pats, holding hands, chatting) and down when you're rude. It's shown in the tray tooltip, and Gemini adjusts her tone to match.
- **AI touch reactions:** with a key set, Gemini writes a fresh reaction to a touch (at most once every 30 s). Otherwise she uses instant preset lines.

**Use another picture:** run
`.venv\Scripts\python tools\make_cutout.py my.jpg assets\characters\mine\character.png`,
copy `character.json` from `scarf_girl` and adjust the zone coordinates (0–1 of the picture's width and height).
Then choose the folder in Settings → Appearance → Live picture folder. A picture with a plain white background works best.

## Architecture

```
main.py                 entry point + logging
waifu/
  app.py                controller: wires everything, tray, menus, canned reactions
  config.py             dataclass settings <-> %APPDATA%\DesktopWaifu\config.json
  character.py          transparent frameless overlay: state machine, gravity, drag/throw, petting
  sprites.py            ProceduralSprites (built-in vector chibi) + SheetSprites (your PNGs)
  brain.py              Gemini (google-genai): system prompt, memory, emotion tags, vision, STT
  vision.py             mss screenshots of the active monitor, foreground-window tracking, privacy blocklist
  voice.py              Speaker (edge-tts / SAPI + QMediaPlayer + lip-sync), MicRecorder (sounddevice, VAD)
  workers.py            run_async(): QThreadPool helper so the UI never blocks
  ui/bubble.py          speech bubble with typewriter text
  ui/chat.py            floating chat panel
  ui/settings.py        tabbed settings dialog
assets/sprites/         custom sprite-pack format (see README there)
```

**Why Python + PySide6:** Qt's `WA_TranslucentBackground` maps to a per-pixel-alpha layered
window on Windows. Fully transparent pixels are click-through natively, so only her body catches
the mouse. Qt also includes audio playback, a tray icon and high-DPI handling, and it uses about
60–100 MB of RAM, compared with around 300 MB for an Electron app. `google-genai`
is Google's first-party SDK.

**Data flow:**
1. You type or speak. Speech is recorded at 16 kHz and sent to Gemini, which transcribes it.
2. `Brain.ask()` sends the history, your message and an optional JPEG screenshot to Gemini on a worker thread.
3. Each reply starts with an emotion tag such as `[shy]`. The app strips the tag and uses it to set her expression.
4. The reply text goes to the bubble, the chat log and the TTS. While the audio plays, word timings
   (edge-tts) or loudness (SAPI) drive the mouth openness.

**Screenshots are not kept in memory.** Only a text note such as `[screenshot of: VS Code]`
stays in the history, so token costs don't grow over time. The history is capped at
*Memory (turns)*.

## Privacy

- Periodic screen glances are **off by default**. Turn them on in the right-click menu or in Settings → Vision.
- She never captures while the active window title contains a blocklisted word (password managers, banking, private windows and similar). You can edit the list.
- Screenshots and voice clips are sent to the Gemini API under Google's terms. Nothing is saved to disk.
- The API key is stored as plain text in `%APPDATA%\DesktopWaifu\config.json`. If you prefer not to store it there, use the `GEMINI_API_KEY` environment variable.

## Voices

The default engine is **edge-tts**: free Microsoft neural voices that need an internet connection.
Cute presets include `en-US-AnaNeural`, `en-GB-MaisieNeural` and `ja-JP-NanamiNeural`.
To list all voices, run `.venv\Scripts\edge-tts --list-voices`. Tune *Rate* and *Pitch* (e.g. `+20Hz`)
for a more anime-style voice. If edge-tts fails, the app falls back to offline Windows SAPI.

## Troubleshooting

- **Clicks on empty space around her are blocked:** enable *Settings → Appearance → Force click-through mask*.
- **She's hidden behind a fullscreen game:** that's expected, because exclusive fullscreen sits above all windows.
- **No mic:** set *Mic device index*. To list devices, run `.venv\Scripts\python -m sounddevice`.
- **Wrong model name / 404:** pick a model from the list or type any model your key can access.

## Ideas for extending

- Live2D: render a Cubism model into an offscreen QOpenGLWidget/QImage via `live2d-py` and return it from a provider with the same interface as `SheetSprites`.
- Gemini native TTS (`gemini-2.5-flash-preview-tts`) returns PCM, which plugs straight into `wav_envelope()` for amplitude lip-sync.
- Gemini Live API for real-time, interruptible voice conversation.
- A global push-to-talk hotkey (`keyboard` package) that calls `WaifuApp.toggle_mic`.
- Standing on top of other windows: use `EnumWindows` + `GetWindowRect` to find surfaces to land on.
- A single .exe: `pyinstaller --noconsole --name DesktopWaifu main.py`.
