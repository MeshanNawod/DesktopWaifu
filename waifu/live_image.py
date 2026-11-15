"""Animate a single character picture, Live2D-style, with no rigging tools needed.

The picture is deformed every frame by a smooth displacement field. The field is a
sum of soft region masks (hair, scarf, skirt, head, hands, cheeks), and each mask is
driven by a damped spring ("bone"). The bones react to:
  * how the window moves (drag, throw, fall, walk): cloth and hair lag and swing
  * touches and tugs from the mouse
  * idle motion: breathing, a light breeze, the head following the cursor

Eyelids (blink, sleep, squint, happy ^^, eyes squeezed shut) are painted onto the
picture before the warp, using the skin colour sampled under each eye.

A character folder holds character.png (transparent) + character.json, which gives
the zones in normalised 0..1 picture coordinates. See assets/characters/README.md.
"""
from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPixmap,
                           QRadialGradient, QTransform)

from .sprites import DRAG, FALL, IDLE, PET, REACT, SIT, SLEEP, WALK, FrameContext, _heart, _pen

log = logging.getLogger(__name__)

try:
    import cv2 as _cv2
except Exception:  # optional: falls back to a slower numpy warp
    _cv2 = None

# hit-test priority: small precise zones first
ZONE_ORDER = ["eye_l", "eye_r", "mouth", "cheek_l", "cheek_r", "hand_face", "hand_low", "head",
              "chest", "scarf", "skirt", "hair_l", "hair_r"]
TUGGABLE = {"hand_face", "hand_low", "scarf", "hair_l", "hair_r", "skirt"}
FMT = QImage.Format.Format_RGBA8888_Premultiplied


@dataclass
class Bone:
    k: float            # stiffness
    c: float            # damping
    inertia: float      # response to window acceleration
    drag: float         # response to window velocity (air drag, trailing)
    limit: float        # max offset (render px; degrees for 'lean')
    ox: float = 0.0
    oy: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    tx: float = 0.0     # rest target
    ty: float = 0.0
    min_y: float | None = None   # e.g. stop the skirt flipping up

    def step(self, dt: float, fx: float, fy: float) -> None:
        ax = -self.k * (self.ox - self.tx) - self.c * self.vx + fx
        ay = -self.k * (self.oy - self.ty) - self.c * self.vy + fy
        self.vx += ax * dt
        self.vy += ay * dt
        self.ox += self.vx * dt
        self.oy += self.vy * dt
        lim = self.limit
        if abs(self.ox) > lim:
            self.ox = math.copysign(lim, self.ox)
            self.vx *= -0.3
        lo = -lim if self.min_y is None else self.min_y
        if self.oy > lim or self.oy < lo:
            self.oy = max(lo, min(lim, self.oy))
            self.vy *= -0.3

    def kick(self, vx: float, vy: float) -> None:
        self.vx += vx
        self.vy += vy


def _np_from_qimage(img: QImage) -> np.ndarray:
    img = img.convertToFormat(FMT)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes())
    return buf.reshape(h, img.bytesPerLine() // 4, 4)[:, :w].copy()


def _qimage_from_np(arr: np.ndarray, dpr: float = 1.0) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    img = QImage(arr.tobytes(), w, h, w * 4, FMT).copy()
    img.setDevicePixelRatio(dpr)
    return img


class LiveImageCharacter:
    mirrors_itself = True   # tells CharacterWindow not to mirror zone rects again

    def __init__(self, folder: str | Path, scale: float, dpr: float):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / "character.json").read_text(encoding="utf-8"))
        self.src = QImage(str(self.folder / self.meta.get("image", "character.png")))
        if self.src.isNull():
            raise FileNotFoundError(self.folder / self.meta.get("image", "character.png"))
        self.zones = self.meta["zones"]
        self.dh = round(self.meta.get("display_height", 420) * max(0.4, min(3.0, scale)))
        self.dw = round(self.src.width() * self.dh / self.src.height())
        self.mx = round(self.dw * 0.3)
        self.mt = 56
        self.W, self.H = self.dw + 2 * self.mx, self.dh + self.mt
        self._xf = QTransform().translate(self.mx, self.mt)
        self.look = [0.0, 0.0]
        self._look_target = [0.0, 0.0]
        self.timers = {"eye_shut": 0.0, "squint": 0.0, "wink": 0.0, "happy": 0.0, "blush": 0.0, "tears": 0.0}
        self._t = 0.0
        self._tugged: set[str] = set()
        self._build(dpr)

    # ------------------------------------------------------------------ setup
    def _build(self, dpr: float) -> None:
        self.dpr = dpr
        rw, rh = max(8, round(self.dw * dpr)), max(8, round(self.dh * dpr))
        self.rw, self.rh = rw, rh
        img = self.src.scaled(rw, rh, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        base = _np_from_qimage(img).astype(np.float32)
        fade = float(self.meta.get("bottom_fade", 0.0))
        if fade > 0:  # soften the cut-off at the bottom of the picture
            n = max(1, int(rh * fade))
            ramp = np.linspace(1.0, 0.0, n, dtype=np.float32) ** 1.5
            base[rh - n:] *= ramp[:, None, None]
        self.base = base.astype(np.uint8)
        self.alpha = self.base[..., 3]
        self.X, self.Y = np.meshgrid(np.arange(rw, dtype=np.float32), np.arange(rh, dtype=np.float32))
        self._variants: dict[tuple, np.ndarray] = {}
        self._skin = {e: self._sample_skin(e) for e in ("eye_l", "eye_r")}

        phys = self.meta.get("physics", {})
        self.masks = {name: self._mask(spec) for name, spec in phys.items()}
        self.eye_mask = sum(self._mask({"ellipse": self._scaled_ellipse(e, 0.85), "blur": 0.006})
                            for e in ("eye_l", "eye_r"))
        sh, wa = self.meta.get("shoulder_y", 0.4), self.meta.get("waist_y", 0.72)
        v = np.linspace(0, 1, rh, dtype=np.float32)[:, None]
        self.breath_w = np.clip((wa - v) / max(1e-3, wa - sh), 0, 1)

        s = rh / 700.0
        self.bones = {
            "hair_l": Bone(40, 5, 0.09 * s, 1.8 * s, 0.05 * rh),
            "hair_r": Bone(42, 5, 0.09 * s, 1.8 * s, 0.05 * rh),
            "scarf": Bone(50, 5, 0.07 * s, 1.5 * s, 0.045 * rh),
            "skirt": Bone(70, 7, 0.05 * s, 1.0 * s, 0.03 * rh, min_y=-0.006 * rh),
            "head": Bone(90, 13, 0.01 * s, 0.0, 0.025 * rh),
            "hand_face": Bone(110, 13, 0.015 * s, 0.2 * s, 0.04 * rh),
            "hand_low": Bone(110, 13, 0.02 * s, 0.3 * s, 0.04 * rh),
            "cheek_l": Bone(220, 20, 0.0, 0.0, 0.02 * rh),
            "cheek_r": Bone(220, 20, 0.0, 0.0, 0.02 * rh),
            "lean": Bone(30, 6, 0.0012, 0.0, 9.0),
        }

    def _scaled_ellipse(self, zone: str, k: float) -> list[float]:
        cx, cy, rx, ry = self.zones[zone]["ellipse"]
        return [cx, cy, rx * k, ry * k]

    def _mask(self, spec: dict) -> np.ndarray:
        rw, rh = self.rw, self.rh
        im = Image.new("L", (rw, rh), 0)
        d = ImageDraw.Draw(im)
        if "poly" in spec:
            d.polygon([(x * rw, y * rh) for x, y in spec["poly"]], fill=255)
        elif "ellipse" in spec:
            cx, cy, rx, ry = spec["ellipse"]
            d.ellipse([(cx - rx) * rw, (cy - ry) * rh, (cx + rx) * rw, (cy + ry) * rh], fill=255)
        else:
            x, y, w, h = spec["rect"]
            d.rectangle([x * rw, y * rh, (x + w) * rw, (y + h) * rh], fill=255)
        im = im.filter(ImageFilter.GaussianBlur(max(1.0, spec.get("blur", 0.015) * rh)))
        m = np.asarray(im, np.float32) / 255.0
        if "grad" in spec:
            g0, g1 = spec["grad"]
            v = np.linspace(0, 1, rh, dtype=np.float32)[:, None]
            m = m * np.clip((v - g0) / max(1e-3, g1 - g0), 0, 1) ** 1.2
        return m

    def _sample_skin(self, eye: str) -> QColor:
        cx, cy, rx, ry = self.zones[eye]["ellipse"]
        x, y = int(cx * self.rw), int(min(0.999, cy + ry * 1.9) * self.rh)
        patch = self.base[max(0, y - 2):y + 3, max(0, x - 2):x + 3].reshape(-1, 4).astype(np.float32)
        a = max(1.0, patch[:, 3].mean())
        r, g, b = (patch[:, :3].mean(axis=0) * 255.0 / a).clip(0, 255)
        return QColor(int(r), int(g), int(b))

    # ------------------------------------------------------------------ interface
    def size(self) -> QSize:
        return QSize(self.W, self.H)

    def foot_offset(self) -> int:
        return 2

    def _zone_rect_img(self, name: str) -> QRectF:
        z = self.zones[name]
        if "ellipse" in z:
            cx, cy, rx, ry = z["ellipse"]
            return QRectF((cx - rx) * self.dw, (cy - ry) * self.dh, 2 * rx * self.dw, 2 * ry * self.dh)
        x, y, w, h = z["rect"]
        return QRectF(x * self.dw, y * self.dh, w * self.dw, h * self.dh)

    def head_rect(self, state: str) -> QRectF:
        return self._xf.mapRect(self._zone_rect_img("head"))

    def icon(self) -> QPixmap:
        w, h = self.src.width(), self.src.height()
        side = int(w * 0.5)
        crop = self.src.copy(int(w * 0.24), 0, side, side)
        return QPixmap.fromImage(crop.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))

    def to_image(self, local: QPointF) -> tuple[float, float] | None:
        inv, ok = self._xf.inverted()
        if not ok:
            return None
        p = inv.map(local)
        return p.x() / self.dw, p.y() / self.dh

    def zone_at(self, local: QPointF) -> str | None:
        uv = self.to_image(local)
        if uv is None:
            return None
        u, v = uv
        if not (0 <= u < 1 and 0 <= v < 1):
            return None
        if self.alpha[int(v * self.rh), int(u * self.rw)] < 20:
            return None
        for name in ZONE_ORDER:
            z = self.zones.get(name)
            if not z:
                continue
            if "ellipse" in z:
                cx, cy, rx, ry = z["ellipse"]
                if ((u - cx) / rx) ** 2 + ((v - cy) / ry) ** 2 <= 1.0:
                    return name
            else:
                x, y, w, h = z["rect"]
                if x <= u <= x + w and y <= v <= y + h:
                    return name
        return "body"

    def look_at(self, local: QPointF | None) -> None:
        if local is None:
            self._look_target = [0.0, 0.0]
            return
        uv = self.to_image(local)
        if uv is None:
            return
        ex = (self.zones["eye_l"]["ellipse"][0] + self.zones["eye_r"]["ellipse"][0]) / 2
        ey = (self.zones["eye_l"]["ellipse"][1] + self.zones["eye_r"]["ellipse"][1]) / 2
        dx, dy = (uv[0] - ex) * self.dw, (uv[1] - ey) * self.dh
        d = math.hypot(dx, dy)
        if d < 1:
            self._look_target = [0.0, 0.0]
            return
        k = min(1.0, d / (self.dh * 0.7)) / d
        self._look_target = [dx * k, dy * k]

    def react(self, zone: str, kind: str = "tap") -> None:
        """Physical reaction to a click/touch on a zone."""
        b, rh = self.bones, self.rh
        side = random.choice((-1, 1))
        if kind == "rough":   # flinch: eyes squeezed shut, knocked back, everything swings
            self.timers["eye_shut"] = 1.3
            self.timers["tears"] = 4.0
            b["head"].kick(side * 0.15 * rh, -0.25 * rh)
            b["lean"].kick(side * 40, 0)
            for name in ("hair_l", "hair_r", "scarf", "skirt"):
                b[name].kick(side * 0.4 * rh, 0)
            return
        if zone == "mouth":
            self.timers["blush"] = 3.0
            self.timers["happy"] = 1.2
            b["head"].kick(0, -0.08 * rh)
            return
        if zone in ("eye_l", "eye_r"):
            self.timers["eye_shut"] = 1.3
            b["head"].kick(0, -0.25 * rh)
            b["lean"].kick(-side * 25, 0)
        elif zone in ("cheek_l", "cheek_r"):
            sign = 1 if zone == "cheek_l" else -1  # push towards the face centre
            b[zone].kick(sign * 0.35 * rh, 0)
            b["head"].kick(-sign * 0.12 * rh, 0)
            self.timers["blush"] = 3.0
            self.timers["squint"] = 0.6
        elif zone in ("hand_face", "hand_low"):
            b[zone].kick(side * 0.15 * rh, 0.1 * rh)
            self.timers["blush"] = 3.5
            self.timers["wink"] = 0.6
        elif zone == "head":
            self.timers["happy"] = 2.5
            self.timers["blush"] = 2.5
            b["head"].kick(0, 0.12 * rh)
        elif zone == "scarf":
            b["scarf"].kick(side * 0.9 * rh, 0.2 * rh)
            b["head"].kick(0, 0.1 * rh)
        elif zone in ("hair_l", "hair_r"):
            b[zone].kick((-1 if zone == "hair_l" else 1) * 0.9 * rh, 0)
            b["head"].kick((-1 if zone == "hair_l" else 1) * 0.1 * rh, 0)
        elif zone == "skirt":
            b["skirt"].kick(side * 0.3 * rh, 0.1 * rh)
            b["lean"].kick(side * 20, 0)
            self.timers["blush"] = 3.0
        elif zone == "chest":
            b["lean"].kick(-35 if random.random() < 0.5 else 35, 0)
            b["head"].kick(0, -0.15 * rh)
            self.timers["blush"] = 3.0
        else:  # body / arm poke
            b["lean"].kick(side * 18, 0)
            b["hair_l"].kick(side * 0.2 * rh, 0)
            b["hair_r"].kick(side * 0.2 * rh, 0)

    def tug(self, zone: str, delta: QPointF | None) -> None:
        """Hold-and-pull a soft part. delta is the cursor offset in window px; None = let go."""
        bone = {"hand_face": "hand_face", "hand_low": "hand_low", "scarf": "scarf",
                "hair_l": "hair_l", "hair_r": "hair_r", "skirt": "skirt"}.get(zone)
        if not bone:
            return
        b = self.bones[bone]
        if delta is None:
            b.tx = b.ty = 0.0
            self._tugged.discard(bone)
            return
        self._tugged.add(bone)
        sx = -1 if self._xf.m11() < 0 else 1   # un-mirror
        k = 0.7 * self.dpr
        b.tx = max(-b.limit, min(b.limit, delta.x() * k * sx))
        b.ty = max(-b.limit, min(b.limit, delta.y() * k))
        if bone == "skirt":
            b.ty = max(0.0, b.ty)  # never lift the skirt
        if bone in ("hand_face", "hand_low"):
            self.timers["blush"] = max(self.timers["blush"], 1.0)

    def squint(self, on: bool) -> None:
        if on:
            self.timers["squint"] = max(self.timers["squint"], 0.25)

    # ------------------------------------------------------------------ simulation
    def step(self, dt: float, ax: float, ay: float, vx: float, vy: float,
             state: str, ctx: FrameContext) -> None:
        self._t += dt
        t, rh, b = self._t, self.rh, self.bones
        for k in self.timers:
            self.timers[k] = max(0.0, self.timers[k] - dt)
        # smooth gaze (cursor, blended with the expression's glance)
        ex = ctx.expr
        for i in (0, 1):
            goal = self._look_target[i]
            if ex is not None:
                goal = goal * ex.follow + ex.look[i] * 0.7
            self.look[i] += (goal - self.look[i]) * min(1.0, dt * 6)

        # rest targets
        h = b["head"]
        h.tx = self.look[0] * 0.010 * rh
        h.ty = self.look[1] * 0.006 * rh - ctx.mouth * 0.007 * rh
        emo = ctx.emotion
        if state == SLEEP:
            h.tx += 0.006 * rh
            h.ty += 0.014 * rh
        elif emo == "sad":
            h.ty += 0.010 * rh
        elif emo == "thinking":
            h.tx += 0.008 * rh
        if state == PET:
            h.tx += math.sin(t * 4) * 0.006 * rh
        if "hand_face" not in self._tugged:   # her hand moves a little while she talks
            b["hand_face"].tx, b["hand_face"].ty = 0.0, ctx.mouth * 0.004 * rh
        for name, ph, amp in (("hair_l", 0.0, 1.0), ("hair_r", 1.1, 1.0), ("scarf", 2.0, 0.6)):
            if name not in self._tugged:      # light breeze
                b[name].tx = (math.sin(t * 1.3 + ph) * 0.006 + math.sin(t * 2.9 + ph) * 0.002) * rh * amp
        lean = b["lean"]
        lean.tx = 0.0
        if state == WALK:
            lean.tx = -5.0 if ctx.facing_left else 5.0
        elif ctx.music:
            lean.tx = math.sin(t * 2.2) * 3.0
            h.ty += ctx.beat * 0.008 * rh
        elif ctx.emotion == "hurt":
            lean.tx = -3.0 if self.look[0] >= 0 else 3.0   # leans away, sulking
        elif state in (IDLE, SIT):
            lean.tx = math.sin(t * 0.6) * 0.8

        # integrate with window motion as the driving force
        ax, ay = max(-30000.0, min(30000.0, ax)), max(-30000.0, min(30000.0, ay))
        steps = max(1, int(dt / 0.012) + 1)
        sdt = dt / steps
        for _ in range(steps):
            for name, bn in b.items():
                if name == "lean":
                    bn.step(sdt, -bn.inertia * ax * 15 * (1 if state == DRAG else 0.4), 0.0)
                else:
                    fx = -bn.inertia * ax - bn.drag * vx
                    fy = -bn.inertia * ay - bn.drag * vy * 0.3
                    bn.step(sdt, fx, fy)

    # ------------------------------------------------------------------ rendering
    def _eye_state(self, state: str, ctx: FrameContext) -> tuple[float, float, str]:
        lid = 1.0 if ctx.blink else 0.0
        mode = "open"
        if state == SLEEP:
            lid = 1.0
        elif state == SIT:
            lid = max(lid, 0.25)
        if ctx.emotion == "sleepy":
            lid = max(lid, 0.5)
        if self.timers["squint"] > 0:
            lid = max(lid, 0.5)
        ex = ctx.expr
        if ex is not None:
            lid = max(lid, ex.lid * 0.8)
            if ex.happy_eyes and self.timers["eye_shut"] <= 0 and state != SLEEP:
                return 1.0, 1.0, "happy"
            if ex.wink:
                self.timers["wink"] = max(self.timers["wink"], 0.05)
        happy = (state == PET or self.timers["happy"] > 0 or
                 (ctx.emotion in ("happy", "excited") and int(ctx.t * 0.6) % 3 == 0))
        if self.timers["eye_shut"] > 0:
            return 1.0, 1.0, "shut"
        if happy and state != SLEEP:
            return 1.0, 1.0, "happy"
        lid_l, lid_r = lid, lid
        if self.timers["wink"] > 0:
            lid_r = 1.0
        return lid_l, lid_r, mode

    def _variant(self, lid_l: float, lid_r: float, mode: str) -> np.ndarray:
        key = (round(lid_l * 4), round(lid_r * 4), mode)
        cached = self._variants.get(key)
        if cached is not None:
            return cached
        img = _qimage_from_np(self.base)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lid_col = QColor(self.meta.get("lid_color", "#3a2420"))
        for eye, q in (("eye_l", key[0] / 4), ("eye_r", key[1] / 4)):
            if q <= 0 and mode == "open":
                continue
            cx, cy, rx, ry = self.zones[eye]["ellipse"]
            cx, cy, rx, ry = cx * self.rw, cy * self.rh, rx * self.rw, ry * self.rh
            skin = self._skin[eye]
            area = QRectF(cx - rx * 1.12, cy - ry * 1.15, rx * 2.24, ry * 2.3)
            pen_w = max(1.5, ry * 0.32)
            path = QPainterPath()
            if mode in ("happy", "shut") or q >= 1:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(skin)
                p.drawEllipse(area)
                if mode == "happy":       # ^ ^
                    path.moveTo(cx - rx * 0.85, cy + ry * 0.35)
                    path.quadTo(cx, cy - ry * 0.95, cx + rx * 0.85, cy + ry * 0.35)
                elif mode == "shut":      # > <  squeezed shut
                    inward = 1 if eye == "eye_l" else -1
                    path.moveTo(cx - inward * rx * 0.8, cy - ry * 0.55)
                    path.lineTo(cx + inward * rx * 0.55, cy)
                    path.lineTo(cx - inward * rx * 0.8, cy + ry * 0.55)
                else:                     # peaceful closed eye
                    path.moveTo(cx - rx * 0.85, cy + ry * 0.05)
                    path.quadTo(cx, cy + ry * 0.85, cx + rx * 0.85, cy + ry * 0.05)
            else:                         # eyelid partly down
                clip = QPainterPath()
                clip.addEllipse(area)
                p.setClipPath(clip)
                y_lid = area.top() + area.height() * q * 0.9
                p.fillRect(QRectF(area.left(), area.top(), area.width(), y_lid - area.top()), skin)
                p.setClipping(False)
                path.moveTo(cx - rx * 1.0, y_lid + ry * 0.1)
                path.quadTo(cx, y_lid - ry * 0.25, cx + rx * 1.0, y_lid + ry * 0.1)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(_pen(lid_col, pen_w))
            p.drawPath(path)
        p.end()
        arr = _np_from_qimage(img)
        self._variants[key] = arr
        return arr

    def _padded(self, arr: np.ndarray) -> np.ndarray:
        key = id(arr)
        cache = getattr(self, "_pad_cache", None)
        if cache is None or cache[0] != key:
            p = np.pad(arr, ((1, 1), (1, 1), (0, 0))).astype(np.float32)
            self._pad_cache = (key, p.reshape(-1, 4), p.shape[1], p.shape[0])
        return self._pad_cache

    def _warp(self, src: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
        if _cv2 is not None:  # ~20x faster
            return _cv2.remap(src, self.X - dx, self.Y - dy, _cv2.INTER_LINEAR,
                              borderMode=_cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        _, flat, wp, hp = self._padded(src)
        sx = np.clip(self.X - dx + 1.0, 0.0, wp - 1.001)
        sy = np.clip(self.Y - dy + 1.0, 0.0, hp - 1.001)
        x0 = sx.astype(np.int32)
        y0 = sy.astype(np.int32)
        fx = (sx - x0)[..., None]
        fy = (sy - y0)[..., None]
        i00 = y0 * wp + x0
        a = flat.take(i00, axis=0)
        bb = flat.take(i00 + 1, axis=0)
        c = flat.take(i00 + wp, axis=0)
        d = flat.take(i00 + wp + 1, axis=0)
        top = a + (bb - a) * fx
        bot = c + (d - c) * fx
        return (top + (bot - top) * fy + 0.5).astype(np.uint8)

    def _field(self, state: str) -> tuple[np.ndarray, np.ndarray]:
        rh = self.rh
        dx = np.zeros((self.rh, self.rw), np.float32)
        dy = np.zeros_like(dx)
        for name, m in self.masks.items():
            bn = self.bones.get(name)
            if bn is None:
                continue
            if abs(bn.ox) > 0.05:
                dx += m * bn.ox
            if abs(bn.oy) > 0.05:
                dy += m * bn.oy
        # eyes follow the cursor
        ex, ey = self.look[0] * 0.005 * rh, self.look[1] * 0.004 * rh
        if abs(ex) > 0.05 or abs(ey) > 0.05:
            dx += self.eye_mask * ex
            dy += self.eye_mask * ey
        # breathing lifts the upper body
        period = 5.0 if state == SLEEP else 3.6
        amp = (0.006 if state == SLEEP else 0.004) * rh
        br = (math.sin(self._t * math.tau / period) * 0.5 + 0.5) * amp
        dy -= self.breath_w * br
        return dx, dy

    def render(self, state: str, ctx: FrameContext, dpr: float) -> QImage:
        if abs(dpr - self.dpr) > 0.01:
            self._build(dpr)
        lid_l, lid_r, mode = self._eye_state(state, ctx)
        src = self._variant(lid_l, lid_r, mode) if (lid_l > 0 or lid_r > 0 or mode != "open") else self.base
        dx, dy = self._field(state)
        warped = _qimage_from_np(self._warp(src, dx, dy), dpr)

        canvas = QImage(round(self.W * dpr), round(self.H * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        dw, dh = self.dw, self.dh
        xf = QTransform()
        xf.translate(self.mx, self.mt)
        if state == WALK:
            xf.translate(0, -abs(math.sin(ctx.t * 7.0)) * dh * 0.012)
        if ctx.music:   # bob to the beat
            xf.translate(0, ctx.beat * dh * 0.008)
        if state == REACT:
            xf.translate(0, -max(0.0, 6 - ctx.state_t * 20))
        angle = self.bones["lean"].ox
        if state == DRAG:
            px, py = dw / 2, dh * 0.12
            angle += ctx.tilt * 0.5
        else:
            px, py = dw / 2, dh
        xf.translate(px, py)
        xf.rotate(angle)
        if state in (SIT, SLEEP):
            xf.scale(1.0, 0.975)
        xf.translate(-px, -py)
        if ctx.facing_left:
            xf.translate(dw, 0)
            xf.scale(-1, 1)
        self._xf = xf
        p.setTransform(xf)
        p.drawImage(QRectF(0, 0, dw, dh), warped)
        self._draw_face_fx(p, state, ctx)
        p.resetTransform()
        self._draw_screen_fx(p, state, ctx)
        p.end()
        return canvas

    def _head_off(self) -> QPointF:
        h = self.bones["head"]
        return QPointF(h.ox / self.dpr, h.oy / self.dpr)

    def _img_pt(self, u: float, v: float) -> QPointF:
        return QPointF(u * self.dw, v * self.dh) + self._head_off()

    def _draw_face_fx(self, p: QPainter, state: str, ctx: FrameContext) -> None:
        emo = ctx.emotion
        blush = {"shy": 0.8, "flustered": 1.0, "happy": 0.45, "excited": 0.5, "playful": 0.35}.get(emo, 0.12)
        if ctx.expr is not None:
            blush = max(blush, ctx.expr.blush)
            if ctx.expr.sparkle:
                for eye in ("eye_l", "eye_r"):
                    cx, cy, rx, ry = self.zones[eye]["ellipse"]
                    c = self._img_pt(cx - rx * 0.3, cy - ry * 0.3)
                    tw = 0.6 + 0.4 * math.sin(ctx.t * 8 + cx * 10)
                    p.setPen(_pen(QColor(255, 255, 255, int(230 * tw)), 1.2))
                    for dx, dy in ((2.5, 0), (0, 2.5)):
                        p.drawLine(c + QPointF(-dx, -dy), c + QPointF(dx, dy))
                p.setPen(Qt.PenStyle.NoPen)
        if state == PET or self.timers["blush"] > 0:
            blush = max(blush, 0.9)
        p.setPen(Qt.PenStyle.NoPen)
        for cheek in ("cheek_l", "cheek_r"):
            cx, cy, rx, ry = self.zones[cheek]["ellipse"]
            c = self._img_pt(cx, cy)
            r = rx * self.dw * 1.1
            g = QRadialGradient(c, r)
            g.setColorAt(0, QColor(255, 90, 120, int(110 * blush)))
            g.setColorAt(1, QColor(255, 90, 120, 0))
            p.setBrush(g)
            p.drawEllipse(c, r, r * 0.55)
            if blush > 0.7:  # anime blush hatching
                p.setPen(_pen(QColor(230, 60, 90, 150), 1.0))
                for k in (-1, 0, 1):
                    x = c.x() + k * r * 0.35
                    p.drawLine(QPointF(x - 1.5, c.y() + 2.5), QPointF(x + 1.5, c.y() - 2.5))
                p.setPen(Qt.PenStyle.NoPen)
        if emo == "angry":
            c = self._img_pt(0.64, 0.07)
            p.setPen(_pen(QColor("#ff3b3b"), 2.4))
            s = self.dh * 0.012
            for a, b in (((-1, -0.3), (-0.3, -0.3)), ((-0.3, -1), (-0.3, -0.3)),
                         ((1, 0.3), (0.3, 0.3)), ((0.3, 1), (0.3, 0.3))):
                p.drawLine(c + QPointF(a[0] * s, a[1] * s), c + QPointF(b[0] * s, b[1] * s))
        if emo == "surprised" or self.timers["eye_shut"] > 0:
            c = self._img_pt(0.69, 0.15)
            self._drop(p, c, self.dh * 0.02, QColor(120, 180, 255, 200))
        if emo in ("sad", "hurt") or self.timers["tears"] > 0:
            cx, cy, rx, ry = self.zones["eye_l"]["ellipse"]
            slide = (ctx.t * 0.5) % 1.0
            c = self._img_pt(cx - rx * 0.6, cy + ry * 1.2 + slide * 0.04)
            self._drop(p, c, self.dh * 0.012, QColor(140, 200, 255, int(220 * (1 - slide))))

    @staticmethod
    def _drop(p: QPainter, c: QPointF, s: float, col: QColor) -> None:
        path = QPainterPath()
        path.moveTo(c.x(), c.y() - s)
        path.cubicTo(c.x() + s * 0.9, c.y() + s * 0.2, c.x() + s * 0.6, c.y() + s, c.x(), c.y() + s)
        path.cubicTo(c.x() - s * 0.6, c.y() + s, c.x() - s * 0.9, c.y() + s * 0.2, c.x(), c.y() - s)
        p.setPen(_pen(col.darker(130), 1.0))
        p.setBrush(col)
        p.drawPath(path)

    def _draw_screen_fx(self, p: QPainter, state: str, ctx: FrameContext) -> None:
        t = ctx.t
        top = self._xf.map(self._img_pt(0.47, 0.04))
        if ctx.pet_level > 0:
            for i in range(3):
                ph = (t * 0.7 + i / 3) % 1.0
                col = QColor("#ff5c7a")
                col.setAlphaF(max(0.0, min(1.0, ctx.pet_level)) * (1 - ph))
                _heart(p, top.x() + (i - 1) * 26 + math.sin(t * 3 + i) * 4, top.y() - 10 - ph * 34, 12, col)
        if state == SLEEP:
            p.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            for i in range(2):
                ph = (t * 0.4 + i / 2) % 1.0
                col = QColor("#7b8cff")
                col.setAlphaF(1 - ph)
                p.setPen(col)
                p.drawText(QPointF(top.x() + 34 + ph * 16, top.y() + 10 - ph * 36), "z")
