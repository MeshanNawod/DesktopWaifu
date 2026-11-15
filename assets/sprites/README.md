# Custom sprite packs

Out of the box DesktopWaifu draws a built-in vector character, so no art is required.
To use your own art, make a folder like this and pick it in **Settings → Appearance → Sprite pack folder**:

```
my_character/
  sheet.json
  idle.png      # horizontal strip of frames
  walk.png
  sit.png
  drag.png
  fall.png
  pet.png
  talk.png      # optional: used while she speaks (lip-sync)
  sleep.png     # optional
```

`sheet.json`:

```json
{
  "frame_width": 128,
  "frame_height": 160,
  "scale": 1.0,
  "faces": "right",
  "foot_offset": 4,
  "head": [32, 0, 64, 60],
  "animations": {
    "idle":  { "file": "idle.png",  "frames": 4, "fps": 6 },
    "walk":  { "file": "walk.png",  "frames": 8, "fps": 12 },
    "sit":   { "file": "sit.png",   "frames": 2, "fps": 2 },
    "drag":  { "file": "drag.png",  "frames": 4, "fps": 10 },
    "fall":  { "file": "fall.png",  "frames": 2, "fps": 8 },
    "pet":   { "file": "pet.png",   "frames": 4, "fps": 8 },
    "talk":  { "file": "talk.png",  "frames": 2, "fps": 10 },
    "sleep": { "file": "sleep.png", "frames": 2, "fps": 1 },
    "react": { "file": "pet.png",   "frames": 4, "fps": 8, "loop": false }
  }
}
```

| Field | Meaning |
|---|---|
| `faces` | Direction your art faces (`right`/`left`); it's mirrored automatically when walking the other way. |
| `foot_offset` | Transparent pixels below her feet, so she stands exactly on the taskbar. |
| `head` | `[x, y, w, h]` of the head in frame pixels: click or rub here to pet her. |
| `columns`, `row` | Optional, for grid sheets: frames per row, and the starting row. |
| `loop` | `false` holds the last frame. |

Optional emotion variants: name an animation `<state>_<emotion>` (e.g. `idle_happy`, `talk_shy`,
`idle_angry`) and it will be used when Gemini tags a reply with that emotion.
Emotions: neutral, happy, excited, shy, sad, angry, surprised, thinking, sleepy.

Only `idle` is required; any missing animation falls back to `idle`.
Use art you made or have the rights to use.
