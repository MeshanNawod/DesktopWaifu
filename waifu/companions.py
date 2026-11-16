"""Pets and toys that live on your desktop with her.

Every pet / toy is its own little transparent always-on-top window with physics (gravity, bouncing,
rolling, throwing). You can:

  * pick them up and throw them (drag + release), click / double-click / stroke them,
  * drop a toy or pet on her  -> she hugs the teddy / cuddles the pet / kicks the ball back,
  * throw something hard at her -> bonk! she gets angry,
  * hurt a pet (hold it squeezed, or slam it into the floor) -> she gets angry at you.

On their own:
  * pets follow her around, chase the ball / yarn, nuzzle her, nap when she naps,
    the puppy plays fetch (brings the ball back to her),
  * now and then she walks over to play: kicks the ball, pets her pet, hugs her teddy.
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

GRAVITY = 2200.0
PETS = ("cat", "puppy", "bunny")
TOYS = ("ball", "teddy", "yarn")
LABELS = {"cat": "🐱 Mochi the kitten", "puppy": "🐶 Kuri the puppy", "bunny": "🐰 Usa the bunny",
          "ball": "⚽ Bouncy ball", "teddy": "🧸 Teddy bear", "yarn": "🧶 Yarn ball"}
NAMES = {"cat": "Mochi", "puppy": "Kuri", "bunny": "Usa", "ball": "the ball", "teddy": "Teddy", "yarn": "the yarn ball"}

SPECIES = {
    "cat":   {"fur": "#f7f2ea", "patch": "#f0b46c", "belly": "#ffffff", "inner": "#ffb7c8", "line": "#6b5148",
              "collar": "#ff5b78", "sounds": ["Nya~", "Mew!", "Purrr~", "Mrrp?", "Nyaa!"], "likes": ("yarn", "ball")},
    "puppy": {"fur": "#eca65e", "patch": "#eca65e", "belly": "#fff4e4", "inner": "#ffc9a8", "line": "#6a4024",
              "collar": "#4f8cff", "sounds": ["Woof!", "Arf arf!", "Wan!", "*wag wag*", "Yip!"], "likes": ("ball",)},
    "bunny": {"fur": "#ffffff", "patch": "#f4e9f4", "belly": "#ffffff", "inner": "#ffc0d4", "line": "#8a7a8a",
              "collar": "#ff8fc0", "sounds": ["*boing*", "*nose wiggle*", "Pyon!", "*thump*"], "likes": ("ball",)},
}


def _pen(c, w):
    pen = QPen(QColor(c), w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _heart(p: QPainter, x: float, y: float, s: float, col: QColor) -> None:
    path = QPainterPath()
    path.moveTo(x, y + s * 0.3)
    path.cubicTo(x - s * 0.5, y - s * 0.2, x - s * 0.1, y - s * 0.55, x, y - s * 0.2)
    path.cubicTo(x + s * 0.1, y - s * 0.55, x + s * 0.5, y - s * 0.2, x, y + s * 0.3)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col)
    p.drawPath(path)


# ------------------------------------------------------------------------------------------- drawing
def draw_ball(p: QPainter, cx: float, cy: float, r: float, angle: float = 0.0) -> None:
    p.save()
    p.translate(cx, cy)
    p.rotate(math.degrees(angle))
    cols = ["#ff5b78", "#ffd84a", "#4fb6ff", "#ffffff", "#6ee07a", "#ffffff"]
    rect = QRectF(-r, -r, 2 * r, 2 * r)
    p.setPen(Qt.PenStyle.NoPen)
    for i, c in enumerate(cols):
        p.setBrush(QColor(c))
        p.drawPie(rect, int(i * 60 * 16), 60 * 16)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(0, 0), r * 0.22, r * 0.22)
    p.restore()
    g = QRadialGradient(QPointF(cx - r * 0.35, cy - r * 0.4), r * 1.3)
    g.setColorAt(0, QColor(255, 255, 255, 120))
    g.setColorAt(0.4, QColor(255, 255, 255, 0))
    g.setColorAt(1, QColor(0, 0, 0, 60))
    p.setBrush(g)
    p.setPen(_pen("#7a4a5a", max(1.0, r * 0.07)))
    p.drawEllipse(QPointF(cx, cy), r, r)


def draw_yarn(p: QPainter, cx: float, cy: float, r: float, angle: float = 0.0) -> None:
    p.setPen(_pen("#d9487a", max(1.0, r * 0.1)))
    tail = QPainterPath()
    tail.moveTo(cx + r * 0.6, cy + r * 0.7)
    tail.cubicTo(cx + r * 1.2, cy + r * 1.1, cx + r * 1.5, cy + r * 0.4, cx + r * 1.9, cy + r * 0.95)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(tail)
    p.setBrush(QColor("#ff8fb6"))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.save()
    p.translate(cx, cy)
    p.rotate(math.degrees(angle))
    p.setPen(_pen("#e0598a", max(0.8, r * 0.08)))
    for k in range(-2, 3):
        p.drawArc(QRectF(-r * 1.6 + k * r * 0.35, -r, r * 2.0, r * 2.0), -60 * 16, 120 * 16)
    p.drawArc(QRectF(-r * 0.9, -r * 0.5, r * 1.8, r * 1.2), 200 * 16, 140 * 16)
    p.restore()


def draw_teddy(p: QPainter, cx: float, base: float, s: float, happy: bool = False) -> None:
    """A sitting teddy bear, `s` px tall, standing on y = base."""
    fur, dark, muzzle, line = QColor("#c98b5a"), QColor("#a86d42"), QColor("#f3d6b8"), "#5b3a26"
    lw = max(1.0, s * 0.035)
    p.setPen(_pen(line, lw))
    # feet, body, arms
    p.setBrush(fur)
    p.drawEllipse(QPointF(cx - s * 0.2, base - s * 0.1), s * 0.14, s * 0.1)
    p.drawEllipse(QPointF(cx + s * 0.2, base - s * 0.1), s * 0.14, s * 0.1)
    p.drawEllipse(QPointF(cx, base - s * 0.3), s * 0.3, s * 0.25)
    p.setBrush(muzzle)
    p.drawEllipse(QPointF(cx, base - s * 0.28), s * 0.17, s * 0.15)
    p.setBrush(fur)
    p.drawEllipse(QPointF(cx - s * 0.31, base - s * 0.34), s * 0.09, s * 0.15)
    p.drawEllipse(QPointF(cx + s * 0.31, base - s * 0.34), s * 0.09, s * 0.15)
    # head
    hy = base - s * 0.68
    for sx in (-1, 1):
        p.setBrush(fur)
        p.drawEllipse(QPointF(cx + sx * s * 0.21, hy - s * 0.2), s * 0.1, s * 0.1)
        p.setBrush(QColor("#f2b6b0"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx + sx * s * 0.21, hy - s * 0.2), s * 0.05, s * 0.05)
        p.setPen(_pen(line, lw))
    p.setBrush(fur)
    p.drawEllipse(QPointF(cx, hy), s * 0.27, s * 0.24)
    p.setBrush(muzzle)
    p.drawEllipse(QPointF(cx, hy + s * 0.08), s * 0.12, s * 0.09)
    p.setBrush(QColor(line))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(cx, hy + s * 0.05), s * 0.04, s * 0.03)
    for sx in (-1, 1):
        if happy:
            p.setPen(_pen(line, lw * 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx + sx * s * 0.1 - s * 0.04, hy - s * 0.06, s * 0.08, s * 0.07), 0, 180 * 16)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#2b1c14"))
            p.drawEllipse(QPointF(cx + sx * s * 0.1, hy - s * 0.03), s * 0.035, s * 0.04)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(cx + sx * s * 0.1 + s * 0.01, hy - s * 0.045), s * 0.012, s * 0.012)
    p.setPen(_pen(line, lw * 0.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(cx - s * 0.05, hy + s * 0.07, s * 0.05, s * 0.04), 180 * 16, 180 * 16)
    p.drawArc(QRectF(cx, hy + s * 0.07, s * 0.05, s * 0.04), 180 * 16, 180 * 16)
    # bow
    p.setPen(_pen("#c2305a", lw * 0.8))
    p.setBrush(QColor("#ff5b8a"))
    by = hy + s * 0.23
    for sx in (-1, 1):
        tri = QPainterPath()
        tri.moveTo(cx, by)
        tri.lineTo(cx + sx * s * 0.12, by - s * 0.06)
        tri.lineTo(cx + sx * s * 0.12, by + s * 0.06)
        tri.closeSubpath()
        p.drawPath(tri)
    p.drawEllipse(QPointF(cx, by), s * 0.035, s * 0.035)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 120, 140, 90))
    for sx in (-1, 1):
        p.drawEllipse(QPointF(cx + sx * s * 0.17, hy + s * 0.06), s * 0.05, s * 0.03)


def draw_pet(p: QPainter, species: str, cx: float, base: float, s: float, facing: float = 1.0,
             walk: float = 0.0, moving: bool = False, eyes: str = "open", tail: float = 0.0,
             pose: str = "stand", mouth_open: bool = False) -> None:
    """A chibi pet seen from the side (3/4 face), `s` px tall, feet on y = base, facing +x if facing > 0.

    eyes: open | happy | closed;  pose: stand | sit | sleep | held"""
    sp = SPECIES[species]
    fur, patch, belly, inner, line = (QColor(sp[k]) for k in ("fur", "patch", "belly", "inner", "line"))
    lw = max(1.0, s * 0.03)
    p.save()
    p.translate(cx, 0)
    p.scale(1 if facing >= 0 else -1, 1)
    p.translate(-cx, 0)
    if pose == "held":
        p.translate(cx, base - s * 0.5)
        p.rotate(-8 + math.sin(walk * 2) * 6)
        p.translate(-cx, -(base - s * 0.5))
    sleep = pose == "sleep"
    sit = pose == "sit"
    low = s * (0.12 if sleep else 0.05 if sit else 0.0)
    bx, by = cx - s * 0.06, base - s * 0.3 + low
    pen = _pen(line, lw)

    # tail (behind the body)
    p.setPen(_pen(line, lw))
    if species == "cat":
        path = QPainterPath()
        path.moveTo(bx - s * 0.28, by - s * 0.02)
        sway = math.sin(tail) * s * 0.14
        path.cubicTo(bx - s * 0.55, by - s * 0.05, bx - s * 0.5 + sway, by - s * 0.45,
                     bx - s * 0.38 + sway * 1.4, by - s * (0.25 if sleep else 0.55))
        p.setPen(_pen(line, s * 0.11))
        p.drawPath(path)
        p.setPen(_pen(patch, s * 0.08))
        p.drawPath(path)
    elif species == "puppy":
        wag = math.sin(tail * 2.5) * 0.25
        p.save()
        p.translate(bx - s * 0.27, by - s * 0.1)
        p.rotate(math.degrees(wag) - 30)
        p.setPen(pen)
        p.setBrush(fur)
        p.drawEllipse(QPointF(-s * 0.05, -s * 0.09), s * 0.09, s * 0.12)
        p.setBrush(belly)
        p.drawEllipse(QPointF(-s * 0.07, -s * 0.13), s * 0.045, s * 0.05)
        p.restore()
    else:
        p.setPen(pen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(bx - s * 0.3, by - s * 0.04), s * 0.09, s * 0.09)

    # legs
    p.setPen(pen)
    if not sleep:
        xs = (-0.21, -0.1, 0.1, 0.2)
        for i, lx in enumerate(xs):
            lift = max(0.0, math.sin(walk + i * math.pi * (1 if i % 2 else 0.5))) * s * 0.06 if moving else 0.0
            if pose == "held":
                lift = -math.sin(walk * 3 + i) * s * 0.03
            leg_h = s * (0.12 if sit and i < 2 else 0.2)
            x = bx + lx * s
            p.setBrush(fur if i % 2 else patch.lighter(105) if species == "cat" else fur)
            p.drawRoundedRect(QRectF(x - s * 0.05, base - leg_h - lift + low * 0.3, s * 0.1, leg_h), s * 0.05, s * 0.05)
    # body
    p.setBrush(fur)
    bw, bh = s * (0.34 if not sit else 0.3), s * (0.19 if not sleep else 0.17)
    p.drawEllipse(QPointF(bx, by - (s * 0.04 if sit else 0)), bw, bh)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(belly)
    p.drawEllipse(QPointF(bx + s * 0.05, by + s * 0.07), bw * 0.6, bh * 0.45)
    if species == "cat":
        p.setBrush(patch)
        p.drawEllipse(QPointF(bx - s * 0.12, by - s * 0.08), s * 0.12, s * 0.08)

    # head
    hx = cx + s * 0.2
    hy = base - s * (0.55 if not sleep else 0.3) - (s * 0.04 if sit else 0)
    hr = s * 0.25
    p.setPen(pen)
    # ears
    if species == "cat" or species == "puppy":
        for k, ex in ((0, -0.55), (1, 0.45)):
            path = QPainterPath()
            x0 = hx + ex * hr
            tip_h = hr * (0.95 if species == "cat" else 0.8)
            path.moveTo(x0 - hr * 0.32, hy - hr * 0.6)
            path.lineTo(x0 + (0.0 if k else -0.05) * hr, hy - hr * 0.6 - tip_h)
            path.lineTo(x0 + hr * 0.36, hy - hr * 0.68)
            path.closeSubpath()
            p.setBrush(patch if species == "cat" and k == 0 else fur)
            p.drawPath(path)
            ip = QPainterPath()
            ip.moveTo(x0 - hr * 0.16, hy - hr * 0.7)
            ip.lineTo(x0 + (0.0 if k else -0.03) * hr, hy - hr * 0.6 - tip_h * 0.7)
            ip.lineTo(x0 + hr * 0.18, hy - hr * 0.74)
            ip.closeSubpath()
            p.setBrush(inner)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(ip)
            p.setPen(pen)
    else:  # bunny: long ears, one flopped
        for k, (ex, rot) in enumerate(((-0.25, -14 + math.sin(tail) * 4), (0.3, 22 + math.sin(tail * 1.3) * 6))):
            p.save()
            p.translate(hx + ex * hr, hy - hr * 0.7)
            p.rotate(rot)
            p.setBrush(fur)
            p.drawEllipse(QPointF(0, -hr * 0.75), hr * 0.24, hr * 0.8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(inner)
            p.drawEllipse(QPointF(0, -hr * 0.72), hr * 0.12, hr * 0.6)
            p.restore()
            p.setPen(pen)
    p.setBrush(fur)
    p.drawEllipse(QPointF(hx, hy), hr * 1.08, hr)
    if species == "puppy":    # cream muzzle + cheeks
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(belly)
        p.drawEllipse(QPointF(hx + hr * 0.35, hy + hr * 0.35), hr * 0.55, hr * 0.42)
        p.drawEllipse(QPointF(hx - hr * 0.35, hy + hr * 0.4), hr * 0.35, hr * 0.3)
        p.setBrush(QColor("#fff4e4"))
        p.drawEllipse(QPointF(hx - hr * 0.1, hy - hr * 0.42), hr * 0.12, hr * 0.09)
        p.drawEllipse(QPointF(hx + hr * 0.42, hy - hr * 0.42), hr * 0.12, hr * 0.09)
    if species == "cat":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(patch)
        p.drawChord(QRectF(hx - hr * 1.08, hy - hr, hr * 1.1, hr * 1.2), 70 * 16, 130 * 16)

    # face (3/4 toward facing)
    e1, e2 = hx - hr * 0.22, hx + hr * 0.5
    ey = hy - hr * 0.05
    if eyes == "open":
        for i, ex in enumerate((e1, e2)):
            w = hr * (0.16 if i == 0 else 0.18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#2a1a22"))
            p.drawEllipse(QPointF(ex, ey), w, w * 1.3)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(ex + w * 0.3, ey - w * 0.45), w * 0.38, w * 0.38)
            p.drawEllipse(QPointF(ex - w * 0.3, ey + w * 0.45), w * 0.16, w * 0.16)
    else:
        p.setPen(_pen("#2a1a22", lw * 1.3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for ex in (e1, e2):
            r = QRectF(ex - hr * 0.15, ey - hr * 0.12, hr * 0.3, hr * 0.24)
            if eyes == "happy":
                p.drawArc(r, 20 * 16, 140 * 16)
            else:
                p.drawArc(r, 200 * 16, 140 * 16)
    # nose + mouth
    nx, ny = hx + hr * 0.2, hy + hr * 0.25
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#3a2328") if species == "puppy" else QColor("#ff8aa6"))
    p.drawEllipse(QPointF(nx, ny), hr * 0.09, hr * 0.065)
    p.setPen(_pen(line, lw * 0.9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if mouth_open:
        p.setBrush(QColor("#ff7a93"))
        p.drawEllipse(QPointF(nx, ny + hr * 0.2), hr * 0.09, hr * 0.1)
    else:
        p.drawArc(QRectF(nx - hr * 0.16, ny, hr * 0.16, hr * 0.14), 180 * 16, 180 * 16)
        p.drawArc(QRectF(nx, ny, hr * 0.16, hr * 0.14), 180 * 16, 180 * 16)
    if species == "cat":   # whiskers
        p.setPen(_pen(line, lw * 0.6))
        for k in (-1, 1):
            p.drawLine(QPointF(nx + hr * 0.35, ny + k * hr * 0.06), QPointF(nx + hr * 0.75, ny + k * hr * 0.14))
    # blush
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 110, 140, 95))
    p.drawEllipse(QPointF(e2 + hr * 0.12, ey + hr * 0.32), hr * 0.15, hr * 0.08)
    p.drawEllipse(QPointF(e1 - hr * 0.12, ey + hr * 0.32), hr * 0.12, hr * 0.07)
    # collar
    if not sleep:
        p.setPen(_pen(QColor(sp["collar"]).darker(130), lw))
        p.setBrush(QColor(sp["collar"]))
        p.drawRoundedRect(QRectF(hx - hr * 0.55, hy + hr * 0.82, hr * 0.9, hr * 0.2), hr * 0.1, hr * 0.1)
        p.setBrush(QColor("#ffd84a"))
        p.setPen(_pen("#b08a10", lw * 0.8))
        p.drawEllipse(QPointF(hx - hr * 0.05, hy + hr * 1.08), hr * 0.12, hr * 0.12)
    if species == "bunny":     # a bow on the ear
        p.setPen(_pen("#c2305a", lw * 0.8))
        p.setBrush(QColor("#ff8fc0"))
        bx2, by2 = hx + hr * 0.4, hy - hr * 0.8
        for sx in (-1, 1):
            tri = QPainterPath()
            tri.moveTo(bx2, by2)
            tri.lineTo(bx2 + sx * hr * 0.28, by2 - hr * 0.15)
            tri.lineTo(bx2 + sx * hr * 0.28, by2 + hr * 0.15)
            tri.closeSubpath()
            p.drawPath(tri)
    p.restore()


def draw_thing(p: QPainter, kind: str, cx: float, cy: float, s: float) -> None:
    """Draw a pet / toy centred on (cx, cy), about `s` px tall (used for what she's holding)."""
    if kind == "ball":
        draw_ball(p, cx, cy, s * 0.42)
    elif kind == "yarn":
        draw_yarn(p, cx, cy, s * 0.36)
    elif kind == "teddy":
        draw_teddy(p, cx, cy + s * 0.5, s, happy=True)
    elif kind in SPECIES:
        draw_pet(p, kind, cx - s * 0.05, cy + s * 0.5, s, 1.0, eyes="happy", pose="held",
                 tail=time.monotonic() * 3)


# ------------------------------------------------------------------------------------------- windows
class Thing(QWidget):
    """A small physics object on the desktop. Subclasses draw it and give it a brain."""

    radius = 18.0      # collision radius (px)
    bounce = 0.5

    def __init__(self, play: "Playground", kind: str, w: int, h: int):
        super().__init__(None)
        self.play, self.kind = play, kind
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.resize(w, h)
        self.px = self.py = 0.0
        self.vx = self.vy = 0.0
        self.t = random.uniform(0, 10)
        self.angle = 0.0
        self.grounded = False
        self.dragging = False
        self.carried_by: Thing | None = None
        self.cooldown = 0.0          # after touching her, ignore collisions for a moment
        self.hearts = 0.0
        self.say_text, self.say_t = "", 0.0
        self.squish = 0.0
        self._pressed = False
        self._press_t = 0.0
        self._press_g = QPointF()
        self._grab = QPointF()
        self._samples: list[tuple[float, QPointF]] = []
        self._rub = 0.0
        self._last_hover: QPointF | None = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # geometry ---------------------------------------------------------------
    def center(self) -> QPointF:
        return QPointF(self.px + self.width() / 2, self.py + self.height() - self.radius - 2)

    def floor(self) -> tuple[float, float, float]:
        scr = QGuiApplication.screenAt(self.center().toPoint()) or QGuiApplication.primaryScreen()
        a = scr.availableGeometry()
        return a.left() - self.width() * 0.3, a.right() - self.width() * 0.7, a.bottom() + 1 - self.height()

    def place(self, x: float, y: float | None = None) -> None:
        left, right, ground = self.floor()
        self.px = max(left, min(right, x - self.width() / 2))
        self.py = ground if y is None else y - self.height() / 2
        self.move(round(self.px), round(self.py))

    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    def say(self, text: str, seconds: float = 1.8) -> None:
        self.say_text, self.say_t = text, seconds

    # physics ----------------------------------------------------------------
    def physics(self, dt: float) -> None:
        self.t += dt
        self.cooldown = max(0.0, self.cooldown - dt)
        self.hearts = max(0.0, self.hearts - dt)
        self.say_t = max(0.0, self.say_t - dt)
        self.squish *= max(0.0, 1 - dt * 6)
        if self.dragging or self._pressed:
            return
        if self.carried_by is not None:
            c = self.carried_by
            mx = c.center().x() + c.facing * c.size * 0.42
            self.px, self.py = mx - self.width() / 2, c.py + c.height() - c.size * 0.45 - self.height() / 2
            self.vx = self.vy = 0.0
            self.move(round(self.px), round(self.py))
            return
        left, right, ground = self.floor()
        self.vy += GRAVITY * dt
        self.px += self.vx * dt
        self.py += self.vy * dt
        if self.px < left or self.px > right:
            self.px = max(left, min(right, self.px))
            self.vx = -self.vx * 0.6
        self.grounded = False
        if self.py >= ground:
            self.py = ground
            if self.vy > 260:
                impact = self.vy
                self.vy = -self.vy * self.bounce
                self.squish = min(1.0, impact / 1800)
                self.on_land(impact)
            else:
                self.vy = 0.0
                self.grounded = True
                self.vx *= max(0.0, 1 - dt * self.friction())
        self.move(round(self.px), round(self.py))

    def friction(self) -> float:
        return 2.5

    def on_land(self, impact: float) -> None:
        pass

    def think(self, dt: float) -> None:
        pass

    # mouse ------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self.play.menu_requested.emit(e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._pressed = True
        self._press_t = time.monotonic()
        self._press_g = e.globalPosition()
        self._grab = e.globalPosition() - QPointF(self.px, self.py)
        self._samples = [(time.perf_counter(), e.globalPosition())]

    def mouseMoveEvent(self, e) -> None:
        gp = e.globalPosition()
        if self._pressed:
            if not self.dragging and (gp - self._press_g).manhattanLength() > 6:
                self.dragging = True
                self.carried_by = None
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.on_pickup()
            if self.dragging:
                self._samples = (self._samples + [(time.perf_counter(), gp)])[-6:]
                tgt = gp - self._grab
                self.px, self.py = tgt.x(), tgt.y()
                self.move(round(self.px), round(self.py))
            return
        if self._last_hover is not None:       # stroking back and forth = petting
            self._rub += (e.position() - self._last_hover).manhattanLength()
            if self._rub > 240:
                self._rub = 0.0
                self.on_pet()
        self._last_hover = e.position()

    def leaveEvent(self, _e) -> None:
        self._last_hover = None
        self._rub = 0.0

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self._pressed:
            return
        self._pressed = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        held = time.monotonic() - self._press_t
        if self.dragging:
            self.dragging = False
            vx = vy = 0.0
            if len(self._samples) >= 2:
                (t0, p0), (t1, p1) = self._samples[0], self._samples[-1]
                if time.perf_counter() - t1 < 0.08 and t1 - t0 > 1e-3:
                    vx, vy = (p1.x() - p0.x()) / (t1 - t0), (p1.y() - p0.y()) / (t1 - t0)
            self.vx, self.vy = max(-2600.0, min(2600.0, vx)), max(-2600.0, min(2600.0, vy))
            self.on_release()
            self.play.released(self)
        elif held > 1.2:
            self.on_squeeze_hard()
        else:
            self.on_click()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.on_pet()

    def on_pickup(self) -> None:
        pass

    def on_release(self) -> None:
        pass

    def on_click(self) -> None:
        self.vy = -420
        self.vx += random.uniform(-80, 80)
        self.squish = 0.5

    def on_pet(self) -> None:
        self.hearts = 1.5

    def on_squeeze_hard(self) -> None:
        self.on_click()

    # painting ---------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw(p)
        w = self.width()
        if self.hearts > 0:
            for i in range(2):
                ph = (self.t * 0.9 + i / 2) % 1.0
                col = QColor("#ff5c8a")
                col.setAlphaF(min(1.0, self.hearts) * (1 - ph))
                _heart(p, w * (0.45 + 0.2 * i), self.height() * 0.32 - ph * self.height() * 0.25, 9, col)
        if self.say_t > 0:
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            col = QColor("#5a3550")
            col.setAlphaF(min(1.0, self.say_t * 2))
            r = QRectF(0, 0, w, 16)
            bg = QColor(255, 255, 255, int(200 * min(1.0, self.say_t * 2)))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(self.say_text) + 10
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF((w - tw) / 2, 0, tw, 15), 7, 7)
            p.setPen(col)
            p.drawText(r, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.say_text)
        p.end()

    def draw(self, p: QPainter) -> None:
        pass


class Toy(Thing):
    def __init__(self, play, kind, scale):
        self.size = {"ball": 34, "yarn": 28, "teddy": 50}[kind] * scale
        r = self.size * (0.42 if kind == "ball" else 0.36 if kind == "yarn" else 0.45)
        w = int(self.size * (1.9 if kind == "yarn" else 1.35)) + 8
        h = int(self.size * 1.25) + 18
        super().__init__(play, kind, w, h)
        self.radius = r
        self.bounce = {"ball": 0.72, "yarn": 0.35, "teddy": 0.25}[kind]

    def friction(self) -> float:
        return {"ball": 0.8, "yarn": 2.2, "teddy": 7.0}[self.kind]

    def physics(self, dt: float) -> None:
        super().physics(dt)
        if self.kind in ("ball", "yarn") and not self.dragging:
            self.angle += self.vx * dt / max(4.0, self.radius)

    def on_land(self, impact: float) -> None:
        if impact > 700 and self.kind == "teddy":
            self.say("*fwump*", 0.8)

    def on_click(self) -> None:
        if self.kind == "ball":
            self.vy = -700
            self.vx += random.choice((-1, 1)) * random.uniform(120, 260)
        else:
            super().on_click()
        self.play.toy_poked(self)

    def on_pet(self) -> None:
        super().on_pet()
        if self.kind == "teddy":
            self.say("♡", 1.0)

    def draw(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        sq = self.squish
        base = h - 2
        cx = (self.size * 0.62 + 4) if self.kind == "yarn" else w / 2
        p.save()
        p.translate(cx, base)
        p.scale(1 + sq * 0.25, 1 - sq * 0.25)
        p.translate(-cx, -base)
        if self.kind == "ball":
            draw_ball(p, cx, base - self.radius, self.radius, self.angle)
        elif self.kind == "yarn":
            draw_yarn(p, cx, base - self.radius, self.radius, self.angle)
        else:
            draw_teddy(p, cx, base, self.size, happy=self.hearts > 0)
        p.restore()


class Pet(Thing):
    def __init__(self, play, kind, scale):
        self.size = 54 * scale
        super().__init__(play, kind, int(self.size * 1.55), int(self.size * 1.45) + 18)
        self.radius = self.size * 0.35
        self.bounce = 0.2
        self.facing = random.choice((-1.0, 1.0))
        self.mode, self.mode_t = "idle", random.uniform(1, 3)
        self.target_x: float | None = None
        self.walk_ph = 0.0
        self.blink_t = random.uniform(1, 4)
        self.fear = 0.0              # just got hurt: runs away from the cursor
        self.toy: Toy | None = None   # chasing / carrying

    @property
    def sp(self):
        return SPECIES[self.kind]

    def sound(self) -> None:
        self.say(random.choice(self.sp["sounds"]))

    def friction(self) -> float:
        return 9.0

    def on_land(self, impact: float) -> None:
        if impact > 1900:
            self.hurt()
        elif impact > 900:
            self.say("!?", 1.0)

    def on_pickup(self) -> None:
        self.mode = "held"
        if self.toy is not None and self.toy.carried_by is self:
            self.toy.carried_by = None
        self.toy = None
        self.say(random.choice(["!?", self.sp["sounds"][0]]), 1.2)

    def on_release(self) -> None:
        self.mode, self.mode_t = "idle", 1.0

    def on_click(self) -> None:
        self.sound()
        self.vy = -260 if self.grounded else self.vy
        self.play.pet_event(self, "boop")

    def on_pet(self) -> None:
        self.hearts = 2.5
        self.mode, self.mode_t = "happy", 2.5
        self.say({"cat": "Purrr~ ♡", "puppy": "*wag wag* ♡", "bunny": "*happy hop* ♡"}[self.kind])
        self.play.pet_event(self, "petted")

    def on_squeeze_hard(self) -> None:
        self.hurt()

    def hurt(self) -> None:
        self.say({"cat": "HSSS!", "puppy": "Yelp!", "bunny": "Eek!"}[self.kind], 2.0)
        self.fear = 6.0
        self.mode, self.mode_t = "flee", 3.0
        self.vy = -380
        self.play.pet_event(self, "hurt")

    # brain ------------------------------------------------------------------
    def think(self, dt: float) -> None:
        self.blink_t -= dt
        if self.blink_t < -0.12:
            self.blink_t = random.uniform(2, 5)
        self.fear = max(0.0, self.fear - dt)
        if self.dragging or self._pressed:
            self.walk_ph += dt * 6
            return
        if not self.grounded:
            return
        self.mode_t -= dt
        her = self.play.her_center()
        me = self.center().x()
        speed = 0.0
        if self.mode == "flee":
            cur = QCursor.pos().x()
            self.facing = 1.0 if me > cur else -1.0
            speed = 260
            if self.mode_t <= 0:
                self.mode, self.mode_t = "idle", 2.0
        elif self.play.her_asleep() and self.mode not in ("sleep",):
            if her is not None and abs(her.x() - me) > 110:
                self.facing = 1.0 if her.x() > me else -1.0
                speed = 80
            else:
                self.mode, self.mode_t = "sleep", 1e9
        elif self.mode == "sleep":
            if not self.play.her_asleep() and self.mode_t > 60:
                self.mode, self.mode_t = "idle", 1.0
            elif self.mode_t <= 0:
                self.mode, self.mode_t = "idle", 1.0
        elif self.mode in ("chase", "fetch"):
            toy = self.toy
            if toy is None or toy.dragging or not toy.isVisible():
                self.mode, self.mode_t, self.toy = "idle", 1.0, None
            elif self.mode == "chase":
                tx = toy.center().x()
                self.facing = 1.0 if tx > me else -1.0
                speed = 240
                if abs(tx - me) < self.size * 0.45 and abs(toy.center().y() - self.center().y()) < self.size:
                    if self.kind == "puppy" and toy.kind == "ball" and her is not None:
                        toy.carried_by = self          # got it! bring it back to her
                        self.mode, self.mode_t = "fetch", 12.0
                        self.say("*chomp*", 1.0)
                    else:                              # bat / boop it along
                        toy.vx = self.facing * random.uniform(250, 450)
                        toy.vy = -random.uniform(200, 420)
                        self.say(random.choice(self.sp["sounds"]), 1.0)
                        self.mode, self.mode_t = ("chase", 2.5) if random.random() < 0.5 else ("idle", 2.0)
                        if self.mode == "idle":
                            self.toy = None
                if self.mode_t <= 0:
                    self.mode, self.toy = "idle", None
            else:   # fetch: carry it to her
                if her is None:
                    toy.carried_by, self.mode, self.toy = None, "idle", None
                else:
                    self.facing = 1.0 if her.x() > me else -1.0
                    speed = 200
                    if abs(her.x() - me) < self.size * 1.1 or self.mode_t <= 0:
                        toy.carried_by = None
                        toy.vx, toy.vy = self.facing * 120, -150
                        self.mode, self.mode_t, self.toy = "happy", 2.5, None
                        self.hearts = 2.0
                        self.say(random.choice(self.sp["sounds"]))
                        self.play.pet_event(self, "fetched")
        else:
            toy = self.play.toy_to_chase(self)
            if toy is not None:
                self.mode, self.mode_t, self.toy = "chase", 6.0, toy
            elif her is not None and abs(her.x() - me) > 260:
                self.facing = 1.0 if her.x() > me else -1.0
                speed = 210 if abs(her.x() - me) > 650 else 95
                self.mode = "follow"
            elif self.mode == "walk" and self.target_x is not None:
                self.facing = 1.0 if self.target_x > me else -1.0
                speed = 70
                if abs(self.target_x - me) < 8 or self.mode_t <= 0:
                    self.mode, self.mode_t = "idle", random.uniform(2, 5)
            elif self.mode_t <= 0 or self.mode == "follow":
                r = random.random()
                if r < 0.3:
                    self.mode, self.mode_t = "sit", random.uniform(4, 10)
                elif r < 0.55 and her is not None:
                    side = random.choice((-1, 1))
                    self.target_x = her.x() + side * random.uniform(60, 160)
                    self.mode, self.mode_t = "walk", 6.0
                elif r < 0.68 and her is not None:     # nuzzle her
                    self.target_x = her.x() - (1 if her.x() > me else -1) * self.size * 0.5
                    self.mode, self.mode_t = "walk", 5.0
                    self.play.pet_event(self, "nuzzle")
                    self.hearts = 2.5
                elif r < 0.75:
                    self.sound()
                    self.mode, self.mode_t = "idle", 2.0
                elif r < 0.82:
                    self.mode, self.mode_t = "sleep", random.uniform(10, 25)
                else:
                    self.target_x = me + random.uniform(-220, 220)
                    self.mode, self.mode_t = "walk", 6.0
        if speed:
            left, right, _ = self.floor()
            self.px = max(left, min(right, self.px + self.facing * speed * dt))
            self.walk_ph += dt * speed / 9.0
        self._moving = bool(speed)

    def draw(self, p: QPainter) -> None:
        s = self.size
        w, h = self.width(), self.height()
        pose = {"sit": "sit", "sleep": "sleep", "held": "held"}.get(self.mode, "stand")
        if self.dragging or self._pressed:
            pose = "held" if self.dragging else pose
        eyes = "closed" if pose == "sleep" or self.blink_t < 0 else "happy" if self.hearts > 0 or self.mode == "happy" else "open"
        sq = self.squish
        base = h - 2
        p.save()
        p.translate(w / 2, base)
        p.scale(1 + sq * 0.2, 1 - sq * 0.2)
        p.translate(-w / 2, -base)
        draw_pet(p, self.kind, w / 2, base, s, self.facing, self.walk_ph, getattr(self, "_moving", False), eyes,
                 self.t * (5 if self.mode == "happy" else 2), pose, mouth_open=self.say_t > 1.2)
        p.restore()
        if pose == "sleep":
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            for i in range(2):
                ph = (self.t * 0.4 + i / 2) % 1.0
                col = QColor("#7b8cff")
                col.setAlphaF(1 - ph)
                p.setPen(col)
                p.drawText(QPointF(w * 0.62 + ph * 8 * self.facing, h * 0.45 - ph * 18), "z")


# ------------------------------------------------------------------------------------------- manager
class Playground(QObject):
    """Owns the pets & toys, runs their physics and how they interact with her."""

    event = Signal(str, str)          # (what happened, which pet/toy)
    menu_requested = Signal(object)   # QPoint

    def __init__(self, character, cfg):
        super().__init__()
        self.c, self.cfg = character, cfg
        self.things: list[Thing] = []
        self._next_play = random.uniform(15, 30)
        self._goal: tuple[str, Thing] | None = None
        self._goal_t = 0.0
        self._last = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        c = character
        if hasattr(c, "held_done"):
            c.held_done.connect(self._put_back)

    # pets / toys out --------------------------------------------------------
    def kinds(self) -> list[str]:
        return [t.kind for t in self.things]

    def add(self, kind: str, announce: bool = True) -> Thing | None:
        if kind in self.kinds() or kind not in LABELS:
            return None
        scale = max(0.6, self.cfg.scale)
        t = Pet(self, kind, scale) if kind in PETS else Toy(self, kind, scale)
        r = self.c.global_rect()
        side = random.choice((-1, 1))
        t.place(r.center().x() + side * random.uniform(90, 180), r.top() + r.height() * 0.3)
        t.vy = 0
        t.show()
        self.things.append(t)
        self._save()
        if announce:
            self.event.emit("arrived", kind)
        return t

    def remove(self, kind: str) -> None:
        for t in [t for t in self.things if t.kind == kind]:
            self.things.remove(t)
            for o in self.things:
                if o.carried_by is t:
                    o.carried_by = None
            t.hide()
            t.deleteLater()
        if getattr(self.c.ctx, "holding", "") == kind:
            self.c.ctx.holding = ""
        self._save()

    def toggle(self, kind: str) -> None:
        if kind in self.kinds():
            self.remove(kind)
        else:
            self.add(kind)

    def clear(self) -> None:
        for k in list(self.kinds()):
            self.remove(k)

    def restore(self) -> None:
        for k in list(getattr(self.cfg, "companions", []) or []):
            self.add(k, announce=False)

    def _save(self) -> None:
        self.cfg.companions = self.kinds()
        try:
            self.cfg.save()
        except Exception:
            pass

    def get(self, kind: str) -> Thing | None:
        return next((t for t in self.things if t.kind == kind), None)

    # her ----------------------------------------------------------------------
    def her_center(self) -> QPointF | None:
        c = self.c
        if not c.isVisible():
            return None
        r = c.global_rect()
        return QPointF(r.center().x(), r.bottom() - r.height() * 0.3)

    def her_asleep(self) -> bool:
        return bool(getattr(self.c, "asleep", False))

    def _her_local(self, gp: QPointF) -> QPointF | None:
        """If the global point is on her (an opaque pixel of her frame), return it in her window coordinates."""
        c = self.c
        img = getattr(c, "_frame", None)
        if img is None or not c.isVisible():
            return None
        lp = gp - QPointF(c.px, c.py)
        if not (0 <= lp.x() < c.width() and 0 <= lp.y() < c.height()):
            return None
        d = img.devicePixelRatio() or 1.0
        x, y = int(lp.x() * d), int(lp.y() * d)
        if 0 <= x < img.width() and 0 <= y < img.height() and img.pixelColor(x, y).alpha() > 40:
            return lp
        return None

    def _hit_her(self, t: Thing, at: QPointF | None = None) -> QPointF | None:
        cen = t.center() if at is None else at
        for dx, dy in ((0, 0), (t.radius * 0.7, 0), (-t.radius * 0.7, 0), (0, -t.radius * 0.7), (0, t.radius * 0.6)):
            lp = self._her_local(cen + QPointF(dx, dy))
            if lp is not None:
                return lp
        return None

    def _her_free(self) -> bool:
        from .sprites import DRAG, FALL, KNOCK, SLEEP
        c = self.c
        return c.state not in (DRAG, FALL, KNOCK, SLEEP) and not c.ctx.holding

    # events from things -------------------------------------------------------
    def released(self, t: Thing) -> None:
        """You let go of a pet / toy. Dropped gently on her = a present; thrown hard at her = bonk."""
        lp = self._hit_her(t)
        if lp is None:
            return
        if t.speed() > 1100:
            self._bonk(t, lp)
        else:
            self._gift(t)

    def _bonk(self, t: Thing, lp: QPointF) -> None:
        c = self.c
        zone = c._zone_at(lp) if hasattr(c, "_zone_at") else "body"
        hit_speed, hit_dir = t.speed(), (1.0 if t.vx >= 0 else -1.0)
        t.vx, t.vy = -t.vx * 0.35, -abs(t.vy) * 0.3 - 250
        t.cooldown = 1.0
        if isinstance(t, Pet):
            t.hurt()
        from .sprites import DRAG, FALL
        if c.state not in (DRAG, FALL):
            c.touch(zone, "rough")
            if hit_speed > 1800:              # a really hard throw knocks her over
                c.knock_down(hit_dir)
        self.event.emit("bonk", t.kind)

    def _gift(self, t: Thing) -> None:
        c = self.c
        t.cooldown = 1.2
        if not self._her_free() and not c.asleep:
            return
        if t.kind in ("teddy",) or t.kind in PETS:
            self._hug(t)
        else:   # ball / yarn: she bats it back to you
            self._kick(t, gentle=True)
        self.event.emit("gift", t.kind)

    def _hug(self, t: Thing, seconds: float = 0.0) -> None:
        c = self.c
        if c.ctx.holding:
            return
        t.hide()
        t.dragging = False
        t.vx = t.vy = 0.0
        if isinstance(t, Pet):
            t.mode, t.mode_t = "held", 1e9
        c.hold(t.kind, seconds or (16.0 if t.kind == "teddy" else 9.0))

    def _put_back(self, kind: str) -> None:
        t = self.get(kind)
        if t is None:
            return
        r = self.c.global_rect()
        t.place(r.center().x() + random.choice((-1, 1)) * 60, r.bottom() - r.height() * 0.45)
        t.vy, t.vx = -200, random.uniform(-60, 60)
        if isinstance(t, Pet):
            t.mode, t.mode_t = "happy", 2.0
            t.hearts = 2.0
            t.sound()
        t.show()

    def _kick(self, t: Thing, gentle: bool = False) -> None:
        c = self.c
        cx = c.global_rect().center().x()
        d = 1.0 if t.center().x() >= cx else -1.0
        angry = getattr(c.ctx, "angry", 0.0) > 0.3
        power = 1.8 if angry else 0.6 if gentle else 1.0
        t.vx = d * random.uniform(420, 650) * power
        t.vy = -random.uniform(380, 600) * min(1.4, power)
        t.cooldown = 1.0
        if hasattr(c, "kick"):
            c.kick()
        self.event.emit("angry_kick" if angry else "kick", t.kind)

    def toy_poked(self, t: Toy) -> None:
        pass

    def pet_event(self, pet: Pet, what: str) -> None:
        if what == "petted":
            pet.cooldown = 0.5
        self.event.emit(f"pet_{what}", pet.kind)

    def toy_to_chase(self, pet: Pet) -> Toy | None:
        me = pet.center()
        best = None
        for t in self.things:
            if not isinstance(t, Toy) or t.kind not in pet.sp["likes"] or t.dragging or not t.isVisible():
                continue
            if t.carried_by is not None:
                continue
            d = abs(t.center().x() - me.x())
            moving = t.speed() > 150
            if (moving and d < 900) or (d < 260 and random.random() < 0.004):
                if best is None or d < abs(best.center().x() - me.x()):
                    best = t
        return best

    # tick ---------------------------------------------------------------------
    def _tick(self) -> None:
        now = time.perf_counter()
        dt = max(1e-3, min(0.1, now - self._last))
        self._last = now
        if not self.things:
            return
        for t in self.things:
            if not t.isVisible():
                continue
            before = t.center()
            t.physics(dt)
            t.think(dt)
            t.update()
            # flying into her (swept along its path, so fast throws can't skip through her)
            if not t.dragging and t.carried_by is None and t.cooldown <= 0 and t.speed() > 350:
                lp = None
                after = t.center()
                steps = max(1, int((after - before).manhattanLength() / max(6.0, t.radius * 0.6)))
                for i in range(1, steps + 1):
                    lp = self._hit_her(t, before + (after - before) * (i / steps))
                    if lp is not None:
                        break
                if lp is not None:
                    if t.speed() > 1300:
                        self._bonk(t, lp)
                    elif isinstance(t, Pet) or t.kind == "teddy":
                        self._gift(t) if self._her_free() else setattr(t, "cooldown", 0.8)
                    else:
                        self._kick(t)
        self._her_plays(dt)

    def _her_plays(self, dt: float) -> None:
        """Now and then she goes over to play with a pet / toy."""
        from .sprites import IDLE, SIT, WALK
        c = self.c
        if self._goal is not None:
            kind, t = self._goal
            self._goal_t -= dt
            if t not in self.things or not t.isVisible() or self._goal_t <= 0 or not self._her_free() \
                    or t.dragging or c._dragging:
                self._goal = None
                return
            her = c.global_rect().center().x()
            tx = t.center().x()
            if abs(tx - her) < max(45.0, c.width() * 0.22):
                self._goal = None
                if kind == "kick":
                    self._kick(t)
                elif kind == "pet" and isinstance(t, Pet):
                    t.hearts = 3.0
                    t.mode, t.mode_t = "happy", 3.0
                    t.sound()
                    c.pet_animal()
                    self.event.emit("her_pets", t.kind)
                elif kind == "hug":
                    self._hug(t)
                    self.event.emit("her_hugs", t.kind)
            elif c.state != WALK and c.state in (IDLE, SIT):
                c.walk_to(tx)
            return
        self._next_play -= dt
        if self._next_play > 0:
            return
        self._next_play = random.uniform(20, 45)
        if not self._her_free() or c.state not in (IDLE, SIT, WALK) or c.busy or c.talking \
                or getattr(c, "user_pose", None) or c.sulking:
            return
        angry = c.ctx.angry > 0.3
        options = []
        for t in self.things:
            if not t.isVisible() or t.dragging:
                continue
            if isinstance(t, Toy) and t.kind in ("ball", "yarn") and t.grounded and t.carried_by is None:
                options += [("kick", t)] * (4 if angry else 2)
            elif not angry and isinstance(t, Pet) and t.mode != "sleep":
                options += [("pet", t)] * 2
            elif not angry and t.kind == "teddy":
                options.append(("hug", t))
        if options:
            self._goal = random.choice(options)
            self._goal_t = 14.0
            c.walk_to(self._goal[1].center().x())
