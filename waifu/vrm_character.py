"""3D VRM desktop character: procedural animation, spring physics, expressions, touch zones.

Implements the same provider interface as the 2D characters (size, foot_offset, render,
head_rect, icon, step, zone_at, look_at, react, tug, squint), so CharacterWindow can
use it unchanged.
"""
from __future__ import annotations

import logging
import math
import random
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap, QRadialGradient

from .sprites import DRAG, FALL, IDLE, PET, REACT, SIT, SLEEP, WALK, FrameContext, _heart, _pen, draw_steam
from .vrm.math3d import (IDENTITY_Q, normalize, ortho, q_axis, q_euler, q_from_vectors, q_inv,
                         q_mul, q_rotate, q_slerp, q_to_m3, rot_m4, translate)
from .vrm.model import R180, VrmModel
from .vrm.renderer import VrmRenderer
from .vrm.springs import SpringSystem

log = logging.getLogger(__name__)

TOP_M, BOT_M, WIDTH_M = 0.14, 0.30, 0.72   # margins / width as fractions of model height
EMOTION_EXPR = {
    "happy": {"happy": 1.0}, "excited": {"happy": 1.0, "aa": 0.25}, "shy": {"relaxed": 0.6, "happy": 0.35},
    "sad": {"sad": 1.0}, "angry": {"angry": 1.0}, "surprised": {"surprised": 1.0},
    "thinking": {"relaxed": 0.25}, "sleepy": {"relaxed": 0.5, "blink": 0.45}, "neutral": {},
    "hurt": {"sad": 0.85, "angry": 0.2},
    "flustered": {"surprised": 0.35, "relaxed": 0.35}, "playful": {"happy": 0.6, "ih": 0.2},
    "struggling": {"surprised": 0.55, "angry": 0.3, "aa": 0.35},
    "pout": {"angry": 0.3, "ou": 0.6},
}
ARM_DOWN = 77.0


def _rx(d): return q_axis((1, 0, 0), d)
def _ry(d): return q_axis((0, 1, 0), d)
def _rz(d): return q_axis((0, 0, 1), d)


# ---------------------------------------------------------------------------- gestures (Seed-san 2)
# Canonical VRM 1.0 frame: she faces +Z, her left arm points +X in the T-pose.
# Left arm: _rz(+) raises, _rz(-) lowers.  Right arm: mirrored.  Forearm forward: left _ry(-), right _ry(+).
GESTURE_LEN = {"wave_l": 2.4, "wave_r": 2.4, "stretch": 3.2, "look_around": 3.4, "hands_behind": 4.5,
               "hair_touch": 2.6, "highfive_l": 1.6, "highfive_r": 1.6, "explain": 2.2, "cheer": 2.0,
               "shy_hands": 4.0}


def gesture_pose(name: str, gt: float) -> dict[str, np.ndarray]:
    w = math.sin(gt * 11)
    if name in ("wave_l", "wave_r"):
        s, side = (1, "left") if name == "wave_l" else (-1, "right")
        return {f"{side}UpperArm": q_mul(_rx(-15), _rz(s * -15)),
                f"{side}LowerArm": _rz(s * (115 + w * 22)),
                f"{side}Hand": _rz(s * w * 12), "head": _rz(-s * 6)}
    if name == "stretch":
        return {"leftUpperArm": _rz(80), "rightUpperArm": _rz(-80), "leftLowerArm": _rz(8),
                "rightLowerArm": _rz(-8), "spine": _rx(-8), "chest": _rx(-6), "head": _rx(-12)}
    if name == "look_around":
        return {"head": _ry(math.sin(gt * 1.9) * 38), "neck": _ry(math.sin(gt * 1.9) * 12)}
    if name == "hands_behind":
        return {"leftUpperArm": q_mul(_rx(28), _rz(-72)), "rightUpperArm": q_mul(_rx(28), _rz(72)),
                "leftLowerArm": _ry(45), "rightLowerArm": _ry(-45), "spine": _rz(math.sin(gt * 1.6) * 3)}
    if name == "hair_touch":
        return {"rightUpperArm": q_mul(_rx(-35), _rz(-25)), "rightLowerArm": q_mul(_ry(55), _rz(-95)),
                "rightHand": _rz(math.sin(gt * 3) * 8), "head": _rz(9)}
    if name in ("highfive_l", "highfive_r"):
        s, side = (1, "left") if name == "highfive_l" else (-1, "right")
        return {f"{side}UpperArm": q_mul(_rx(-45), _rz(s * 55)), f"{side}LowerArm": _rz(s * 20),
                f"{side}Hand": _rx(-20)}
    if name == "explain":
        return {"rightUpperArm": q_mul(_rx(-18), _rz(68)), "rightLowerArm": q_mul(_ry(75), _rz(-10)),
                "rightHand": _rx(-25 + w * 6)}
    if name == "cheer":
        b = abs(math.sin(gt * 6))
        return {"leftUpperArm": _rz(62 - b * 18), "rightUpperArm": _rz(-62 + b * 18),
                "leftLowerArm": _rz(12), "rightLowerArm": _rz(-12)}
    if name == "shy_hands":
        return {"leftUpperArm": q_mul(q_mul(_rx(-10), _ry(-20)), _rz(-78)),
                "rightUpperArm": q_mul(q_mul(_rx(-10), _ry(20)), _rz(78)),
                "leftLowerArm": q_mul(_ry(-60), _rz(-30)), "rightLowerArm": q_mul(_ry(60), _rz(30)),
                "head": q_mul(_rx(8), _rz(6)),
                "leftShoulder": _rz(6), "rightShoulder": _rz(-6)}
    return {}


def gesture_for(face: str, emotion: str) -> str:
    """Pick an idle gesture that matches her current expression / mood."""
    if face == "blush" or emotion == "shy":
        return random.choice(["shy_hands", "hair_touch"])
    if face == "teasing_smirk" or emotion == "playful":
        return random.choice(["hair_touch", "wave_l", "look_around"])
    if face == "beaming" or emotion in ("happy", "excited"):
        return random.choice(["cheer", "wave_r", "look_around"])
    if face in ("sorrowful", "pout") or emotion in ("sad", "hurt", "angry"):
        return "hands_behind"
    if emotion == "sleepy":
        return "stretch"
    return random.choice(["look_around", "hands_behind", "stretch", "hair_touch"])


class VrmCharacter:
    mirrors_itself = True

    def __init__(self, path: str | Path, scale: float, dpr: float, enhanced: bool = False):
        self.enhanced = enhanced
        self._grab_blend = 0.0
        self._grab_last = None
        self._pin_M = None
        self.gesture: str | None = None
        self.gesture_t = 0.0
        self._next_gesture = random.uniform(6, 12)
        self._saccade = np.zeros(2)
        self._next_saccade = 1.0
        self._cursor_far = True
        self._last_wave = -99.0
        self.m = m = VrmModel(path)
        self.k = m.height / 1.58                       # size relative to a typical 1.58 m avatar
        self.fig_px = round(300 * max(0.4, min(3.0, scale)))
        self.ppm = self.fig_px / m.height              # logical px per metre
        self.W = round(WIDTH_M * m.height * self.ppm)
        self.H = round((1 + TOP_M + BOT_M) * m.height * self.ppm)
        self.dpr = dpr
        self.gl = VrmRenderer(m, round(self.W * dpr), round(self.H * dpr), realism=1.0 if enhanced else 0.0)
        self.springs = SpringSystem(m)
        self.hair_springs, self.cloth_springs = self._classify_springs()

        self.t = 0.0
        self.off = np.zeros(3)                         # model position in world (metres)
        self.yaw = 0.0
        self.cur_pose: dict[str, np.ndarray] = {}
        self.hip_off = np.zeros(3)
        self.expr = {name: 0.0 for name in m.expressions}
        self.look = [0.0, 0.0]
        self._look_target = [0.0, 0.0]
        self.timers = {"eye_shut": 0.0, "blush": 0.0, "lean": 0.0, "jerk": 0.0, "wave_l": 0.0, "wave_r": 0.0,
                       "tears": 0.0, "steam": 0.0}
        self.lean_sign = 1.0
        self._tug_hand: tuple[str, QPointF] | None = None
        self._pinned: list = []
        self._press_local = QPointF()
        self._W = None
        self._vp = np.eye(4)
        self._frame: QImage | None = None
        self._root_rot = IDENTITY_Q.copy()
        self.expr_bias = 0.0
        self.step(1 / 30, 0, 0, 0, 0, IDLE, FrameContext())

    # ------------------------------------------------------------------ setup
    def _classify_springs(self):
        head = self.m.humanoid.get("head")
        head_desc = self.m.descendants(head) if head is not None else set()
        hair, cloth = set(), set()
        for j in self.springs.joints:
            (hair if j.node in head_desc else cloth).add(id(j))
        return hair, cloth

    # ------------------------------------------------------------------ interface
    def size(self) -> QSize:
        return QSize(self.W, self.H)

    def foot_offset(self) -> int:
        return round(BOT_M * self.m.height * self.ppm)

    def project(self, p: np.ndarray) -> tuple[float, float]:
        c = self._vp @ np.append(p, 1.0)
        x, y = c[0] / c[3], c[1] / c[3]
        return (x + 1) / 2 * self.W, (1 - y) / 2 * self.H

    def _canon(self, v) -> np.ndarray:
        """Canonical-frame vector (VRM1: +Z forward, +X = her left) to world, scaled by model size."""
        return q_rotate(self._root_rot, np.asarray(v, float) * self.k)

    def _head_center(self) -> np.ndarray:
        hp = self.m.bone_pos(self._W, "head")
        if hp is None:
            return self.off + np.array([0, self.m.height * 0.9, 0])
        return hp + self._canon((0, 0.085, 0.01))

    def head_rect(self, state: str) -> QRectF:
        cx, cy = self.project(self._head_center())
        r = 0.12 * self.k * self.ppm
        return QRectF(cx - r, cy - r, 2 * r, 2 * r)

    def icon(self) -> QPixmap:
        img = self._frame or self.render(IDLE, FrameContext(), self.dpr)
        r = self.head_rect(IDLE)
        d = img.devicePixelRatio()
        crop = img.copy(int(r.x() * d), int(r.y() * d), int(r.width() * d), int(r.height() * d))
        return QPixmap.fromImage(crop.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))

    # ------------------------------------------------------------------ zones
    def _zones(self):
        W, m, k = self._W, self.m, self.k
        hc = self._head_center()
        z = []
        for side, bone, sx in (("eye_l", "leftEye", 1), ("eye_r", "rightEye", -1)):
            p = m.bone_pos(W, bone)
            if p is None:
                p = hc + self._canon((sx * 0.032, -0.012, 0.075))
            z.append((side, p, 0.024 * k))
        for side, sx in (("cheek_l", 1), ("cheek_r", -1)):
            z.append((side, hc + self._canon((sx * 0.048, -0.045, 0.065)), 0.026 * k))
        z.insert(0, ("nose", hc + self._canon((0, -0.032, 0.088)), 0.014 * k))
        z.append(("mouth", hc + self._canon((0, -0.062, 0.07)), 0.02 * k))
        for side, bone in (("hand_l", "leftHand"), ("hand_r", "rightHand")):
            p = m.bone_pos(W, bone)
            if p is not None:
                mid = m.bone_pos(W, bone.replace("Hand", "MiddleProximal"))
                z.append((side, (p + mid) / 2 if mid is not None else p, 0.065 * k))
        for side, pre in (("leg_l", "left"), ("leg_r", "right")):
            knee, foot = m.bone_pos(W, f"{pre}LowerLeg"), m.bone_pos(W, f"{pre}Foot")
            if knee is not None and foot is not None:
                z.append((side, (knee + foot) / 2, 0.075 * k))
                z.append((side, knee, 0.06 * k))
        z.append(("head", hc, 0.12 * k))
        ch = m.bone_pos(W, "upperChest")
        if ch is None:
            ch = m.bone_pos(W, "chest")
        if ch is not None:
            z.append(("chest", ch + self._canon((0, 0.02, 0.07)), 0.085 * k))
        hp = m.bone_pos(W, "hips")
        if hp is not None:
            z.append(("skirt", hp + self._canon((0, -0.09, 0.02)), 0.15 * k))
        return z

    def zone_at(self, local: QPointF) -> str | None:
        self._press_local = QPointF(local)
        img = self._frame
        if img is None:
            return None
        d = img.devicePixelRatio()
        x, y = int(local.x() * d), int(local.y() * d)
        if not (0 <= x < img.width() and 0 <= y < img.height()) or img.pixelColor(x, y).alpha() < 20:
            return None
        lx, ly = local.x(), local.y()
        for name, p, r in self._zones():
            sx, sy = self.project(p)
            rp = r * self.ppm
            if (sx - lx) ** 2 + (sy - ly) ** 2 <= rp * rp:
                return name
        near = self.springs.tails_near(self.project, (lx, ly), 22)
        if any(id(j) in self.hair_springs for j in near):
            hx, _ = self.project(self._head_center())
            return "hair_l" if lx > hx else "hair_r"
        if near:
            return "skirt"
        return "body"

    # ------------------------------------------------------------------ interaction
    def look_at(self, local: QPointF | None) -> None:
        if local is None or self._W is None:
            self._look_target = [0.0, 0.0]
            return
        hx, hy = self.project(self._head_center())
        self._look_target = [math.tanh((local.x() - hx) / 350.0), math.tanh((hy - local.y()) / 350.0)]
        if self.enhanced:
            # she waves when your cursor comes close after being away
            d = math.hypot(local.x() - hx, local.y() - hy)
            if d > 420:
                self._cursor_far = True
            elif d < 170 and self._cursor_far and self.t - self._last_wave > 25 and not self.gesture:
                self._cursor_far = False
                self._last_wave = self.t
                self.start_gesture("wave_l" if local.x() > hx else "wave_r")

    def squint(self, on: bool) -> None:
        if on:
            self.timers["eye_shut"] = max(self.timers["eye_shut"], 0.0)
            self.expr_bias = 0.4

    def react(self, zone: str, kind: str = "tap") -> None:
        side = random.choice((-1.0, 1.0))
        if kind == "rough":   # flinch: eyes squeezed shut, head knocked back, tears
            self.timers["eye_shut"] = 1.2
            self.timers["jerk"] = 0.5
            self.timers["tears"] = 4.0
            self.springs.kick(list(self.springs.joints), self._canon((side * 0.04, 0.03, -0.03)))
            return
        if zone == "mouth":
            self.timers["blush"] = 3.0
            self.timers["jerk"] = -0.3
            return
        if zone in ("nose", "eye_l", "eye_r") and kind != "pet":
            self.timers["eye_shut"] = 0.45 if zone == "nose" else 0.9   # instant blink / squint
            self.timers["jerk"] = 0.45                                   # startled recoil
            return
        if zone in ("chest", "skirt"):
            self.timers["blush"] = 4.0
            if kind != "pet":                     # rough / sudden: flustered recoil + steam
                self.timers["lean"] = 1.0
                self.lean_sign = 0.0 if zone == "chest" else side
                self.timers["steam"] = 3.0
            return
        if zone in ("eye_l", "eye_r"):
            self.timers["eye_shut"] = 1.3
            self.timers["jerk"] = 0.5
        elif zone in ("cheek_l", "cheek_r"):
            self.timers["blush"] = 3.0
            self.timers["jerk"] = -0.4   # tilt towards the touch
        elif zone in ("hand_l", "hand_r"):
            self.timers["blush"] = 3.0
            if self.enhanced and kind in ("tap", "gentle"):
                self.start_gesture("highfive_l" if zone == "hand_l" else "highfive_r")   # high five!
            else:
                self.timers["wave_l" if zone == "hand_l" else "wave_r"] = 1.2
        elif zone == "head" and self.enhanced and kind == "pet":
            self.start_gesture("shy_hands")
        elif zone in ("hair_l", "hair_r"):
            joints = [j for j in self.springs.joints if id(j) in self.hair_springs]
            self.springs.kick(joints, self._canon((0.06 if zone == "hair_l" else -0.06, 0.02, 0.05)))
        elif zone == "skirt":
            joints = [j for j in self.springs.joints if id(j) in self.cloth_springs]
            self.springs.kick(joints, self._canon((side * 0.05, 0.0, 0.04)))
            self.timers["lean"] = 0.8
            self.lean_sign = side
            self.timers["blush"] = 3.0
        elif zone == "chest":
            self.timers["lean"] = 1.0
            self.lean_sign = 0.0
            self.timers["blush"] = 3.0
        else:
            joints = list(self.springs.joints)
            self.springs.kick(joints, self._canon((side * 0.03, 0.03, 0.0)))

    def tug(self, zone: str, delta: QPointF | None) -> None:
        if zone in ("hand_l", "hand_r"):
            self._tug_hand = None if delta is None else (zone, self._press_local + delta)
            if delta is not None:
                self.timers["blush"] = max(self.timers["blush"], 1.0)
            return
        if delta is None:
            for j in self._pinned:
                j.pin = None
            self._pinned = []
            return
        if not self._pinned:
            p = self._press_local
            self._pinned = self.springs.tails_near(self.project, (p.x(), p.y()), 40)
            for j in self._pinned:
                j.grab = j.cur.copy()
        d = np.array([delta.x(), -delta.y(), 0.0]) / self.ppm * 0.8
        for j in self._pinned:
            j.pin = j.grab + d

    # ------------------------------------------------------------------ animation
    def _pose_targets(self, state: str, ctx: FrameContext) -> tuple[dict, np.ndarray]:
        t = self.t
        P: dict[str, np.ndarray] = {}

        def add(bone, q):
            P[bone] = q_mul(q, P.get(bone, IDENTITY_Q))

        br = math.sin(t * math.tau / (5.0 if state == SLEEP else 3.6))
        hip = np.zeros(3)
        # relaxed arms, breathing
        add("leftUpperArm", _rz(-ARM_DOWN))
        add("rightUpperArm", _rz(ARM_DOWN))
        add("leftLowerArm", _ry(-14))
        add("rightLowerArm", _ry(14))
        add("leftHand", _rz(-6))
        add("rightHand", _rz(6))
        add("spine", _rx(br * 1.2))
        add("chest", _rx(br * 1.0))
        add("leftShoulder", _rz(br * 1.2))
        add("rightShoulder", _rz(-br * 1.2))
        if state in (IDLE, REACT, PET):
            add("hips", _rz(math.sin(t * 0.7) * 1.5))
            add("spine", _rz(-math.sin(t * 0.7) * 1.2))

        if state == WALK:
            ph = t * math.tau * 1.7
            s, c = math.sin(ph), math.cos(ph)
            add("leftUpperLeg", _rx(-s * 26))
            add("rightUpperLeg", _rx(s * 26))
            add("leftLowerLeg", _rx(max(0.0, c) * 42 + 4))
            add("rightLowerLeg", _rx(max(0.0, -c) * 42 + 4))
            add("leftFoot", _rx(-max(0.0, c) * 12))
            add("rightFoot", _rx(-max(0.0, -c) * 12))
            P["leftUpperArm"] = q_mul(_rx(s * 22), P["leftUpperArm"])
            P["rightUpperArm"] = q_mul(_rx(-s * 22), P["rightUpperArm"])
            add("hips", _ry(s * 6))
            add("spine", _ry(-s * 5))
            hip[1] = -abs(c) * 0.018 * self.k
        elif state in (SIT, SLEEP):
            hips = self.m.bone_pos(self.m.rest_world, "hips")
            knee = self.m.bone_pos(self.m.rest_world, "leftLowerLeg")
            drop = (hips[1] - knee[1]) if hips is not None and knee is not None else 0.4 * self.k
            hip[1] = -(hips[1] - self.m.min_y - 0.07 * self.k) if hips is not None else -drop
            for side, sgn in (("left", 1), ("right", -1)):
                add(f"{side}UpperLeg", _rx(-86))
                add(f"{side}UpperLeg", _rz(sgn * -4))
                add(f"{side}LowerLeg", _rx(84 + math.sin(t * 1.6 + sgn) * 12))
                P[f"{side}UpperArm"] = q_mul(_rx(-28), _rz(sgn * -76))
                P[f"{side}LowerArm"] = _ry(sgn * -38)
            add("spine", _rx(6))
            if state == SLEEP:
                add("neck", _rx(10))
                add("head", q_mul(_rx(14), _rz(8)))
        elif state == DRAG and ctx.grab:
            self._limb_hang_pose(P, add, ctx)
        elif state == DRAG:
            s = ctx.struggle
            for side, sgn in (("left", 1), ("right", -1)):
                add(f"{side}UpperLeg", _rx(math.sin(t * 13 + sgn * 1.6) * 32 * s))
                add(f"{side}LowerLeg", _rx(15 + (math.sin(t * 13 + sgn * 1.6 + 1) * 0.5 + 0.5) * 35 * s))
            add("spine", _ry(math.sin(t * 8) * 10 * s))
            for side, sgn in (("left", 1), ("right", -1)):
                P[f"{side}UpperArm"] = _rz(sgn * (-22 + math.sin(t * 9 + sgn) * 10))
                P[f"{side}LowerArm"] = _ry(sgn * -25)
                swing = math.sin(t * 5 + sgn) * 12 - max(-20.0, min(20.0, ctx.vx * 0.01))
                add(f"{side}UpperLeg", _rx(swing))
                add(f"{side}LowerLeg", _rx(12 + math.sin(t * 5 + sgn + 1) * 8))
            add("head", _rx(10))
        elif state == FALL:
            for side, sgn in (("left", 1), ("right", -1)):
                P[f"{side}UpperArm"] = _rz(sgn * (35 + math.sin(t * 14 + sgn) * 15))
                add(f"{side}UpperLeg", _rx(math.sin(t * 12 + sgn * 2) * 20))
                add(f"{side}LowerLeg", _rx(20))
        elif state == PET:
            add("head", q_mul(_rz(math.sin(t * 3.2) * 7), _rx(6)))
            add("leftShoulder", _rz(7))
            add("rightShoulder", _rz(-7))
            P["leftUpperArm"] = q_mul(_rx(-18), P["leftUpperArm"])
            P["rightUpperArm"] = q_mul(_rx(-18), P["rightUpperArm"])

        # music: bob the head on the beat, sway the hips
        if ctx.music and state in (IDLE, REACT, SIT, PET):
            b = ctx.beat
            add("head", _rx(b * 9 - 2))
            add("neck", _rz(math.sin(t * 2.2) * 5))
            if state != SIT:
                add("hips", _rz(math.sin(t * 2.2) * 5))
                add("spine", _rz(-math.sin(t * 2.2) * 4))
                hip[1] -= b * 0.012 * self.k
                for side, sgn in (("left", 1), ("right", -1)):
                    P[f"{side}UpperArm"] = q_mul(_rx(-10 - b * 12), _rz(sgn * -(ARM_DOWN - 12)))

        # reactions
        if self.timers["lean"] > 0:
            a = math.sin(self.timers["lean"] / 1.0 * math.pi) * 14
            if self.lean_sign == 0:
                add("spine", _rx(-a))
                add("chest", _rx(-a * 0.6))
            else:
                add("spine", _rz(self.lean_sign * a))
        if self.timers["jerk"] != 0:
            j = self.timers["jerk"]
            add("head", _rx(-abs(j) * 30) if j > 0 else _rz(j * 25))
        for side, sgn in (("l", 1), ("r", -1)):
            w = self.timers[f"wave_{side}"]
            if w > 0 and state not in (DRAG, FALL):
                bone = "leftUpperArm" if side == "l" else "rightUpperArm"
                P[bone] = q_mul(_rx(-25 - math.sin(w * 12) * 8), _rz(sgn * -40))

        if self.enhanced:
            self._enhanced_pose(P, hip, state, ctx, add)

        # looking at the cursor
        if state not in (SLEEP, DRAG):
            yaw, pitch = self.look[0] * 38, self.look[1] * 22
            add("neck", q_mul(_ry(yaw * 0.4), _rx(-pitch * 0.4)))
            add("head", q_mul(_ry(yaw * 0.6), _rx(-pitch * 0.6)))
            if self.m.look_type == "bone":
                for eye in ("leftEye", "rightEye"):
                    add(eye, q_mul(_ry(yaw * 0.25), _rx(-pitch * 0.3)))
            if ctx.mouth > 0:
                add("head", _rx(ctx.mouth * 4))
            if ctx.expr is not None and ctx.expr.roll:
                add("head", _rz(-ctx.expr.roll))     # coy head tilt
            if ctx.expr is not None and ctx.expr.gaze == "downcast":
                add("head", _rx(8))

        # holding a hand: the arm reaches toward the cursor
        if self._tug_hand and self._W is not None:
            zone, pt = self._tug_hand
            side = "left" if zone == "hand_l" else "right"
            sh = self.m.bone_pos(self._W, f"{side}UpperArm")
            if sh is not None:
                sx, sy = self.project(sh)
                d_world = np.array([pt.x() - sx, -(pt.y() - sy), 0.4 * self.ppm * self.k])
                d = q_rotate(q_inv(self._root_rot), normalize(d_world))   # back to canonical frame
                rest_dir = np.array([1.0 if side == "left" else -1.0, 0, 0])
                P[f"{side}UpperArm"] = q_from_vectors(rest_dir, d)
                P[f"{side}LowerArm"] = _ry(-8 if side == "left" else 8)
        return P, hip

    # ------------------------------------------------------------------ lifted by a limb: dangle + struggle
    def _limb_hang_pose(self, P: dict, add, ctx: FrameContext) -> None:
        t, s = self.t, max(0.25, ctx.struggle)
        g = ctx.grab
        by_leg = g.startswith("leg")
        held = "left" if g.endswith("_l") else "right"
        free = "right" if held == "left" else "left"
        hs = 1 if held == "left" else -1
        if by_leg:
            # hanging upside down by one leg: that leg straight, the other kicks, arms flail toward the ground
            P[f"{held}UpperLeg"] = _rz(hs * 4)
            P[f"{held}LowerLeg"] = IDENTITY_Q
            add(f"{free}UpperLeg", _rx(-20 + math.sin(t * 12) * 40 * s))
            add(f"{free}LowerLeg", _rx(30 + (math.sin(t * 12 + 1) * 0.5 + 0.5) * 50 * s))
            for side, sgn in (("left", 1), ("right", -1)):
                P[f"{side}UpperArm"] = _rz(sgn * (70 + math.sin(t * 10 + sgn) * 25 * s))
                P[f"{side}LowerArm"] = _rz(sgn * (15 + math.sin(t * 13 + sgn) * 25 * s))
            add("head", _rx(-20 + math.sin(t * 9) * 8 * s))     # looks "up" at the ground, flustered
        else:
            # dangling from one hand: that arm straight up, the rest wriggles
            P[f"{held}UpperArm"] = q_mul(_rx(-6), _rz(hs * 84))
            P[f"{held}LowerArm"] = _rz(hs * 4)
            P[f"{free}UpperArm"] = q_mul(_rx(math.sin(t * 9) * 20 * s), _rz(-hs * (-55 + math.sin(t * 11) * 35 * s)))
            P[f"{free}LowerArm"] = _rz(-hs * (20 + math.sin(t * 13) * 30 * s))
            for side, sgn in (("left", 1), ("right", -1)):
                add(f"{side}UpperLeg", _rx(math.sin(t * 13 + sgn * 1.6) * 38 * s))
                add(f"{side}LowerLeg", _rx(10 + (math.sin(t * 13 + sgn * 1.6 + 1) * 0.5 + 0.5) * 45 * s))
            add("head", q_mul(_rx(-12), _rz(hs * 10)))           # looks up at the hand holding her
        add("spine", _ry(math.sin(t * 8) * 14 * s))
        add("hips", _rz(math.sin(t * 6.5) * 8 * s))
        add("neck", _ry(math.sin(t * 11) * 12 * s))               # head shaking "no no no"

    def _limb_pin(self, W: np.ndarray, ctx: FrameContext, dt: float) -> np.ndarray:
        """Move/rotate the whole body so the grabbed hand/foot sits under the cursor and swings."""
        if ctx.grab:
            self._grab_last = (ctx.grab, ctx.grab_pt)
            self._grab_blend = min(1.0, self._grab_blend + dt * 7)
        else:
            self._grab_blend = max(0.0, self._grab_blend - dt * 5)
        self._pin_M = None
        if self._grab_blend <= 0 or not self._grab_last:
            return W
        g, pt = self._grab_last
        bone = ("leftFoot" if g.endswith("_l") else "rightFoot") if g.startswith("leg") else \
            ("leftHand" if g.endswith("_l") else "rightHand")
        limb = self.m.bone_pos(W, bone)
        if limb is None:
            return W
        # cursor point in the world (orthographic camera, at the limb's depth)
        clip = self._vp @ np.append(limb, 1.0)
        ndc = np.array([pt[0] / self.W * 2 - 1, 1 - pt[1] / self.H * 2, clip[2] / clip[3], 1.0])
        w = np.linalg.inv(self._vp) @ ndc
        target = w[:3] / w[3]
        b = self._grab_blend * self._grab_blend * (3 - 2 * self._grab_blend)
        swing = (180.0 if g.startswith("leg") else 0.0) - ctx.tilt * 0.9 + math.sin(self.t * 7) * 6 * ctx.struggle
        M = translate(*(limb + (target - limb) * b)) @ rot_m4(_rz(swing * b)) @ translate(*-limb)
        self._pin_M = M
        return M[None] @ W

    # ------------------------------------------------------------------ Seed-san 2: lifelike motion
    def _enhanced_pose(self, P: dict, hip: np.ndarray, state: str, ctx: FrameContext, add) -> None:
        t, k = self.t, self.k
        # relaxed, slightly curled fingers instead of a flat T-pose hand
        for side, s in (("left", -1), ("right", 1)):
            for finger, spread in (("Index", 4), ("Middle", 0), ("Ring", -3), ("Little", -6)):
                for seg, a in (("Proximal", 14), ("Intermediate", 22), ("Distal", 14)):
                    add(f"{side}{finger}{seg}", _rz(s * a) if seg != "Proximal" else q_mul(_ry(-s * spread), _rz(s * a)))
            add(f"{side}ThumbProximal", _ry(-s * 15))
        if state in (IDLE, REACT, PET) and not ctx.music:
            # contrapposto: weight slowly shifts from one leg to the other
            w = math.sin(t * 0.42)
            hip[0] += w * 0.014 * k
            add("hips", _rz(w * 2.2))
            add("spine", _rz(-w * 2.6))
            add("leftLowerLeg" if w > 0 else "rightLowerLeg", _rx(abs(w) * 9))
            add("leftUpperLeg" if w > 0 else "rightUpperLeg", _rx(-abs(w) * 4))
            add("leftShoulder", _rz(math.sin(t * 2 * math.pi / 3.6) * 1.5))
        if state == WALK:
            ph = t * math.tau * 1.7
            add("leftLowerArm", _ry(-18 - max(0.0, math.sin(ph)) * 15))
            add("rightLowerArm", _ry(18 + max(0.0, -math.sin(ph)) * 15))
            add("chest", _ry(math.sin(ph) * 6))
            add("spine", _rx(4))
            add("head", _rx(-abs(math.cos(ph)) * 2))
        # gestures (blend in/out smoothly)
        if self.gesture and state in (IDLE, REACT, PET, SIT):
            length = GESTURE_LEN.get(self.gesture, 2.0)
            env = max(0.0, min(1.0, self.gesture_t / 0.35, (length - self.gesture_t) / 0.4))
            env = env * env * (3 - 2 * env)
            for bone, q in gesture_pose(self.gesture, self.gesture_t).items():
                base = P.get(bone, IDENTITY_Q)
                if bone.endswith(("UpperArm", "LowerArm", "Hand")):
                    # go through the T-pose so big swings (arms down -> arms up) take the natural path
                    mid = q_slerp(base, IDENTITY_Q, min(1.0, env * 2))
                    P[bone] = q_slerp(mid, q, max(0.0, env * 2 - 1))
                else:
                    P[bone] = q_mul(q_slerp(IDENTITY_Q, q, env), base)

    def start_gesture(self, name: str) -> None:
        if name in GESTURE_LEN:
            self.gesture, self.gesture_t = name, 0.0

    def _update_gestures(self, dt: float, state: str, ctx: FrameContext) -> None:
        if self.gesture:
            self.gesture_t += dt
            if self.gesture_t >= GESTURE_LEN.get(self.gesture, 2.0) or state in (DRAG, FALL, WALK, SLEEP):
                self.gesture = None
                self._next_gesture = random.uniform(9, 20)
            return
        # eye saccades: tiny quick jumps of the gaze, like real eyes
        self._next_saccade -= dt
        if self._next_saccade <= 0:
            self._saccade = np.random.uniform(-0.09, 0.09, 2)
            self._next_saccade = random.uniform(0.4, 2.2)
        if ctx.mouth > 0.3 and random.random() < dt * 0.25:
            self.start_gesture("explain")
            return
        self._next_gesture -= dt
        if self._next_gesture <= 0 and state in (IDLE, SIT) and not ctx.music and ctx.pet_level <= 0:
            face = ctx.expr.face if ctx.expr is not None else "calm"
            g = gesture_for(face, ctx.emotion)
            if state == SIT and g in ("stretch", "cheer", "hands_behind"):
                g = "look_around"
            self.start_gesture(g)

    def _expressions(self, state: str, ctx: FrameContext, dt: float) -> None:
        tgt: dict[str, float] = {}
        ex = ctx.expr
        emo = dict(ex.weights) if ex is not None else EMOTION_EXPR.get(ctx.emotion, {})
        if ex is not None:
            if ex.happy_eyes:
                emo["happy"] = 1.0                       # ^^ closed-eye smile
            elif ex.lid > 0:
                emo["blink"] = max(emo.get("blink", 0.0), ex.lid * 0.85)
            if ex.wink:
                emo["blinkRight" if "blinkRight" in self.m.expressions else "blink"] = 1.0
        if state == PET:
            emo = {"happy": 1.0}
        for k_, v in emo.items():
            tgt[k_] = max(tgt.get(k_, 0.0), v)
        if "surprised" in emo and "surprised" not in self.m.expressions:
            tgt["aa"] = 0.5
        if self.timers["eye_shut"] > 0:
            tgt = {"blink": 1.0, "angry": 0.5}
        elif state == SLEEP:
            tgt["blink"] = 1.0
        elif ctx.blink:
            block = max([self.expr.get(n, 0.0) for n, e in self.m.expressions.items()
                         if e.override_blink == "block" and n in tgt] + [0.0])
            tgt["blink"] = max(tgt.get("blink", 0.0), 1.0 - block)
        if self.expr_bias > 0:
            tgt["blink"] = max(tgt.get("blink", 0.0), self.expr_bias)
            self.expr_bias = max(0.0, self.expr_bias - dt * 2)
        if ctx.mouth > 0.02:
            tgt["aa"] = max(tgt.get("aa", 0.0), ctx.mouth * 0.85)
            tgt["ih"] = ctx.mouth * 0.25 * (0.5 + 0.5 * math.sin(self.t * 13))
        if self.m.look_type == "expression":
            lx, ly = self.look
            tgt["lookLeft" if lx > 0 else "lookRight"] = min(0.7, abs(lx))
            tgt["lookUp" if ly > 0 else "lookDown"] = min(0.6, abs(ly))
        for name in self.expr:
            goal = tgt.get(name, 0.0)
            rate = 30.0 if name.startswith("blink") else 9.0
            cur = self.expr[name]
            self.expr[name] = cur + (goal - cur) * min(1.0, dt * rate)

    def _morph_weights(self) -> dict[int, dict[int, float]]:
        out: dict[int, dict[int, float]] = {}
        for name, w in self.expr.items():
            if w <= 1e-3:
                continue
            e = self.m.expressions[name]
            if e.binary:
                w = 1.0 if w > 0.5 else 0.0
            for mesh, idx, bw in e.binds:
                d = out.setdefault(mesh, {})
                d[idx] = min(1.0, d.get(idx, 0.0) + w * bw)
        return out

    def step(self, dt: float, ax: float, ay: float, vx: float, vy: float,
             state: str, ctx: FrameContext) -> None:
        self.t += dt
        for key in self.timers:
            if key == "jerk":
                j = self.timers[key]
                self.timers[key] = math.copysign(max(0.0, abs(j) - dt), j) if j else 0.0
            else:
                self.timers[key] = max(0.0, self.timers[key] - dt)
        ex = ctx.expr
        if self.enhanced:
            self._update_gestures(dt, state, ctx)
        for i in (0, 1):
            goal = self._look_target[i]
            if ex is not None:   # gaze from the expression matrix (side glances, shy downcast...)
                goal = goal * ex.follow + ex.look[i] * 0.6
            if self.enhanced:
                goal += self._saccade[i]
            rate = 14 if self.enhanced else 6    # eyes snap faster in the realistic version
            self.look[i] += (goal - self.look[i]) * min(1.0, dt * rate)

        # window motion moves the model through the world -> spring bones lag behind
        move = np.array([vx * dt, -vy * dt, 0.0]) / self.ppm
        if np.linalg.norm(move) > 0.6 * self.m.height:   # teleport: don't fling the hair
            self.springs.shift(move)
        self.off = self.off + move

        # body orientation
        if state == WALK:
            target_yaw = -58.0 if ctx.facing_left else 58.0
        elif ctx.emotion == "hurt":
            target_yaw = -40.0 if self.look[0] >= 0 else 40.0   # turns away from you, sulking
        elif state in (SIT, SLEEP):
            target_yaw = 8.0
        else:
            target_yaw = self.look[0] * 14
        self.yaw += (target_yaw - self.yaw) * min(1.0, dt * 6)
        tilt = -ctx.tilt * 0.6 if state == DRAG and not ctx.grab else 0.0
        if ctx.grab:
            self.yaw *= max(0.0, 1 - dt * 6)      # face the user while dangling
        hop = 0.0
        if state == REACT:
            hop = max(0.0, math.sin(min(1.0, ctx.state_t / 0.35) * math.pi)) * 0.045 * self.k

        q_root = q_mul(_rz(tilt), _ry(self.yaw))
        self._root_rot = q_root
        pivot = np.array([0.0, self.m.height * 0.82, 0.0])
        root = (translate(*self.off) @ translate(*pivot) @ rot_m4(_rz(tilt)) @ translate(*-pivot)
                @ translate(0, hop, 0) @ rot_m4(_ry(self.yaw)))
        if self.m.version == 0:
            root = root @ rot_m4(R180)

        # pose (smoothly blended)
        targets, hip = self._pose_targets(state, ctx)
        a = min(1.0, dt * 10)
        bones = set(targets) | set(self.cur_pose)
        for b in bones:
            goal = targets.get(b, IDENTITY_Q)
            self.cur_pose[b] = q_slerp(self.cur_pose.get(b, IDENTITY_Q), goal, a)
        self.hip_off += (hip - self.hip_off) * a
        m = self.m
        m.reset_pose()
        for b, q in self.cur_pose.items():
            m.set_bone(b, q)
        m.offset_bone("hips", self.hip_off)

        h = m.height
        half = WIDTH_M * h / 2
        cx = self.off[0] + m.center_x
        view = translate(-cx, -self.off[1], 0) @ translate(0, 0, -5)
        self._vp = ortho(-half, half, m.min_y - BOT_M * h, m.min_y + (1 + TOP_M) * h, 0.1, 20) @ view

        W = m.world_matrices(root)
        W = self._limb_pin(W, ctx, dt)          # lifted by a hand/leg: hang from it under the cursor
        pin = getattr(self, "_pin_M", None)
        n = 1 if dt <= 0.04 else 2
        for _ in range(n):
            self.springs.update(W, dt / n)
        W2 = m.world_matrices(root)
        self._W = pin[None] @ W2 if pin is not None else W2
        self._expressions(state, ctx, dt)

    # ------------------------------------------------------------------ rendering
    def render(self, state: str, ctx: FrameContext, dpr: float) -> QImage:
        if self._W is None:
            self.step(1 / 30, 0, 0, 0, 0, state, ctx)
        if abs(dpr - self.dpr) > 0.01:
            self.dpr = dpr
            self.gl.ctx.makeCurrent(self.gl.surface)
            self.gl.resize(round(self.W * dpr), round(self.H * dpr))
            self.gl.ctx.doneCurrent()
        img = self.gl.render(self._W, self._vp, self._morph_weights())
        img.setDevicePixelRatio(self.dpr)
        if self.enhanced and state not in (DRAG, FALL):
            # soft contact shadow under her feet, drawn beneath the model
            base = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
            base.setDevicePixelRatio(self.dpr)
            base.fill(Qt.GlobalColor.transparent)
            sp = QPainter(base)
            sp.setRenderHint(QPainter.RenderHint.Antialiasing)
            gx, gy = self.project(np.array([self.off[0] + self.m.center_x, self.off[1] + self.m.min_y, 0.0]))
            rw, rh = 0.2 * self.m.height * self.ppm, 0.035 * self.m.height * self.ppm
            g = QRadialGradient(QPointF(gx, gy), rw)
            g.setColorAt(0, QColor(0, 0, 0, 90))
            g.setColorAt(0.6, QColor(0, 0, 0, 35))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            sp.setPen(Qt.PenStyle.NoPen)
            sp.setBrush(g)
            sp.save()
            sp.translate(gx, gy)
            sp.scale(1.0, rh / rw)
            sp.drawEllipse(QPointF(0, 0), rw, rw)
            sp.restore()
            sp.drawImage(0, 0, img)
            sp.end()
            img = base
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._overlays(p, state, ctx)
        p.end()
        self._frame = img
        return img

    def _overlays(self, p: QPainter, state: str, ctx: FrameContext) -> None:
        t = ctx.t
        hc = self._head_center()
        blush = {"shy": 0.8, "flustered": 1.0, "happy": 0.35, "excited": 0.4, "playful": 0.3}.get(ctx.emotion, 0.0)
        if ctx.expr is not None:
            blush = max(blush, ctx.expr.blush)
        if state == PET or self.timers["blush"] > 0:
            blush = 0.9
        if ctx.expr is not None and ctx.expr.sparkle:
            for sx in (1, -1):
                x, y = self.project(hc + self._canon((sx * 0.03, 0.0, 0.08)))
                tw = 0.6 + 0.4 * math.sin(t * 8 + sx)
                p.setPen(_pen(QColor(255, 255, 255, int(230 * tw)), 1.4))
                for dx, dy in ((3, 0), (0, 3)):
                    p.drawLine(QPointF(x - dx, y - dy), QPointF(x + dx, y + dy))
        if blush > 0.05:
            for sx in (1, -1):
                cx, cy = self.project(hc + self._canon((sx * 0.046, -0.04, 0.07)))
                r = 0.022 * self.k * self.ppm * 1.4
                g = QRadialGradient(QPointF(cx, cy), r)
                g.setColorAt(0, QColor(255, 90, 120, int(130 * blush)))
                g.setColorAt(1, QColor(255, 90, 120, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawEllipse(QPointF(cx, cy), r, r * 0.6)
        top = self.project(hc + self._canon((0, 0.13, 0)))
        if self.timers["steam"] > 0 or ctx.emotion == "flustered":
            draw_steam(p, top[0], top[1] + 4, t, 0.9)
        if ctx.struggle > 0.3:   # flailing: sweat drops and little motion marks
            for i, sx in enumerate((1, -1)):
                ph = (t * 2.2 + i * 0.5) % 1.0
                x, y = self.project(hc + self._canon((sx * (0.09 + ph * 0.04), 0.05 - ph * 0.04, 0.0)))
                col = QColor(120, 180, 255, int(220 * (1 - ph)))
                p.setPen(_pen(col.darker(130), 1.0))
                p.setBrush(col)
                p.drawEllipse(QPointF(x, y), 2.5, 3.4)
            p.setPen(_pen(QColor(60, 60, 80, 160), 1.6))
            for sx in (1, -1):
                x, y = self.project(hc + self._canon((sx * 0.12, 0.0, 0.0)))
                for k in range(3):
                    p.drawLine(QPointF(x + sx * 3, y - 6 + k * 6), QPointF(x + sx * 9, y - 8 + k * 6))
        if self.timers["tears"] > 0 or ctx.emotion == "hurt":
            for sx in (1, -1):
                slide = (t * 0.7 + (0.5 if sx < 0 else 0)) % 1.0
                x, y = self.project(hc + self._canon((sx * 0.035, -0.03 - slide * 0.05, 0.075)))
                col = QColor(140, 200, 255, int(230 * (1 - slide)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawEllipse(QPointF(x, y), 2.2, 3.0)
        if ctx.music:
            p.setFont(QFont("Segoe UI Symbol", 12))
            for i in range(2):
                ph = (t * 0.45 + i / 2) % 1.0
                col = QColor("#a07bff")
                col.setAlphaF(1 - ph)
                p.setPen(col)
                p.drawText(QPointF(top[0] + (-30 if i else 26) + math.sin(t * 2 + i) * 6, top[1] + 20 - ph * 40),
                           "♪" if i else "♫")
        if ctx.emotion == "angry":
            x, y = self.project(hc + self._canon((-0.07, 0.08, 0)))
            s = 5.0
            p.setPen(_pen(QColor("#ff3b3b"), 2.2))
            for a, b in (((-1, -0.3), (-0.3, -0.3)), ((-0.3, -1), (-0.3, -0.3)),
                         ((1, 0.3), (0.3, 0.3)), ((0.3, 1), (0.3, 0.3))):
                p.drawLine(QPointF(x + a[0] * s, y + a[1] * s), QPointF(x + b[0] * s, y + b[1] * s))
        if ctx.pet_level > 0:
            for i in range(3):
                ph = (t * 0.7 + i / 3) % 1.0
                col = QColor("#ff5c7a")
                col.setAlphaF(max(0.0, min(1.0, ctx.pet_level)) * (1 - ph))
                _heart(p, top[0] + (i - 1) * 18 + math.sin(t * 3 + i) * 3, top[1] - ph * 26, 10, col)
        if state == SLEEP:
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            for i in range(2):
                ph = (t * 0.4 + i / 2) % 1.0
                col = QColor("#7b8cff")
                col.setAlphaF(1 - ph)
                p.setPen(col)
                p.drawText(QPointF(top[0] + 22 + ph * 12, top[1] + 8 - ph * 26), "z")
