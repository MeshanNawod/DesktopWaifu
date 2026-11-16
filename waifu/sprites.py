"""Character rendering.

Two providers share one interface:
  * ProceduralSprites - a vector chibi girl drawn with QPainter (works with zero assets)
  * SheetSprites      - your own PNG sprite sheets described by a sheet.json

Interface:
  size() -> QSize                       logical window size (already scaled)
  foot_offset() -> int                  transparent pixels below her feet
  head_rect(state) -> QRectF            pet zone in logical, un-mirrored coords
  render(state, ctx, dpr) -> QImage     current frame
  icon() -> QPixmap                     tray icon
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF, QTransform)

log = logging.getLogger(__name__)

# Animation state names (shared with character.py)
IDLE, WALK, SIT, SLEEP, DRAG, FALL, PET, REACT = (
    "idle", "walk", "sit", "sleep", "drag", "fall", "pet", "react")
KNOCK = "knocked"   # knocked over: lies dizzy, then gets back up


@dataclass
class FrameContext:
    t: float = 0.0              # global clock (s)
    state_t: float = 0.0        # time in current state (s)
    emotion: str = "neutral"
    mouth: float = 0.0          # 0..1 lip-sync openness
    blink: bool = False
    facing_left: bool = False
    tilt: float = 0.0           # degrees, used while dragged
    vx: float = 0.0
    pet_level: float = 0.0      # 0..1, hearts while > 0
    music: bool = False         # music is playing
    beat: float = 0.0           # 1.0 on a beat, decays to 0
    expr: object = None         # expressions.Expr: current cell of the 64-expression matrix
    grab: str = ""              # limb she is held by while dragged: hand_l / hand_r / leg_l / leg_r
    grab_pt: tuple = (0.0, 0.0) # where the cursor holds her, in window coordinates
    struggle: float = 0.0       # 0..1 wriggling intensity while lifted
    knock: float = 0.0          # 0..1 how far she has fallen over (knocked down)
    knock_dir: float = 1.0      # which way she tips while falling
    user_yaw: float = 0.0       # how far you've turned her (mouse wheel / Pose menu), degrees
    spin: float = 0.0           # extra spin while she twirls, degrees
    jump: bool = False          # she jumped on purpose (cute "yay!" pose in the air)
    user_pitch: float = 0.0     # lean forward / back (right-drag up and down), degrees
    holding: str = ""           # pet / toy she's hugging: teddy, cat, puppy, bunny...
    angry: float = 0.0          # 0..1 how angry she is (arms crossed, turns away, steam, vein)
    pose: str = ""              # pose you asked for: kneel, bow, hands_up, dance... (see poses.py)
    lie: float = 0.0            # 0..1 lying down (you told her to lie down)


# --------------------------------------------------------------------------- procedural
BASE_W, BASE_H, GROUND = 150, 190, 186

CLASSIC = {
    "hair": QColor("#f4a9c6"), "hair_dark": QColor("#d77fa2"), "hair_line": QColor("#b9617f"),
    "skin": QColor("#ffe8db"), "skin_line": QColor("#e8b9a3"),
    "dress": QColor("#5d6fe3"), "dress_dark": QColor("#3f4fb5"),
    "collar": QColor("#ffffff"), "ribbon": QColor("#ff5b78"),
    "eye": QColor("#8a5ce6"), "eye_dark": QColor("#3a1f78"), "line": QColor("#3b2a3f"),
    "sock": QColor("#ffffff"), "sock_line": QColor("#dfe3f0"), "shoe": QColor("#3d3554"),
    "blush": QColor(255, 110, 150, 90), "mouth": QColor("#c4455f"), "tongue": QColor("#ff9aa8"),
    "clip": QColor("#ffd24a"), "heart": QColor("#ff5c8a"),
}

# Long dark-brown hair, red-brown eyes, red scarf + fingerless gloves, black outfit.
SCARF = {
    "hair": QColor("#4b3428"), "hair_dark": QColor("#3a281f"), "hair_line": QColor("#24170f"),
    "skin": QColor("#fff0e6"), "skin_line": QColor("#e6bfae"),
    "dress": QColor("#34343d"), "dress_dark": QColor("#1c1c22"),
    "collar": QColor("#ffffff"), "ribbon": QColor("#ff5b78"),
    "eye": QColor("#c9563c"), "eye_dark": QColor("#5c1a12"), "line": QColor("#2a1a14"),
    "sock": QColor("#fff0e6"), "sock_line": QColor("#e6bfae"), "shoe": QColor("#1e1b22"),
    "blush": QColor(255, 110, 130, 90), "mouth": QColor("#b8404f"), "tongue": QColor("#ff9aa8"),
    "clip": QColor("#ffd24a"), "heart": QColor("#ff5c7a"),
    "scarf": QColor("#c23b4a"), "scarf_dark": QColor("#8e2433"), "glove": QColor("#d24d5c"),
    "belt": QColor("#141418"), "buckle": QColor("#c9c9d1"),
}

STYLES = {"scarf": SCARF, "classic": CLASSIC}
C = dict(SCARF)   # active palette, swapped in render()


def _pen(color: QColor, w: float) -> QPen:
    return QPen(color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _heart(p: QPainter, x: float, y: float, s: float, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(x, y + s * 0.3)
    path.cubicTo(x, y, x - s * 0.5, y, x - s * 0.5, y + s * 0.3)
    path.cubicTo(x - s * 0.5, y + s * 0.6, x, y + s * 0.8, x, y + s)
    path.cubicTo(x, y + s * 0.8, x + s * 0.5, y + s * 0.6, x + s * 0.5, y + s * 0.3)
    path.cubicTo(x + s * 0.5, y, x, y, x, y + s * 0.3)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def draw_steam(p: QPainter, x: float, y: float, t: float, scale: float = 1.0) -> None:
    """Little puffs of steam rising from her head: extreme embarrassment."""
    p.save()
    for i in range(3):
        ph = (t * 1.1 + i / 3) % 1.0
        col = QColor(255, 255, 255, int(200 * (1 - ph)))
        p.setPen(_pen(QColor(255, 160, 190, int(160 * (1 - ph))), 1.2))
        p.setBrush(col)
        sx = (i - 1) * 12 * scale + math.sin(t * 3 + i) * 3
        r = (4 + ph * 5) * scale
        p.drawEllipse(QPointF(x + sx, y - ph * 26 * scale), r, r * 0.8)
    p.restore()


class ProceduralSprites:
    def __init__(self, scale: float = 1.0, style: str = "scarf"):
        self.scale = max(0.4, min(3.0, scale))
        self.style = style if style in STYLES else "scarf"
        self.palette = STYLES[self.style]

    def size(self) -> QSize:
        return QSize(round(BASE_W * self.scale), round(BASE_H * self.scale))

    def foot_offset(self) -> int:
        return round((BASE_H - GROUND) * self.scale)

    def head_rect(self, state: str) -> QRectF:
        drop = 24 if state in (SIT, SLEEP) else 0
        s = self.scale
        return QRectF((75 - 40) * s, (26 + drop) * s, 80 * s, 74 * s)

    def icon(self) -> QPixmap:
        img = self.render(IDLE, FrameContext(emotion="happy"), 1.0)
        s = self.scale
        crop = img.copy(round(25 * s), round(20 * s), round(100 * s), round(100 * s))
        return QPixmap.fromImage(crop.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))

    # -- drawing -------------------------------------------------------------
    def render(self, state: str, ctx: FrameContext, dpr: float) -> QImage:
        sz = self.size()
        img = QImage(round(sz.width() * dpr), round(sz.height() * dpr),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        C.clear()
        C.update(self.palette)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.scale, self.scale)
        if ctx.facing_left:
            p.translate(BASE_W, 0)
            p.scale(-1, 1)
        if ctx.spin:               # twirling: fake a 3D spin by squashing horizontally
            p.translate(BASE_W / 2, 0)
            p.scale(max(0.08, abs(math.cos(math.radians(ctx.spin)))), 1.0)
            p.translate(-BASE_W / 2, 0)
        if ctx.knock > 0:          # knocked over: flattened and tipped sideways
            p.translate(BASE_W / 2, GROUND)
            p.rotate(ctx.knock_dir * 25 * ctx.knock)
            p.scale(1.0 + 0.1 * ctx.knock, 1.0 - 0.55 * ctx.knock)
            p.translate(-BASE_W / 2, -GROUND)
        self._draw(p, state, ctx)
        p.end()
        return img

    def _draw(self, p: QPainter, state: str, ctx: FrameContext) -> None:
        t, cx = ctx.t, 75.0
        emo = ctx.emotion
        bob, drop, leg_a, tilt = 0.0, 0.0, 0.0, 0.0
        arm_l, arm_r = 12.0, -12.0
        sitting = state in (SIT, SLEEP)
        pivot = QPointF(cx, GROUND)

        if state == WALK:
            ph = t * 9.0
            bob = -abs(math.sin(ph)) * 3.0
            leg_a = math.sin(ph) * 22
            arm_l, arm_r = 10 - math.sin(ph) * 16, -10 - math.sin(ph) * 16
        elif state in (IDLE, REACT):
            bob = math.sin(t * 2.2) * 1.2
            arm_l, arm_r = 10 + math.sin(t * 2.2) * 2, -10 - math.sin(t * 2.2) * 2
            if emo in ("excited", "happy") and state == IDLE:
                wave = math.sin(t * 10) * 18
                arm_r = -150 + wave if emo == "excited" else arm_r
                arm_l = 150 - wave if emo == "excited" else arm_l
            if state == REACT:
                bob -= max(0.0, 6 - ctx.state_t * 20)  # little hop
                arm_l, arm_r = 40, -40
        elif sitting:
            drop = 24
            bob = math.sin(t * 1.6) * 0.8
            arm_l, arm_r = 25, -25
            if state == SLEEP:
                tilt = 6 + math.sin(t * 1.2) * 1.5
        elif state == DRAG:
            pivot = QPointF(cx, 66)
            tilt = ctx.tilt
            leg_a = math.sin(t * 7) * 12 - max(-25.0, min(25.0, ctx.vx * 0.02))
            arm_l, arm_r = 150 + math.sin(t * 9) * 8, -150 - math.sin(t * 9) * 8
        elif state == FALL:
            leg_a = math.sin(t * 14) * 18
            arm_l, arm_r = 140, -140
        elif state == PET:
            bob = math.sin(t * 3) * 1.5
            tilt = math.sin(t * 4) * 4
            arm_l, arm_r = 4, -4

        p.save()
        if tilt:
            p.translate(pivot)
            p.rotate(tilt)
            p.translate(-pivot)
        p.translate(0, bob)

        # Face/upper body offset when sitting
        up = drop
        scarf = self.style == "scarf"

        # 1. long back hair
        p.setPen(_pen(C["hair_line"], 1.2))
        p.setBrush(C["hair_dark"])
        if scarf:  # very long, straight, tapering to soft points
            bot = 166 - up * 0.2
            hair = QPainterPath()
            hair.moveTo(cx - 40, 62 + up)
            hair.quadTo(cx - 47, 110 + up, cx - 37, bot)
            hair.lineTo(cx - 26, bot - 6)
            hair.lineTo(cx - 14, bot - 2)
            hair.lineTo(cx, bot - 8)
            hair.lineTo(cx + 14, bot - 2)
            hair.lineTo(cx + 26, bot - 6)
            hair.lineTo(cx + 37, bot)
            hair.quadTo(cx + 47, 110 + up, cx + 40, 62 + up)
            hair.closeSubpath()
            p.drawPath(hair)
        else:
            p.drawRoundedRect(QRectF(cx - 43, 58 + up, 86, 84 - up * 0.6), 30, 30)

        # 2. legs
        p.setPen(_pen(C["skin_line"], 1.0))
        if sitting:
            for i, hx in enumerate((cx - 7, cx + 6)):
                swing = math.sin(t * 2.0 + i * 1.7) * 6
                self._leg(p, hx, 150 + up - 4, -78 + swing, short=True)
        else:
            self._leg(p, cx - 7, 148, leg_a)
            self._leg(p, cx + 7, 148, -leg_a)

        # 3. dress + collar
        dress = QPainterPath()
        hem = up * 0.75
        dress.moveTo(cx - 17, 106 + up)
        dress.lineTo(cx + 17, 106 + up)
        if scarf:  # fitted shirt, belted waist, flared pleated skirt
            dress.lineTo(cx + 14, 130 + up)
            dress.lineTo(cx + 33, 155 + hem)
            dress.quadTo(cx, 161 + hem, cx - 33, 155 + hem)
            dress.lineTo(cx - 14, 130 + up)
        else:
            dress.lineTo(cx + 31, 153 + hem)
            dress.quadTo(cx, 161 + hem, cx - 31, 153 + hem)
        dress.closeSubpath()
        grad = QLinearGradient(0, 106 + up, 0, 160 + up)
        grad.setColorAt(0, C["dress"])
        grad.setColorAt(1, C["dress_dark"])
        p.setPen(_pen(C["dress_dark"].darker(130), 1.4))
        p.setBrush(grad)
        p.drawPath(dress)
        if scarf:
            self._scarf_outfit(p, cx, up, hem, t)
        else:
            self._sailor_collar(p, cx, up)

        # 4. arms
        self._arm(p, cx - 16, 110 + up, arm_l)
        self._arm(p, cx + 16, 110 + up, arm_r)
        if scarf:
            self._scarf_wrap(p, cx, up)

        # 5. head
        p.setPen(_pen(C["skin_line"], 1.0))
        p.setBrush(C["skin"])
        p.drawRect(QRectF(cx - 5, 96 + up, 10, 6 if scarf else 12))
        p.drawEllipse(QPointF(cx, 66 + up), 38, 37)

        # 6. face
        self._face(p, cx, up, ctx, state)

        # 7. bangs + side locks
        p.setPen(_pen(C["hair_line"], 1.2))
        p.setBrush(C["hair"])
        bangs = QPainterPath()
        bangs.moveTo(cx - 41, 72 + up)
        bangs.arcTo(QRectF(cx - 41, 24 + up, 82, 82), 180, -180)
        if scarf:  # blunt, straight-cut fringe
            pts = [(39, 74), (32, 60), (25, 63), (17, 58), (9, 63), (1, 58), (-7, 63),
                   (-15, 58), (-23, 63), (-30, 59), (-36, 70)]
        else:
            pts = [(38, 72), (31, 50), (24, 63), (14, 46), (4, 61), (-6, 45), (-16, 61), (-26, 49), (-34, 66)]
        for dx, y in pts:
            bangs.lineTo(cx + dx, y + up)
        bangs.lineTo(cx - 41, 72 + up)
        bangs.closeSubpath()
        p.drawPath(bangs)
        lock_end = 126 if scarf else 108
        for side in (-1, 1):
            lock = QPainterPath()
            lock.moveTo(cx + side * 40, 58 + up)
            lock.quadTo(cx + side * 46, 90 + up, cx + side * 42, lock_end + up)
            lock.quadTo(cx + side * 33, 96 + up, cx + side * 31, 64 + up)
            lock.closeSubpath()
            p.drawPath(lock)
        if scarf:  # soft shine band
            shine = QColor(255, 255, 255, 40)
            p.setPen(_pen(shine, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - 30, 34 + up, 60, 40), 30 * 16, 120 * 16)

        # 8. ahoge + hair clip (asymmetric so flipping reads)
        if not scarf:
            sway = math.sin(t * 3.0) * 3
            ah = QPainterPath()
            ah.moveTo(cx + 2, 30 + up)
            ah.cubicTo(cx + 2, 16 + up, cx + 14 + sway, 8 + up, cx + 20 + sway, 17 + up)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(_pen(C["hair_line"], 3.6))
            p.drawPath(ah)
            p.setPen(_pen(C["hair"], 2.2))
            p.drawPath(ah)
            self._star(p, cx + 27, 44 + up, 6.5)

        # 9. effects
        if emo == "angry":
            p.setPen(_pen(QColor("#ff3b3b"), 2.2))
            x0, y0 = cx + 30, 34 + up
            for a, b in (((-4, -1), (-1, -1)), ((-1, -4), (-1, -1)), ((4, 1), (1, 1)), ((1, 4), (1, 1))):
                p.drawLine(QPointF(x0 + a[0], y0 + a[1]), QPointF(x0 + b[0], y0 + b[1]))
        p.restore()
        self._overlay_effects(p, state, ctx, cx, drop)

    def _sailor_collar(self, p: QPainter, cx: float, up: float) -> None:
        collar = QPainterPath()
        collar.moveTo(cx - 18, 106 + up)
        collar.lineTo(cx + 18, 106 + up)
        collar.lineTo(cx, 125 + up)
        collar.closeSubpath()
        p.setPen(_pen(QColor("#d6dbf5"), 1.0))
        p.setBrush(C["collar"])
        p.drawPath(collar)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C["ribbon"])
        p.drawPolygon(QPolygonF([QPointF(cx, 119 + up), QPointF(cx - 9, 114 + up), QPointF(cx - 8, 125 + up)]))
        p.drawPolygon(QPolygonF([QPointF(cx, 119 + up), QPointF(cx + 9, 114 + up), QPointF(cx + 8, 125 + up)]))
        p.drawEllipse(QPointF(cx, 119.5 + up), 3, 3)

    def _scarf_outfit(self, p: QPainter, cx: float, up: float, hem: float, t: float) -> None:
        # skirt pleats
        p.setPen(_pen(QColor(0, 0, 0, 110), 1.0))
        for k in (-2, -1, 0, 1, 2):
            p.drawLine(QPointF(cx + k * 5.5, 133 + up), QPointF(cx + k * 12, 157 + hem))
        # shirt buttons
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#5a5a66"))
        for y in (116, 122):
            p.drawEllipse(QPointF(cx, y + up), 1.3, 1.3)
        # belt + buckle
        p.setBrush(C["belt"])
        p.drawRect(QRectF(cx - 15, 127 + up, 30, 4.5))
        p.setPen(_pen(C["buckle"], 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(cx - 3, 126.5 + up, 6, 5.5))

    def _scarf_wrap(self, p: QPainter, cx: float, up: float) -> None:
        # hanging tail on one side (also makes left/right facing readable)
        tail = QPainterPath()
        tail.moveTo(cx + 5, 105 + up)
        tail.lineTo(cx + 17, 105 + up)
        tail.lineTo(cx + 21, 141 + up)
        tail.lineTo(cx + 9, 142 + up)
        tail.closeSubpath()
        p.setPen(_pen(C["scarf_dark"], 1.2))
        p.setBrush(C["scarf"])
        p.drawPath(tail)
        for i in range(4):  # fringe
            x = cx + 10.5 + i * 3.2
            p.drawLine(QPointF(x, 141.5 + up), QPointF(x + 0.6, 146 + up))
        # wrap around the neck
        p.setBrush(C["scarf"])
        p.drawRoundedRect(QRectF(cx - 21, 96 + up, 42, 15), 7, 7)
        p.setPen(_pen(C["scarf_dark"], 1.0))
        for a, b in (((-14, 99), (-10, 109)), ((1, 98), (4, 110)), ((12, 99), (15, 108))):
            p.drawLine(QPointF(cx + a[0], a[1] + up), QPointF(cx + b[0], b[1] + up))

    def _overlay_effects(self, p: QPainter, state: str, ctx: FrameContext, cx: float, drop: float) -> None:
        t = ctx.t
        if ctx.pet_level > 0:
            for i in range(3):
                ph = (t * 0.7 + i / 3) % 1.0
                col = QColor(C["heart"])
                col.setAlphaF(max(0.0, min(1.0, ctx.pet_level)) * (1 - ph))
                _heart(p, cx + (i - 1) * 24 + math.sin(t * 3 + i) * 4, 26 - ph * 26 + drop, 11, col)
        if state == SLEEP:
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            for i in range(2):
                ph = (t * 0.4 + i / 2) % 1.0
                col = QColor("#7b8cff")
                col.setAlphaF(1 - ph)
                p.setPen(col)
                p.drawText(QPointF(cx + 30 + ph * 14, 40 - ph * 30 + drop), "z")

    def _leg(self, p: QPainter, x: float, y: float, angle: float, short: bool = False) -> None:
        length = 30 if short else 32
        p.save()
        p.translate(x, y)
        p.rotate(angle)
        p.setPen(_pen(C["sock_line"], 1.0))
        p.setBrush(C["sock"])
        p.drawRoundedRect(QRectF(-5.5, 0, 11, length), 5, 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C["shoe"])
        p.drawRoundedRect(QRectF(-6.5, length - 4, 15, 9), 4, 4)
        p.restore()

    def _arm(self, p: QPainter, x: float, y: float, angle: float) -> None:
        p.save()
        p.translate(x, y)
        p.rotate(angle)
        p.setPen(_pen(C["skin_line"], 1.0))
        p.setBrush(C["skin"])
        p.drawRoundedRect(QRectF(-4, 6, 8, 20), 4, 4)
        p.drawEllipse(QPointF(0, 27), 4.8, 4.8)
        gloves = "glove" in C
        if gloves:  # fingerless gloves
            p.setPen(_pen(C["scarf_dark"], 0.9))
            p.setBrush(C["glove"])
            p.drawRoundedRect(QRectF(-4.6, 19, 9.2, 8), 3, 3)
            p.drawEllipse(QPointF(0, 27.5), 5, 4.2)
        p.setPen(_pen(C["dress_dark"].darker(120), 1.0))
        p.setBrush(C["dress"])
        p.drawRoundedRect(QRectF(-5.5, -3, 11, 16 if gloves else 13), 5, 5)
        if gloves:  # rolled-up cuff
            p.setBrush(C["dress"].lighter(135))
            p.drawRoundedRect(QRectF(-5.8, 10, 11.6, 4), 2, 2)
        p.restore()

    def _star(self, p: QPainter, x: float, y: float, r: float) -> None:
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.45
            pts.append(QPointF(x + math.cos(a) * rr, y + math.sin(a) * rr))
        p.setPen(_pen(QColor("#e0a800"), 1.0))
        p.setBrush(C["clip"])
        p.drawPolygon(QPolygonF(pts))

    def _face(self, p: QPainter, cx: float, up: float, ctx: FrameContext, state: str) -> None:
        emo = ctx.emotion
        ey = 76 + up
        if state == SLEEP or ctx.blink:
            kind = "closed"
        elif state == PET or emo in ("happy",) and ctx.mouth < 0.05 and int(ctx.t * 0.5) % 3 == 0:
            kind = "happy"
        elif state in (DRAG, FALL, REACT) or emo == "surprised":
            kind = "wide"
        elif emo == "sleepy":
            kind = "half"
        elif emo == "excited":
            kind = "happy" if int(ctx.t * 2) % 4 == 0 else "open"
        else:
            kind = "open"

        # blush
        strong = emo in ("shy", "happy", "excited") or state == PET
        p.setPen(Qt.PenStyle.NoPen)
        b = QColor(C["blush"])
        b.setAlpha(150 if strong else 60)
        p.setBrush(b)
        p.drawEllipse(QPointF(cx - 25, ey + 12), 7, 3.6)
        p.drawEllipse(QPointF(cx + 25, ey + 12), 7, 3.6)
        if strong:
            p.setPen(_pen(QColor(255, 90, 130, 160), 1.0))
            for s in (-1, 1):
                for k in (-3, 0, 3):
                    p.drawLine(QPointF(cx + s * 25 + k - 1, ey + 14), QPointF(cx + s * 25 + k + 1, ey + 10))

        for ex in (cx - 15, cx + 15):
            self._eye(p, ex, ey, kind)

        # brows
        p.setPen(_pen(C["hair_line"], 1.8))
        for s in (-1, 1):
            x = cx + s * 15
            by = ey - 15
            if emo == "angry":
                p.drawLine(QPointF(x + s * 6, by - 3), QPointF(x - s * 6, by + 1))
            elif emo == "sad":
                p.drawLine(QPointF(x - s * 6, by - 2), QPointF(x + s * 6, by + 1))
            elif emo == "surprised" or kind == "wide":
                p.drawLine(QPointF(x - 6, by - 4), QPointF(x + 6, by - 4))

        # mouth
        my = 93 + up
        if ctx.mouth > 0.08:
            w, h = 7 + ctx.mouth * 3, 2 + ctx.mouth * 7
            p.setPen(_pen(C["line"], 1.2))
            p.setBrush(C["mouth"])
            p.drawEllipse(QRectF(cx - w / 2, my - h / 2, w, h))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(C["tongue"])
            p.drawEllipse(QRectF(cx - w / 3, my, w / 1.5, h / 2.2))
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_pen(C["line"], 1.5))
        path = QPainterPath()
        if emo in ("happy", "excited") or state == PET:
            path.moveTo(cx - 5, my - 1)
            path.quadTo(cx, my + 5, cx + 5, my - 1)
        elif emo == "sad":
            path.moveTo(cx - 4, my + 2)
            path.quadTo(cx, my - 2, cx + 4, my + 2)
        elif emo == "shy":  # little "w" mouth
            path.moveTo(cx - 5, my)
            path.quadTo(cx - 2.5, my + 3, cx, my)
            path.quadTo(cx + 2.5, my + 3, cx + 5, my)
        elif emo == "surprised" or state in (DRAG, FALL, REACT):
            p.setBrush(C["mouth"])
            p.drawEllipse(QPointF(cx, my + 1), 2.6, 3.2)
            return
        elif emo == "angry":
            path.moveTo(cx - 4, my + 1)
            path.lineTo(cx + 4, my)
        else:
            path.moveTo(cx - 3, my)
            path.quadTo(cx, my + 2, cx + 3, my)
        p.drawPath(path)

    def _eye(self, p: QPainter, x: float, y: float, kind: str) -> None:
        line = C["line"]
        if kind == "closed":
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(_pen(line, 2.2))
            path = QPainterPath()
            path.moveTo(x - 7, y)
            path.quadTo(x, y + 5, x + 7, y)
            p.drawPath(path)
            return
        if kind == "happy":
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(_pen(line, 2.4))
            path = QPainterPath()
            path.moveTo(x - 7, y + 2)
            path.quadTo(x, y - 8, x + 7, y + 2)
            p.drawPath(path)
            return
        wide = kind == "wide"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x - 7.5, y - 10, 15, 19 if wide else 18))
        ir = 4.0 if wide else 5.8
        grad = QLinearGradient(0, y - 8, 0, y + 8)
        grad.setColorAt(0, C["eye_dark"])
        grad.setColorAt(1, C["eye"])
        p.setBrush(grad)
        p.drawEllipse(QRectF(x - ir, y - 8 + (2 if wide else 0), ir * 2, 15 if not wide else 11))
        p.setBrush(C["eye_dark"])
        p.drawEllipse(QRectF(x - 2.6, y - 3, 5.2, 7))
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(x - 2, y - 4), 2.4, 2.4)
        p.drawEllipse(QPointF(x + 2, y + 3), 1.1, 1.1)
        if kind == "half":
            p.setBrush(C["skin"])
            p.drawRect(QRectF(x - 9, y - 12, 18, 9))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_pen(line, 2.4))
        path = QPainterPath()
        top = y - (3 if kind == "half" else 9)
        path.moveTo(x - 8.5, top + 3)
        path.quadTo(x, top - 3, x + 8.5, top + 3)
        p.drawPath(path)


# --------------------------------------------------------------------------- sprite sheets
class SheetSprites:
    """Loads a folder containing sheet.json + PNG strips. See assets/sprites/README.md."""

    def __init__(self, folder: str | Path, scale: float, dpr: float):
        self.folder = Path(folder)
        meta = json.loads((self.folder / "sheet.json").read_text(encoding="utf-8"))
        self.fw, self.fh = int(meta["frame_width"]), int(meta["frame_height"])
        self.scale = scale * float(meta.get("scale", 1.0))
        self.faces_left = meta.get("faces", "right") == "left"
        self._foot = int(meta.get("foot_offset", 0))
        self._head = meta.get("head", [self.fw * 0.2, 0, self.fw * 0.6, self.fh * 0.4])
        self.dpr = dpr
        self.anims: dict[str, dict] = {}
        out_w, out_h = round(self.fw * self.scale * dpr), round(self.fh * self.scale * dpr)
        for name, a in meta["animations"].items():
            sheet = QImage(str(self.folder / a["file"]))
            if sheet.isNull():
                log.warning("Missing sprite file %s", a["file"])
                continue
            n = int(a.get("frames", max(1, sheet.width() // self.fw)))
            cols = int(a.get("columns", n))
            row0 = int(a.get("row", 0))
            frames, flipped = [], []
            for i in range(n):
                fx, fy = (i % cols) * self.fw, (row0 + i // cols) * self.fh
                f = sheet.copy(fx, fy, self.fw, self.fh).scaled(
                    out_w, out_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                f.setDevicePixelRatio(dpr)
                frames.append(f)
                m = f.transformed(QTransform().scale(-1, 1))
                m.setDevicePixelRatio(dpr)
                flipped.append(m)
            self.anims[name] = {"frames": frames, "flipped": flipped,
                                "fps": float(a.get("fps", 8)), "loop": bool(a.get("loop", True))}
        if "idle" not in self.anims:
            raise ValueError("sprite pack must define an 'idle' animation")

    def size(self) -> QSize:
        return QSize(round(self.fw * self.scale), round(self.fh * self.scale))

    def foot_offset(self) -> int:
        return round(self._foot * self.scale)

    def head_rect(self, state: str) -> QRectF:
        x, y, w, h = self._head
        s = self.scale
        return QRectF(x * s, y * s, w * s, h * s)

    def icon(self) -> QPixmap:
        return QPixmap.fromImage(self.anims["idle"]["frames"][0]).scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def render(self, state: str, ctx: FrameContext, dpr: float) -> QImage:
        names = []
        if ctx.mouth > 0.3 and state in (IDLE, PET, SIT):
            names += [f"talk_{ctx.emotion}", "talk"]
        names += [f"{state}_{ctx.emotion}", state, "idle"]
        if state == SLEEP:
            names.insert(0, "sleep")
        anim = next(self.anims[n] for n in names if n in self.anims)
        n = len(anim["frames"])
        idx = int(ctx.state_t * anim["fps"])
        idx = idx % n if anim["loop"] else min(idx, n - 1)
        mirror = ctx.facing_left != self.faces_left
        return (anim["flipped"] if mirror else anim["frames"])[idx]


DEFAULT_IMAGE_PACK = Path(__file__).resolve().parent.parent / "assets" / "characters" / "scarf_girl"


MODELS_DIR = Path(__file__).resolve().parent.parent / "assets" / "models"


def default_vrm() -> str:
    found = sorted(MODELS_DIR.glob("*.vrm")) + sorted(MODELS_DIR.glob("*/*.fbx"))
    return str(found[0]) if found else ""


def list_characters() -> list[tuple[str, str, str]]:
    """(label, style, model path) for every character the user can pick."""
    import json
    out = []
    for f in sorted(MODELS_DIR.glob("*.vrm")) + sorted(MODELS_DIR.glob("*/*.vrm")):
        out.append((f"3D: {f.stem}", "vrm", str(f)))
        out.append((f"3D: {f.stem} 2 (realistic, interactive)", "vrm", str(f) + "#v2"))
    for f in sorted(MODELS_DIR.glob("*.variant.json")):     # cute / custom variants of a base model
        try:
            name = json.loads(f.read_text(encoding="utf-8")).get("name", f.stem)
        except Exception:  # noqa: BLE001
            continue
        out.insert(0, (f"3D: {name}", "vrm", str(f)))
    for f in sorted(MODELS_DIR.glob("*.fbx")) + sorted(MODELS_DIR.glob("*/*.fbx")):
        name = f.stem
        meta = f.with_name("character.json")
        if meta.exists():
            try:
                name = json.loads(meta.read_text(encoding="utf-8")).get("name", name)
            except Exception:  # noqa: BLE001
                pass
        out.append((f"3D: {name}", "vrm", str(f)))
    out += [("Live picture: scarf girl", "image", ""), ("Drawn: scarf girl", "scarf", ""),
            ("Drawn: classic pink", "classic", "")]
    return out


def load_sprites(sprite_pack: str, scale: float, dpr: float, style: str = "vrm", image_pack: str = "",
                 vrm_path: str = ""):
    if sprite_pack:
        try:
            return SheetSprites(sprite_pack, scale, dpr)
        except Exception:
            log.exception("Failed to load sprite pack %s; using built-in character", sprite_pack)
    if style == "vrm":
        path = vrm_path or default_vrm()
        try:
            if path.lower().endswith(".variant.json"):
                import json as _json
                from .vrm_character import VrmCharacter
                var = _json.loads(Path(path).read_text(encoding="utf-8"))
                return VrmCharacter(Path(path).parent / var["base"], scale, dpr, variant=var)
            if path.lower().endswith(".fbx"):
                from .mesh_character import MeshCharacter
                return MeshCharacter(path, scale, dpr)
            from .vrm_character import VrmCharacter
            enhanced = path.endswith("#v2")
            return VrmCharacter(path.removesuffix("#v2"), scale, dpr, enhanced=enhanced)
        except Exception:
            log.exception("Failed to load VRM model %r; using live picture", path)
            style = "image"
    if style == "image":
        try:
            from .live_image import LiveImageCharacter
            return LiveImageCharacter(image_pack or DEFAULT_IMAGE_PACK, scale, dpr)
        except Exception:
            log.exception("Failed to load live picture character; using drawn character")
            style = "scarf"
    return ProceduralSprites(scale, style)
