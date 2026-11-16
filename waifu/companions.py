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


def draw_treat(p: QPainter, cx: float, cy: float, r: float) -> None:
    """A heart-shaped cookie."""
    col = QColor("#e8a35c")
    path = QPainterPath()
    x, y, s = cx, cy - r * 0.15, r * 2.2
    path.moveTo(x, y + s * 0.3)
    path.cubicTo(x - s * 0.5, y - s * 0.2, x - s * 0.1, y - s * 0.55, x, y - s * 0.2)
    path.cubicTo(x + s * 0.1, y - s * 0.55, x + s * 0.5, y - s * 0.2, x, y + s * 0.3)
    p.setPen(_pen("#8a5a2b", max(1.0, r * 0.12)))
    p.setBrush(col)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ff8fb0"))
    for dx, dy in ((-0.25, -0.15), (0.2, -0.1), (0.0, 0.15)):
        p.drawEllipse(QPointF(cx + dx * r, cy + dy * r), r * 0.1, r * 0.1)


def draw_book(p: QPainter, cx: float, cy: float, s: float) -> None:
    """An open book, `s` px wide."""
    w, h = s, s * 0.62
    p.setPen(_pen("#9b3b62", max(1.0, s * 0.03)))
    p.setBrush(QColor("#ff8fb6"))
    p.drawRoundedRect(QRectF(cx - w / 2 - s * 0.03, cy - h / 2 - s * 0.02, w + s * 0.06, h + s * 0.06), 3, 3)
    for sx in (-1, 1):
        page = QPainterPath()
        page.moveTo(cx, cy - h / 2 + s * 0.04)
        page.cubicTo(cx + sx * w * 0.2, cy - h / 2 - s * 0.02, cx + sx * w * 0.35, cy - h / 2, cx + sx * w / 2, cy - h / 2 + s * 0.02)
        page.lineTo(cx + sx * w / 2, cy + h / 2 - s * 0.02)
        page.cubicTo(cx + sx * w * 0.35, cy + h / 2 - s * 0.04, cx + sx * w * 0.2, cy + h / 2 - s * 0.06, cx, cy + h / 2)
        page.closeSubpath()
        p.setPen(_pen("#c9b8a8", max(0.8, s * 0.015)))
        p.setBrush(QColor("#fffaf2"))
        p.drawPath(page)
        p.setPen(_pen("#b9aaa0", max(0.6, s * 0.012)))
        for k in range(4):
            yy = cy - h * 0.25 + k * h * 0.15
            p.drawLine(QPointF(cx + sx * w * 0.08, yy), QPointF(cx + sx * w * 0.42, yy))


def draw_tea(p: QPainter, cx: float, cy: float, s: float) -> None:
    """A steaming mug, `s` px tall."""
    t = time.monotonic()
    p.setPen(_pen("#c7c0d8", max(1.0, s * 0.05)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for k in (-1, 1):
        ph = (t * 0.8 + (k + 1) * 0.3) % 1.0
        path = QPainterPath()
        x0, y0 = cx + k * s * 0.15, cy - s * 0.55 - ph * s * 0.3
        path.moveTo(x0, y0 + s * 0.25)
        path.cubicTo(x0 - s * 0.1, y0 + s * 0.15, x0 + s * 0.1, y0 + s * 0.08, x0, y0)
        p.drawPath(path)
    p.setPen(_pen("#8a4a5a", max(1.0, s * 0.05)))
    p.drawArc(QRectF(cx + s * 0.28, cy - s * 0.25, s * 0.3, s * 0.35), -90 * 16, 180 * 16)
    p.setBrush(QColor("#ffe0ec"))
    p.drawRoundedRect(QRectF(cx - s * 0.35, cy - s * 0.45, s * 0.7, s * 0.85), s * 0.12, s * 0.12)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#a8664a"))
    p.drawEllipse(QPointF(cx, cy - s * 0.43), s * 0.3, s * 0.07)
    _heart(p, cx, cy, s * 0.35, QColor("#ff6b97"))


def draw_thing(p: QPainter, kind: str, cx: float, cy: float, s: float) -> None:
    """Draw a pet / toy centred on (cx, cy), about `s` px tall (used for what she's holding)."""
    if kind == "ball":
        draw_ball(p, cx, cy, s * 0.42)
    elif kind == "yarn":
        draw_yarn(p, cx, cy, s * 0.36)
    elif kind == "teddy":
        draw_teddy(p, cx, cy + s * 0.5, s, happy=True)
    elif kind == "book":
        draw_book(p, cx, cy, s * 0.8)
    elif kind == "tea":
        draw_tea(p, cx + s * 0.1, cy - s * 0.05, s * 0.45)
    elif kind == "treat":
        draw_treat(p, cx, cy, s * 0.25)
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
        self.flying = False          # you threw it: it can hit / land on her until it touches the ground
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
                self.flying = False
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
            self.flying = True
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
        self.size = {"ball": 34, "yarn": 28, "teddy": 50, "treat": 20}[kind] * scale
        r = self.size * (0.42 if kind == "ball" else 0.36 if kind in ("yarn", "treat") else 0.45)
        w = int(self.size * (1.9 if kind == "yarn" else 1.35)) + 8
        h = int(self.size * 1.25) + 18
        super().__init__(play, kind, w, h)
        self.radius = r
        self.bounce = {"ball": 0.72, "yarn": 0.35, "teddy": 0.25, "treat": 0.2}[kind]

    def friction(self) -> float:
        return {"ball": 0.8, "yarn": 2.2, "teddy": 7.0, "treat": 8.0}[self.kind]

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
        elif self.kind == "treat":
            draw_treat(p, cx, base - self.radius, self.radius)
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
        if self.mode != "fetch":           # interrupted (petted, party, nap...): drop whatever it was carrying
            for t in self.play.things:
                if t.carried_by is self:
                    t.carried_by = None
                    t.vx, t.vy = self.facing * 80, -120
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
        elif self.mode == "wait":                       # staying put next to her (being petted / waiting)
            if her is not None:
                self.facing = 1.0 if her.x() > me else -1.0
            if self.mode_t <= 0:
                self.mode, self.mode_t = "idle", 1.0
        elif self.play.party_t > 0 and self.mode not in ("held", "sleep"):
            if her is not None and abs(her.x() - me) > 200:     # come join the party
                self.facing = 1.0 if her.x() > me else -1.0
                speed = 180
            elif random.random() < dt * 2.2:                      # hop to the beat
                self.vy = -random.uniform(260, 380)
                self.facing = -self.facing if random.random() < 0.3 else self.facing
                self.say(random.choice(("♪", "♫", self.sp["sounds"][0])), 0.8)
                self.hearts = 1.0
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
                    if toy.kind == "treat":
                        self.toy = None
                        self.play.eat(toy, self)
                    elif self.kind == "puppy" and toy.kind == "ball" and her is not None:
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
        self._act: dict | None = None
        self._throwing: Thing | None = None
        self.party_t = 0.0
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
        self.cfg.companions = [k for k in self.kinds() if k in LABELS]
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
        if self._throwing is t:                    # wheee~ throw!
            self._throwing = None
            r = self.c.global_rect()
            her = r.center().x()
            pup = self.get("puppy") or self.get("cat")
            scr = QGuiApplication.screenAt(r.center()) or QGuiApplication.primaryScreen()
            mid = scr.availableGeometry().center().x()
            d = (1.0 if mid >= her else -1.0) if pup is None else (1.0 if pup.center().x() < her and random.random() < 0.3 else
                                                                 1.0 if her < mid else -1.0)
            t.place(her + d * 30, r.top() + r.height() * 0.3)
            t.vx, t.vy = d * random.uniform(700, 1000), -random.uniform(650, 900)
            t.cooldown = 1.2
            t.show()
            if hasattr(self.c, "kick"):
                self.c.kick()
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
        treat = self.get("treat")
        if treat is not None and treat.isVisible() and abs(treat.center().x() - me.x()) < 1200:
            return treat
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
            if t.flying and not t.dragging and t.carried_by is None and t.cooldown <= 0 and t.speed() > 350:
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

    # ------------------------------------------------------------------ her activities
    ACT_PETS = ("pet", "pet", "pet", "cuddle", "treat", "party")

    def _act_options(self) -> list[tuple[str, Thing | None]]:
        c = self.c
        angry = c.ctx.angry > 0.3
        opts: list[tuple[str, Thing | None]] = []
        vis = [t for t in self.things if t.isVisible() and not t.dragging and t.carried_by is None]
        pets = [t for t in vis if isinstance(t, Pet) and t.mode not in ("sleep", "flee")]
        toys = [t for t in vis if isinstance(t, Toy)]
        for t in toys:
            if t.kind in ("ball", "yarn") and t.grounded:
                opts += [("kick", t)] * (4 if angry else 1)
                if not angry and t.kind == "ball":
                    opts += [("throw", t)] * (3 if self.get("puppy") else 1)
                if not angry and t.kind == "yarn" and self.get("cat"):
                    opts += [("yarn", t)] * 2
            elif t.kind == "teddy" and not angry:
                opts += [("hug", t)]
        if not angry:
            for p in pets:
                opts += [("pet", p)] * 4 + [("cuddle", p)] * 2 + [("treat", p)] * 2
            if pets:
                opts += [("party", None)] * 2
            opts += [("read", None), ("tea", None), ("exercise", None)]   # things she does on her own
        return opts

    def start_activity(self, name: str, target: Thing | None = None) -> bool:
        """Begin one of her activities (also used by the menu). Returns False if she can't now."""
        c = self.c
        if not self._her_free() or c.busy or c.talking or c.sulking:
            return False
        if target is None and name in ("pet", "cuddle", "treat"):
            pets = [t for t in self.things if isinstance(t, Pet) and t.isVisible()]
            target = random.choice(pets) if pets else None
        if target is None and name in ("throw", "kick"):
            target = self.get("ball") or self.get("yarn")
        if target is None and name == "yarn":
            target = self.get("yarn")
        if target is None and name not in ("party", "read", "tea", "exercise"):
            return False
        if name == "party" and not any(isinstance(t, Pet) for t in self.things):
            return False
        if c.user_pose:
            c.stand_up()
        self._act = {"name": name, "t": target, "stage": "go" if target is not None else "do", "time": 16.0, "k": 0.0}
        if target is not None:
            c.walk_to(self._spot(name, target))
        else:
            self._begin(self._act)          # nothing to walk to: start right away
        return True

    def _spot(self, name: str, t: Thing) -> float:
        """Where she walks to for an activity (global x of her centre)."""
        her = self.c.global_rect().center().x()
        tx = t.center().x()
        if name in ("pet", "treat"):      # stand beside the pet, not on it
            side = 1.0 if tx >= her else -1.0
            return tx - side * self._reach()
        return tx

    def _reach(self) -> float:
        ppm = getattr(self.c.sprites, "ppm", None)
        return (0.34 * ppm) if ppm else self.c.width() * 0.3

    def _end_act(self) -> None:
        self._act = None
        self._next_play = random.uniform(18, 40)

    def _her_plays(self, dt: float) -> None:
        """Now and then she does something: plays with her pets and toys, or reads, sips tea, exercises."""
        from .sprites import DRAG, FALL, IDLE, KNOCK, SIT, SLEEP, WALK
        c = self.c
        self.party_t = max(0.0, self.party_t - dt)
        a = self._act
        if a is not None:
            a["time"] -= dt
            t = a["t"]
            gone = t is not None and (t not in self.things or (not t.isVisible() and a["stage"] == "go") or t.dragging)
            if c.state in (DRAG, FALL, KNOCK, SLEEP) or c._dragging or a["time"] <= 0 or gone:
                if a.get("pose") and c.user_pose == a["pose"]:
                    c.stand_up()
                if isinstance(t, Pet) and t.mode == "wait":
                    t.mode, t.mode_t = "idle", 1.0
                self._end_act()
                return
            name = a["name"]
            if a["stage"] == "go":
                goal = self._spot(name, t)
                her = c.global_rect().center().x()
                if abs(goal - her) > max(30.0, c.width() * 0.12):
                    if c.state != WALK and c.state in (IDLE, SIT):
                        c.walk_to(goal)
                    if isinstance(t, Pet) and name in ("pet", "treat", "cuddle"):
                        t.mode, t.mode_t = "wait", 3.0          # "stay~"
                    return
                a["stage"], a["k"] = "do", 0.0
                self._begin(a)
                return
            a["k"] = a.get("k", 0.0) + dt
            self._during(a, dt)
            return
        self._next_play -= dt
        if self._next_play > 0:
            return
        self._next_play = random.uniform(18, 40)
        if not self._her_free() or c.state not in (IDLE, SIT, WALK) or c.busy or c.talking \
                or getattr(c, "user_pose", None) or c.sulking:
            return
        opts = self._act_options()
        if opts:
            self.start_activity(*random.choice(opts))

    def _begin(self, a: dict) -> None:
        c, name, t = self.c, a["name"], a["t"]
        her = c.global_rect().center().x()
        if name == "pet" and isinstance(t, Pet):
            side = 1.0 if t.center().x() >= her else -1.0
            # the pet comes right under her hand, facing her
            t.place(her + side * (self._reach() + t.size * 0.2))
            t.facing = -side
            t.mode, t.mode_t = "wait", 6.0
            a["pose"] = "pet_l" if side > 0 else "pet_r"
            c.act_pose(a["pose"], 5.0)
            a["time"] = 5.5
            self.event.emit("her_pets", t.kind)
        elif name == "cuddle":
            self._hug(t, 9.0)
            self.event.emit("her_cuddles", t.kind)
            self._end_act()
        elif name == "hug":
            self._hug(t)
            self.event.emit("her_hugs", t.kind)
            self._end_act()
        elif name == "kick":
            self._kick(t)
            self._end_act()
        elif name == "yarn":
            cat = self.get("cat")
            d = 1.0 if cat is None or cat.center().x() >= t.center().x() else -1.0
            t.vx, t.vy = d * random.uniform(280, 420), -random.uniform(250, 380)
            c.kick()
            self.event.emit("yarn_play", "cat")
            self._end_act()
        elif name == "throw":                      # pick the ball up, wind up, throw it
            t.hide()
            c.hold("ball", 0.9)
            self._throwing = t
            a["time"] = 1.6
            self.event.emit("throw", t.kind)
        elif name == "treat" and isinstance(t, Pet):
            side = 1.0 if t.center().x() >= her else -1.0
            if self.get("treat") is None:
                tr = Toy(self, "treat", max(0.6, self.cfg.scale))
                r = c.global_rect()
                tr.place(her + side * self._reach() * 0.6, r.bottom() - r.height() * 0.45)
                tr.vx, tr.vy = side * 160, -220
                tr.show()
                self.things.append(tr)
            t.mode, t.mode_t = "idle", 0.5
            c.act_pose("pet_l" if side > 0 else "pet_r", 2.0)
            a["pose"] = "pet_l" if side > 0 else "pet_r"
            a["time"] = 2.2
            self.event.emit("treat", t.kind)
        elif name == "party":
            c.act_pose("dance", 8.0)
            self.party_t = 8.0
            a["time"] = 8.2
            self.event.emit("party", "")
        elif name == "read":
            c.act_pose("read", 22.0)
            c.hold("book", 22.0)
            a["time"] = 22.5
            self.event.emit("read", "")
        elif name == "tea":
            c.act_pose("tea", 14.0)
            c.hold("tea", 14.0)
            a["time"] = 14.5
            self.event.emit("tea", "")
        elif name == "exercise":
            c.act_pose("jumping_jacks", 7.0)
            a["time"] = 7.5
            self.event.emit("exercise", "")
        else:
            self._end_act()

    def _during(self, a: dict, dt: float) -> None:
        t = a["t"]
        if a["name"] == "pet" and isinstance(t, Pet):
            t.mode, t.mode_t = "wait", 1.0
            t.hearts = max(t.hearts, 1.5)
            if a["k"] > 1.0 and not a.get("purred"):
                a["purred"] = True
                t.say({"cat": "Purrr~ ♡", "puppy": "*wag wag wag*", "bunny": "*happy nose wiggle*"}[t.kind], 2.5)

    def eat(self, treat: Toy, pet: Pet) -> None:
        self.remove("treat")
        pet.hearts = 3.0
        pet.mode, pet.mode_t = "happy", 3.0
        pet.say("nom nom~ ♡", 2.0)
        self.event.emit("pet_ate", pet.kind)
