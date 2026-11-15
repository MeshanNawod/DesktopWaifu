"""Render the README images into docs/images/ using the real renderers (no screen capture needed).

    .venv\\Scripts\\python tools\\make_screenshots.py
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="waifu_docs_")   # never touch the real config / memory
os.environ.pop("GEMINI_API_KEY", None)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from waifu.expressions import build  # noqa: E402
from waifu.live_image import LiveImageCharacter  # noqa: E402
from waifu.mesh_character import MeshCharacter  # noqa: E402
from waifu.sprites import (DEFAULT_IMAGE_PACK, DRAG, IDLE, PET, REACT, SLEEP, WALK,  # noqa: E402
                           FrameContext, ProceduralSprites)
from waifu.vrm_character import GESTURE_LEN, VrmCharacter  # noqa: E402

OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
DPR = 1.5
SEED = ROOT / "assets" / "models" / "Seed-san.vrm"
FUTURE = ROOT / "assets" / "models" / "FuturisticGirl" / "FuturisticGirl.fbx"
TASKBAR = 34


# ------------------------------------------------------------------ helpers
def run(ch, state, ctx, n, vx=0.0, ax=0.0):
    for _ in range(n):
        ctx.t += 1 / 30
        ctx.state_t += 1 / 30
        if hasattr(ch, "step"):
            ch.step(1 / 30, ax, 0, vx, 0, state, ctx)
    return ch.render(state, ctx, DPR)


def desktop(w: int, h: int) -> tuple[QImage, QPainter]:
    img = QImage(int(w * DPR), int(h * DPR), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(DPR)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#2b3a67"))
    g.setColorAt(0.55, QColor("#7a5aa8"))
    g.setColorAt(1, QColor("#e9a0b8"))
    p.fillRect(QRectF(0, 0, w, h), g)
    # soft bokeh circles
    for i in range(14):
        x, y, r = (i * 137) % w, (i * 71) % (h - 60), 18 + (i * 13) % 40
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawEllipse(QPointF(x, y), r, r)
    # taskbar
    p.fillRect(QRectF(0, h - TASKBAR, w, TASKBAR), QColor(24, 24, 32, 235))
    p.setBrush(QColor(255, 255, 255, 40))
    for i in range(6):
        p.drawRoundedRect(QRectF(w / 2 - 105 + i * 36, h - TASKBAR + 6, 26, 22), 5, 5)
    return img, p


def stand(p: QPainter, frame: QImage, x: float, ground: float, foot: int) -> None:
    w = frame.width() / frame.devicePixelRatio()
    hgt = frame.height() / frame.devicePixelRatio()
    p.drawImage(QPointF(x - w / 2, ground - hgt + foot), frame)


def label(p: QPainter, text: str, x: float, y: float, size: int = 11, color: str = "white") -> None:
    p.setFont(QFont("Segoe UI", size, QFont.Weight.DemiBold))
    fm = p.fontMetrics()
    tw = fm.horizontalAdvance(text)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 120))
    p.drawRoundedRect(QRectF(x - tw / 2 - 8, y - fm.ascent() - 3, tw + 16, fm.height() + 6), 8, 8)
    p.setPen(QColor(color))
    p.drawText(QPointF(x - tw / 2, y), text)


def bubble(p: QPainter, text: str, x: float, y: float, maxw: float = 210) -> None:
    p.setFont(QFont("Segoe UI", 10))
    r = p.fontMetrics().boundingRect(0, 0, int(maxw), 400, int(Qt.TextFlag.TextWordWrap), text)
    bw, bh = r.width() + 24, r.height() + 18
    body = QRectF(x - bw / 2, y - bh - 10, bw, bh)
    path = QPainterPath()
    path.addRoundedRect(body, 12, 12)
    tail = QPainterPath()
    tail.moveTo(x - 8, body.bottom() - 1)
    tail.lineTo(x, body.bottom() + 10)
    tail.lineTo(x + 8, body.bottom() - 1)
    path = path.united(tail)
    p.setPen(QColor("#ff8fb8"))
    p.setBrush(QColor(255, 255, 255, 245))
    p.drawPath(path)
    p.setPen(QColor("#3b2a3f"))
    p.drawText(body.adjusted(12, 9, -12, -9), int(Qt.TextFlag.TextWordWrap), text)


def save(img: QImage, p: QPainter, name: str) -> None:
    p.end()
    img.save(str(OUT / name))
    print("saved", name)


# ------------------------------------------------------------------ characters
print("loading characters…")
seed = VrmCharacter(SEED, 1.0, DPR)
seed2 = VrmCharacter(SEED, 1.0, DPR, enhanced=True)
fut = MeshCharacter(FUTURE, 1.0, DPR)
pic = LiveImageCharacter(DEFAULT_IMAGE_PACK, 1.0, DPR)
drawn = ProceduralSprites(1.4, "scarf")
classic = ProceduralSprites(1.4, "classic")

# 1. hero: everyone on a desktop
W, H = 1280, 520
img, p = desktop(W, H)
ground = H - TASKBAR
cast = [
    (fut, "Futuristic Girl (3D FBX)", FrameContext(emotion="happy", expr=build("direct", "gentle_smile"))),
    (seed2, "Seed-san 2 (3D VRM)", FrameContext(emotion="happy", expr=build("sparkle", "beaming"))),
    (seed, "Seed-san (3D VRM)", FrameContext(expr=build("direct", "gentle_smile"))),
    (pic, "Live picture", FrameContext(emotion="shy", expr=build("downcast", "blush"))),
    (drawn, "Drawn: scarf girl", FrameContext(emotion="happy")),
    (classic, "Drawn: classic", FrameContext(emotion="excited")),
]
xs = [130, 330, 520, 720, 935, 1140]
for (ch, name, ctx), x in zip(cast, xs):
    fr = run(ch, IDLE, ctx, 30)
    stand(p, fr, x, ground, ch.foot_offset())
    label(p, name, x, 26)
bubble(p, "Hi! Double-click me for headpats, middle-click to chat~", 330, 120)
bubble(p, "Ouch! Don't pull my hair~!", 720, 205, 170)
save(img, p, "hero.png")

# 2. 64-expression moods (Futuristic Girl + Seed-san 2)
moods = [("Happy", "happy", "sparkle", "beaming"), ("Shy", "shy", "downcast", "blush"),
         ("Flustered", "flustered", "side_glance", "blush"), ("Playful", "playful", "wink", "teasing_smirk"),
         ("Upset", "sad", "side_glance", "pout"), ("Sleepy", "sleepy", "half_lidded", "calm"),
         ("Struggling", "struggling", "side_glance", "startled")]
W, H = 1260, 760
img, p = desktop(W, H)
for row, ch in enumerate((seed2, fut)):
    for i, (name, emo, gaze, face) in enumerate(moods):
        ctx = FrameContext(emotion=emo, expr=build(gaze, face))
        fr = run(ch, SLEEP if emo == "sleepy" else IDLE, ctx, 20)
        x = 95 + i * 178
        g = 360 * (row + 1) - (0 if row else 12)
        hgt = fr.height() / fr.devicePixelRatio()
        p.drawImage(QPointF(x - fr.width() / fr.devicePixelRatio() / 2, g - hgt + ch.foot_offset()), fr)
        label(p, f"{name}", x, g - hgt + ch.foot_offset() + 26, 10)
        label(p, f"{gaze}+{face}", x, g + 2 if row else g - 6, 8, "#ffd6e7")
save(img, p, "expressions.png")

# 3. Seed-san 2 gestures
gests = [("Wave", "wave_l"), ("High five", "highfive_r"), ("Stretch", "stretch"), ("Hair touch", "hair_touch"),
         ("Shy hands", "shy_hands"), ("Hands behind", "hands_behind"), ("Cheer", "cheer"), ("Explain", "explain")]
W, H = 1280, 470
img, p = desktop(W, H)
for i, (name, g) in enumerate(gests):
    seed2.start_gesture(g)
    seed2._next_gesture = 999
    fr = run(seed2, IDLE, FrameContext(expr=build("direct", "gentle_smile")), int(GESTURE_LEN[g] * 0.45 * 30))
    x = 85 + i * 158
    stand(p, fr, x, H - TASKBAR, seed2.foot_offset())
    label(p, name, x, 30)
save(img, p, "gestures.png")

# 4. touch reactions
reacts = [("Petting (double-click / stroke)", PET, FrameContext(emotion="happy", pet_level=1.0), ("head", "pet")),
          ("Hold = hard press (hurt)", REACT, FrameContext(emotion="hurt"), ("head", "rough")),
          ("Nose / eye poke", REACT, FrameContext(emotion="surprised", expr=build("closed", "startled")), ("nose", "gentle")),
          ("Hair pull (pout)", REACT, FrameContext(emotion="pout", expr=build("side_glance", "pout")), None),
          ("Sudden touch (flustered)", REACT, FrameContext(emotion="flustered", expr=build("side_glance", "blush")), ("chest", "gentle")),
          ("Sleeping", SLEEP, FrameContext(emotion="sleepy"), None),
          ("Listening to music", IDLE, FrameContext(music=True, beat=1.0, emotion="happy"), None)]
W, H = 1640, 470
img, p = desktop(W, H)
for i, (name, st, ctx, react) in enumerate(reacts):
    for ch, dx in ((seed2, -58), (fut, 58)):
        for _ in range(25):          # settle the previous reaction
            ch.step(1 / 30, 0, 0, 0, 0, IDLE, FrameContext())
        if react:
            ch.react(*react)
        fr = run(ch, st, FrameContext(**{k: getattr(ctx, k) for k in ("emotion", "pet_level", "music", "beat", "expr")}),
                 5 if react else 20)
        x = 120 + i * 233 + dx
        stand(p, fr, x, H - TASKBAR, ch.foot_offset())
    label(p, name, 120 + i * 233, 30, 10)
save(img, p, "touch.png")

# 5. lifted by a limb (struggling)
W, H = 1180, 560
img, p = desktop(W, H)
lifts = [("By the hand", "hand_r"), ("By the leg", "leg_l"), ("By the body", "")]
for i, (name, grab) in enumerate(lifts):
    for ch, dx in ((seed2, -95), (fut, 95)):
        ctx = FrameContext(emotion="struggling", expr=build("side_glance", "startled"), grab=grab,
                           grab_pt=(ch.W * (0.42 if grab == "hand_r" else 0.5), ch.H * 0.07), struggle=0.85)
        for _ in range(30):
            ctx.struggle = 0.85
            ctx.t += 1 / 30
            ch.step(1 / 30, 0, 0, 0, 0, DRAG, ctx)
        fr = ch.render(DRAG, ctx, DPR)
        x = 195 + i * 395 + dx
        p.drawImage(QPointF(x - fr.width() / DPR / 2, 60), fr)
        # the "cursor" holding her
        px, py = x - fr.width() / DPR / 2 + ctx.grab_pt[0], 60 + ctx.grab_pt[1]
        p.setPen(QColor("white"))
        p.setBrush(QColor(30, 30, 30))
        cur = QPainterPath()
        cur.moveTo(px, py)
        cur.lineTo(px, py + 16)
        cur.lineTo(px + 4, py + 12)
        cur.lineTo(px + 8, py + 19)
        cur.lineTo(px + 10, py + 18)
        cur.lineTo(px + 7, py + 11)
        cur.lineTo(px + 12, py + 11)
        cur.closeSubpath()
        p.drawPath(cur)
    label(p, name, 195 + i * 395, 30)
    # reset so the next lift starts clean
    for ch in (seed2, fut):
        for _ in range(20):
            ch.step(1 / 30, 0, 0, 0, 0, IDLE, FrameContext())
save(img, p, "lifting.png")

# 6. live picture physics frames
W, H = 900, 400
img, p = desktop(W, H)
frames = [("Idle", IDLE, FrameContext(), None, 0), ("Blink + blush", IDLE, FrameContext(blink=True, emotion="shy"), None, 0),
          ("Headpat", PET, FrameContext(emotion="happy", pet_level=1.0), ("head", "pet"), 0),
          ("Swinging (drag)", DRAG, FrameContext(emotion="surprised", tilt=-14), None, 22000)]
for i, (name, st, ctx, react, ax) in enumerate(frames):
    if react:
        pic.react(*react)
    fr = run(pic, st, ctx, 8, ax=ax)
    x = 115 + i * 225
    stand(p, fr, x, H - TASKBAR, pic.foot_offset())
    label(p, name, x, 28)
save(img, p, "live_picture.png")

# 7. chat panel + bubble UI
from waifu.ui.chat import ChatPanel  # noqa: E402

chat = ChatPanel("Zenya")
chat.set_status("☺ ♥72")
chat.add_message("You", "hi! what are you up to?", "you")
chat.add_message("Zenya", "Just watching you code~ That function looks tidy! Want me to look at your screen?", "her")
chat.add_message("You", "look at me, what am I holding?", "you")
chat.add_message("Zenya", "Ooh, a cup of tea! Is that milk tea? Don't let it get cold~", "her")
chat.add_message("", "Remembered: They like milk tea", "system")
chat.input.setText("good night!")
chat.resize(chat.W, chat.H)
shot = chat.grab().toImage()
W, H = 760, 520
img, p = desktop(W, H)
fr = run(seed2, IDLE, FrameContext(emotion="happy", expr=build("direct", "gentle_smile"), mouth=0.6), 10)
stand(p, fr, 170, H - TASKBAR, seed2.foot_offset())
bubble(p, "Ooh, a cup of tea! Don't let it get cold~", 170, 95)
p.drawImage(QRectF(350, 30, chat.W, chat.H), shot)
save(img, p, "chat.png")
print("done ->", OUT)
