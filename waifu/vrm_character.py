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
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap, QRadialGradient

from .sprites import DRAG, FALL, IDLE, KNOCK, PET, REACT, SIT, SLEEP, WALK, FrameContext, _heart, _pen, draw_steam
from .vrm.math3d import (IDENTITY_Q, normalize, ortho, q_axis, q_euler, q_from_vectors, q_inv,
                         q_mul, q_rotate, q_slerp, q_to_m3, rot_m4, translate)
from .vrm.model import R180, VrmModel
from .vrm.renderer import VrmRenderer
from .cloth import ClothSim
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
    "squeezed": {"blink": 1.0, "ou": 0.55, "happy": 0.15},
}
ARM_DOWN = 77.0


def _rx(d): return q_axis((1, 0, 0), d)
def _ry(d): return q_axis((0, 1, 0), d)
def _rz(d): return q_axis((0, 0, 1), d)


# ---------------------------------------------------------------------------- gestures (Seed-san 2)
# Canonical VRM 1.0 frame: she faces +Z, her left arm points +X in the T-pose.
# Left arm: _rz(+) raises, _rz(-) lowers.  Right arm: mirrored.  Forearm forward: left _ry(-), right _ry(+).
GESTURE_LEN = {"nya": 2.4, "heart": 2.6, "kiss": 2.0, "wave_l": 2.4, "wave_r": 2.4, "stretch": 3.2, "look_around": 3.4, "hands_behind": 4.5,
               "hair_touch": 2.6, "highfive_l": 1.6, "highfive_r": 1.6, "explain": 2.2, "cheer": 2.0,
               "shy_hands": 4.0}


def _arm_pair(ux, uy, uz, ly, lz) -> dict:
    """Left-arm angles (solved numerically) plus the mirrored right arm."""
    return {"leftUpperArm": q_mul(q_mul(_rx(ux), _ry(uy)), _rz(uz)), "leftLowerArm": q_mul(_ry(ly), _rz(lz)),
            "rightUpperArm": q_mul(q_mul(_rx(ux), _ry(-uy)), _rz(-uz)), "rightLowerArm": q_mul(_ry(-ly), _rz(-lz))}


def gesture_pose(name: str, gt: float) -> dict[str, np.ndarray]:
    w = math.sin(gt * 11)
    if name == "nya":          # cat paws beside her face, head tilted: "Nya~"
        P = _arm_pair(-70, -60, -60, -150, 30)
        paw = math.sin(gt * 7) * 18
        P.update({"leftHand": _rz(-35 + paw), "rightHand": _rz(35 - paw), "head": _rz(10 * math.sin(gt * 3)),
                  "spine": _rz(math.sin(gt * 3) * 4)})
        return P
    if name == "heart":        # both arms making a heart over her head
        P = _arm_pair(0, -60, 60, -75, 60)
        P.update({"head": _rz(math.sin(gt * 2.5) * 6), "spine": _rz(math.sin(gt * 2.5) * 3)})
        return P
    if name == "kiss":         # blows a kiss: fingers to her lips, then the hand flies out
        if gt < 0.9:
            P = {k: v for k, v in _arm_pair(10, -60, 50, -135, 15).items() if k.startswith("right")}
        else:
            P = {"rightUpperArm": q_mul(_rx(-55), _rz(25)), "rightLowerArm": _ry(15), "rightHand": _rx(-20)}
        P["head"] = _rz(-8)
        return P
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


def hold_pose(name: str, t: float) -> dict[str, np.ndarray]:
    """Poses held for as long as she's hugging something / angry."""
    if name == "hug":          # hugging a teddy / pet against her chest, swaying happily
        sway = math.sin(t * 2.2) * 4
        P = _arm_pair(-38, -25, -62, -105, -10)
        P.update({"spine": _rz(sway * 0.6), "head": q_mul(_rx(10), _rz(sway + 6))})
        return P
    if name == "cross":        # arms crossed, chin up: "Hmph!"
        P = _arm_pair(-30, -22, -70, -128, -12)
        P["leftLowerArm"] = q_mul(P["leftLowerArm"], _rx(8))
        P["rightLowerArm"] = q_mul(P["rightLowerArm"], _rx(-8))
        P.update({"head": q_mul(_rx(-9), _rz(math.sin(t * 0.9) * 3)), "chest": _rx(-3)})
        return P
    return {}


def body_pose(name: str, t: float, add, P: dict, beat: float = 0.0) -> tuple[float, str | None]:
    """Held poses you can ask for. Fills P; returns (how far to drop the hips 0..1 of hip height, leg mode)."""
    def arms(lu, ll, ru=None, rl=None):
        P["leftUpperArm"], P["leftLowerArm"] = lu, ll
        if ru is not None:
            P["rightUpperArm"], P["rightLowerArm"] = ru, rl
    if name == "kneel":           # seiza: sitting on her heels, hands on her lap
        for side, sgn in (("left", 1), ("right", -1)):
            P[f"{side}UpperLeg"] = q_mul(_rx(-86), _rz(sgn * -3))
            P[f"{side}LowerLeg"] = _rx(158)
            P[f"{side}Foot"] = _rx(-35)
        P.update(_arm_pair(-32, -12, -74, -62, -12))
        add("spine", _rx(4))
        return 0.74, "kneel"
    if name == "squat":
        for side, sgn in (("left", 1), ("right", -1)):
            P[f"{side}UpperLeg"] = q_mul(_rx(-108), _rz(sgn * -16))
            P[f"{side}LowerLeg"] = _rx(132)
            P[f"{side}Foot"] = _rx(-22)
        P.update(_arm_pair(-52, -18, -64, -25, 0))
        add("spine", _rx(16))
        add("head", _rx(-10))
        return 0.5, "squat"
    if name == "bow":             # polite bow, hands together in front
        P.update(gesture_pose("shy_hands", t))
        P["head"] = _rx(10)
        add("spine", _rx(24))
        add("chest", _rx(14))
        add("neck", _rx(8))
        return 0.0, None
    if name == "hands_up":        # banzai!
        b = abs(math.sin(t * 4)) * 6
        P.update({"leftUpperArm": _rz(82 - b), "rightUpperArm": _rz(-82 + b),
                  "leftLowerArm": _rz(8), "rightLowerArm": _rz(-8)})
        add("head", _rx(-8))
        return 0.0, None
    if name == "salute":
        P.update({"rightUpperArm": q_mul(_rx(-45), _rz(-20)), "rightLowerArm": q_mul(_ry(78), _rz(-98)),
                  "rightHand": _rx(-10)})
        add("head", _rx(-4))
        add("chest", _rx(-4))
        return 0.0, None
    if name == "peace":           # V-sign next to her face, head tilted
        P.update({"rightUpperArm": q_mul(_rx(-52), _rz(-28)), "rightLowerArm": q_mul(_ry(62), _rz(-102)),
                  "rightHand": _rz(math.sin(t * 3) * 6)})
        add("head", _rz(-9))
        return 0.0, None
    if name == "think":           # hand on her chin, other arm across her waist
        P.update({"rightUpperArm": q_mul(q_mul(_rx(-28), _ry(28)), _rz(70)), "rightLowerArm": q_mul(_ry(122), _rz(-12)),
                  "leftUpperArm": q_mul(_rx(-22), _rz(-76)), "leftLowerArm": _ry(-82)})
        add("head", q_mul(_rz(8), _rx(-6)))
        return 0.0, None
    if name == "arms_crossed":
        P.update(hold_pose("cross", t))
        return 0.0, None
    if name == "hug_self":
        P.update(_arm_pair(-42, -30, -66, -142, -22))
        add("head", _rx(8))
        add("spine", _rx(6))
        return 0.0, None
    if name == "hands_on_hips":   # confident hero pose
        P.update({"leftUpperArm": q_mul(_rx(12), _rz(-48)), "rightUpperArm": q_mul(_rx(12), _rz(48)),
                  "leftLowerArm": q_mul(_rz(-78), _ry(18)), "rightLowerArm": q_mul(_rz(78), _ry(-18)),
                  "leftHand": _rz(-20), "rightHand": _rz(20)})
        add("chest", _rx(-6))
        add("head", _rx(-6))
        for side, sgn in (("left", 1), ("right", -1)):
            add(f"{side}UpperLeg", _rz(sgn * -5))
        return 0.0, None
    if name == "t_pose":
        for b in ("leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand"):
            P[b] = IDENTITY_Q
        return 0.0, None
    if name == "shy":
        P.update(gesture_pose("shy_hands", t))
        for side, sgn in (("left", 1), ("right", -1)):
            add(f"{side}UpperLeg", _rz(sgn * 5))
            add(f"{side}LowerLeg", _rx(6))
        return 0.0, None
    if name == "dance":
        ph = t * math.tau * 1.1
        s = math.sin(ph)
        P.update({"leftUpperArm": q_mul(_rx(-20), _rz(30 + 40 * max(0.0, s))),
                  "rightUpperArm": q_mul(_rx(-20), _rz(-30 - 40 * max(0.0, -s))),
                  "leftLowerArm": _rz(30 + 20 * s), "rightLowerArm": _rz(-30 + 20 * s)})
        add("hips", _rz(s * 8))
        add("spine", q_mul(_rz(-s * 6), _ry(math.sin(ph * 0.5) * 12)))
        add("head", _rz(s * 7))
        for side, sgn in (("left", 1), ("right", -1)):
            bend = max(0.0, s * sgn) * 30
            add(f"{side}UpperLeg", _rx(-bend * 0.6))
            add(f"{side}LowerLeg", _rx(bend))
        return 0.03 * abs(math.sin(ph * 2)), None
    if name.startswith("groove_"):
        return groove_pose(name[7:], t, beat, add, P)
    base, _, sd = name.rpartition("_")
    if base == "peer" and sd in ("l", "r"):    # squinting at your screen, hand shading her eyes, leaning in
        s = 1 if sd == "l" else -1
        side, other = ("left", "right") if s > 0 else ("right", "left")
        P.update({f"{side}UpperArm": q_mul(_rx(-52), _rz(s * 18)), f"{side}LowerArm": q_mul(_ry(s * -70), _rz(s * 98)),
                  f"{side}Hand": _rx(12),
                  f"{other}UpperArm": q_mul(_rx(-20), _rz(-s * -76)), f"{other}LowerArm": _ry(-s * -82)})
        nod = max(0.0, math.sin(t * 0.7)) ** 8 * 6           # an occasional little nod
        add("spine", _rx(12))
        add("neck", _rx(-6))
        add("head", q_mul(_rx(-8 + nod), _rz(s * 6)))
        return 0.0, None
    if base in ("eww", "spray", "mop") and sd in ("l", "r"):
        s = 1 if sd == "l" else -1
        side, other = ("left", "right") if s > 0 else ("right", "left")
        if base == "eww":       # looking down at the mess, hand over her mouth
            P.update({f"{other}UpperArm": q_mul(q_mul(_rx(-28), _ry(-s * 28)), _rz(-s * 70)),
                      f"{other}LowerArm": q_mul(_ry(-s * 122), _rz(s * 12))})
            add("spine", _rx(-4))
            add("head", q_mul(_rx(24), _rz(s * -8)))
            return 0.0, None
        if base == "spray":     # spritzing the mess with a spray bottle, other hand on her hip
            squeeze = abs(math.sin(t * 6)) * 8
            P.update({f"{side}UpperArm": q_mul(_rx(-52), _rz(s * -42)),
                      f"{side}LowerArm": q_mul(_ry(s * -12), _rz(s * -8)),
                      f"{side}Hand": _rx(-20 - squeeze),
                      f"{other}UpperArm": q_mul(_rx(12), _rz(-s * -48)),
                      f"{other}LowerArm": q_mul(_rz(-s * -78), _ry(s * 18))})
            add("spine", _rx(12))
            add("head", _rx(18))
            return 0.0, None
        # mop: both hands on the handle, scrubbing side to side
        sw = math.sin(t * 7)
        P.update(_arm_pair(-46 + sw * 6, -24, -60, -55, -6))
        add("spine", q_mul(_rx(14), q_mul(_rz(sw * 5 * s), _ry(sw * 12))))
        add("hips", _rz(-sw * 3))
        add("head", _rx(16))
        for leg, sgn in (("left", 1), ("right", -1)):
            add(f"{leg}UpperLeg", _rx(-12))
            add(f"{leg}LowerLeg", _rx(18))
        return 0.03, None
    if name in ("pet_l", "pet_r"):  # crouching beside her pet, stroking it with one hand
        s = 1 if name == "pet_l" else -1
        side, other = ("left", "right") if s > 0 else ("right", "left")
        stroke = math.sin(t * 5.5)
        for sd, sgn in (("left", 1), ("right", -1)):
            P[f"{sd}UpperLeg"] = q_mul(_rx(-100), _rz(sgn * -14))
            P[f"{sd}LowerLeg"] = _rx(128)
            P[f"{sd}Foot"] = _rx(-22)
        P[f"{side}UpperArm"] = q_mul(_rx(-45 + stroke * 8), _rz(s * -62))      # reaching down to the pet's head
        P[f"{side}LowerArm"] = q_mul(_ry(s * -12), _rz(s * (-25 + stroke * 12)))
        P[f"{side}Hand"] = _rz(s * (-20 + stroke * 10))
        P[f"{other}UpperArm"] = q_mul(_rx(-50), _rz(-s * -62))                 # other hand on her knee
        P[f"{other}LowerArm"] = _ry(-s * -30)
        add("spine", _rx(36))
        add("head", q_mul(_rx(16), _rz(s * -10)))
        return 0.58, "squat"
    if name == "read":            # holding an open book, reading, turning a page now and then
        P.update(_arm_pair(-40, -25, -62, -100, -6))
        if math.sin(t * 0.5) > 0.93:
            P["rightHand"] = _ry(25)
        add("head", q_mul(_rx(18), _rz(math.sin(t * 0.3) * 4)))
        return 0.0, None
    if name == "tea":             # mug in both hands, a little sip now and then
        sip = max(0.0, math.sin(t * 0.8)) ** 6
        P.update(_arm_pair(-38 - sip * 12, -25, -62, -105 - sip * 12, -10))
        add("head", q_mul(_rx(8 - sip * 14), _rz(5)))
        return 0.0, None
    if name == "jumping_jacks":
        s = abs(math.sin(t * 4.2))
        P.update({"leftUpperArm": _rz(-ARM_DOWN + 160 * s), "rightUpperArm": _rz(ARM_DOWN - 160 * s),
                  "leftLowerArm": _rz(5), "rightLowerArm": _rz(-5)})
        for sd, sgn in (("left", 1), ("right", -1)):
            P[f"{sd}UpperLeg"] = _rz(sgn * -13 * s)
            P[f"{sd}Foot"] = _rz(sgn * 13 * s)
        return 0.03 * (1 - s), None
    if name == "sick":            # doubled over, hands on her knees: bleh
        heave = abs(math.sin(t * 6)) * 7
        P.update(_arm_pair(-40, -12, -76, -16, 0))          # hands braced on her knees
        for sd, sgn in (("left", 1), ("right", -1)):
            P[f"{sd}UpperLeg"] = q_mul(_rx(-30), _rz(sgn * -7))
            P[f"{sd}LowerLeg"] = _rx(40)
        add("spine", _rx(36 + heave))
        add("chest", _rx(14))
        add("neck", _rx(6))
        add("head", _rx(4 + heave * 0.5))
        return 0.12, None
    if name == "lie":             # lying on her back, one knee up, looking at you
        add("leftUpperLeg", _rx(-32))
        add("leftLowerLeg", _rx(58))
        P.update(_arm_pair(10, 0, -60, -20, 0))
        add("head", _rx(-28))
        return 0.0, None
    return 0.0, None


def groove_pose(style: str, t: float, ph: float, add, P: dict) -> tuple[float, str | None]:
    """Dancing on the beat (ph = beat phase, one turn per beat), in a style that matches the song's mood."""
    s1, c1 = math.sin(ph), math.cos(ph)
    s2 = math.sin(ph / 2)                 # every other beat: left / right
    bounce = (1 - c1) * 0.5               # 0 on the beat ... 1 between beats
    if style == "energetic":              # bouncy knees, fist pumps, twisting
        up = max(0.0, s2)
        P.update({"leftUpperArm": q_mul(_rx(-30), _rz(20 + 70 * up)), "rightUpperArm": q_mul(_rx(-30), _rz(-20 - 70 * max(0.0, -s2))),
                  "leftLowerArm": _ry(-60 + 30 * up), "rightLowerArm": _ry(60 - 30 * max(0.0, -s2))})
        for leg, sgn in (("left", 1), ("right", -1)):
            add(f"{leg}UpperLeg", _rx(-14 * (1 - bounce)))
            add(f"{leg}LowerLeg", _rx(26 * (1 - bounce)))
        add("spine", _ry(s2 * 14))
        add("head", _rx(-c1 * 7))
        return 0.05 * (1 - bounce), None
    if style == "intense":                # head-bob + air guitar
        strum = math.sin(ph * 2)
        P.update({"leftUpperArm": q_mul(_rx(-50), _rz(-35)), "leftLowerArm": q_mul(_ry(-70), _rz(-10)),
                  "rightUpperArm": q_mul(_rx(-25), _rz(55)), "rightLowerArm": q_mul(_ry(55 + strum * 25), _rz(10))})
        add("head", _rx(abs(s1) * 20 - 4))
        add("spine", _rx(abs(s1) * 6))
        for leg, sgn in (("left", 1), ("right", -1)):
            add(f"{leg}UpperLeg", q_mul(_rx(-10 * abs(s1)), _rz(sgn * -8)))
            add(f"{leg}LowerLeg", _rx(18 * abs(s1)))
        return 0.03 * abs(s1), None
    if style == "calm":                   # slow sway over two beats, hands together, head tilted
        sway = math.sin(ph / 2)
        P.update(gesture_pose("shy_hands", t))
        add("hips", _rz(sway * 5))
        add("spine", _rz(-sway * 4))
        add("head", q_mul(_rz(sway * 8 + 6), _rx(6)))
        return 0.0, None
    if style == "sad":                    # hugging herself, head down, the smallest sway
        sway = math.sin(ph / 2)
        P.update(_arm_pair(-42, -30, -66, -142, -22))
        add("head", q_mul(_rx(16), _rz(sway * 4)))
        add("spine", q_mul(_rx(6), _rz(sway * 2)))
        add("leftShoulder", _rz(-5))
        add("rightShoulder", _rz(5))
        return 0.0, None
    # happy: side-to-side steps, arms swinging out, a little hop on the beat
    P.update({"leftUpperArm": q_mul(_rx(-15), _rz(-40 + 35 * max(0.0, s2))),
              "rightUpperArm": q_mul(_rx(-15), _rz(40 - 35 * max(0.0, -s2))),
              "leftLowerArm": _rz(25 + 15 * s1), "rightLowerArm": _rz(-25 + 15 * s1)})
    add("hips", _rz(s2 * 8))
    add("spine", _rz(-s2 * 6))
    add("head", _rz(s2 * 8))
    for leg, sgn in (("left", 1), ("right", -1)):
        lift = max(0.0, sgn * s2)
        add(f"{leg}UpperLeg", _rx(-22 * lift))
        add(f"{leg}LowerLeg", _rx(40 * lift))
    return 0.025 * bounce, None


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

    def __init__(self, path: str | Path, scale: float, dpr: float, enhanced: bool = False,
                 variant: dict | None = None):
        self.variant = variant or {}
        self.enhanced = enhanced or bool(self.variant.get("enhanced"))
        self._grab_blend = 0.0
        self._grab_last = None
        self._pin_M = None
        self._brush = None           # cursor brushing through her hair
        self._gust = 0.0
        self._next_gust = 4.0
        self._move_head = None       # (dx, dy) while you drag her face
        self._move_body = None       # (dx, dy) while you drag her body
        self._move_leg = None        # (zone, cursor point) while you drag a leg
        self._sq_center = np.zeros(3)
        self._sq_radius = 0.1
        self._sq = 0.0
        self._sq_v = 0.0
        self._sq_target = 0.0
        self._jelly = 0.0
        self._jelly_v = 0.0
        self.gesture: str | None = None
        self.gesture_t = 0.0
        self._next_gesture = random.uniform(6, 12)
        self._saccade = np.zeros(2)
        self._next_saccade = 1.0
        self._cursor_far = True
        self._last_wave = -99.0
        self.m = m = VrmModel(path, self.variant or None)
        self.k = m.height / 1.58                       # size relative to a typical 1.58 m avatar
        self.fig_px = round(300 * max(0.4, min(3.0, scale)))
        self.ppm = self.fig_px / m.height              # logical px per metre
        self.W = round(WIDTH_M * m.height * self.ppm)
        self.H = round((1 + TOP_M + BOT_M) * m.height * self.ppm)
        self.dpr = dpr
        self.gl = VrmRenderer(m, round(self.W * dpr), round(self.H * dpr), realism=1.0 if enhanced else 0.0)
        head = m.humanoid.get("head")
        self.springs = SpringSystem(m, m.descendants(head) if head is not None else set())
        self.hair_springs, self.cloth_springs = self._classify_springs()
        co = m.options.get("cloth", {})
        self.cloth = ClothSim(m.height, float(co.get("softness", 1.0)), float(co.get("flutter", 0.006))) \
            if any(mt.cloth for mt in m.materials) else None
        self._cloth_leg_r = float(co.get("leg_radius", 0.075)) * self.k
        self._wind = np.zeros(3)
        self._pitch = 0.0

        self.t = 0.0
        self.off = np.zeros(3)                         # model position in world (metres)
        self.yaw = 0.0
        self.cur_pose: dict[str, np.ndarray] = {}
        self.mid_pose: dict[str, np.ndarray] = {}      # two-stage smoothing: ease in AND out of every pose change
        self._walk_ph = 0.0
        self._walk_amp = 18.0
        self.hip_off = np.zeros(3)
        self.expr = {name: 0.0 for name in m.expressions}
        self.look = [0.0, 0.0]
        self._look_target = [0.0, 0.0]
        self.timers = {"eye_shut": 0.0, "blush": 0.0, "lean": 0.0, "jerk": 0.0, "wave_l": 0.0, "wave_r": 0.0,
                       "tears": 0.0, "steam": 0.0, "puni": 0.0}
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
        # the face (front, lower part of the head) is "head": drag = tilt, click = hello
        z.append(("head", hc + self._canon((0, -0.03, 0.06)), 0.072 * k))
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
        near = self.springs.tails_near(self.project, (lx, ly), 34)
        hx, hy = self.project(self._head_center())
        if any(id(j) in self.hair_springs for j in near):
            return "hair_l" if lx > hx else "hair_r"
        # anything around the head that isn't the face is her hair
        if (lx - hx) ** 2 + (ly - hy) ** 2 <= (0.17 * self.k * self.ppm) ** 2 and ly < hy + 0.06 * self.k * self.ppm:
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
        self._jelly_v += 3.0 if kind in ("pet", "squeeze", "gentle") else 5.0
        hair = [j for j in self.springs.joints if id(j) in self.hair_springs]
        flick = 0.03 if kind in ("pet", "gentle", "squeeze") else 0.07
        if self.cloth is not None:
            strong = 3.0 if zone in ("skirt", "body", "chest", "leg_l", "leg_r") or kind == "rough" else 1.2
            self.cloth.kick(self._canon((side * 0.25 * strong, 0.05, 0.2 * strong)) / max(self.k, 1e-3))
        self.springs.kick(hair, self._canon((side * flick, flick * 0.6, flick * 0.5)))
        if kind == "squeeze":
            self.timers["blush"] = 3.0
            self.timers["puni"] = 1.0
            return
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
            radius = 110 if zone.startswith("hair") else 45
            near = self.springs.tails_near(self.project, (p.x(), p.y()), radius)
            if zone.startswith("hair"):
                near = [j for j in near if id(j) in self.hair_springs] or near
            for j in near:
                sx, sy = self.project(j.cur)
                j.grab = j.cur.copy()
                j.grab_w = max(0.25, 1.0 - math.hypot(sx - p.x(), sy - p.y()) / radius)   # nearer = follows more
            self._pinned = near
        d = np.array([delta.x(), -delta.y(), 0.0]) / self.ppm
        for j in self._pinned:
            j.pin = j.grab + d * getattr(j, "grab_w", 1.0)

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
            ph, A = self._walk_ph, self._walk_amp
            s, c = math.sin(ph), math.cos(ph)
            r = A / 26.0
            add("leftUpperLeg", _rx(-s * A))
            add("rightUpperLeg", _rx(s * A))
            # the swinging leg lifts its knee; the standing leg stays almost straight
            add("leftLowerLeg", _rx(max(0.0, c) ** 1.4 * 48 * r + 4))
            add("rightLowerLeg", _rx(max(0.0, -c) ** 1.4 * 48 * r + 4))
            # heel strike (toes up) as the leg lands, toe-off (heel up) as it leaves
            add("leftFoot", _rx(-max(0.0, c) * 16 * r + max(0.0, s) * 8 * r))
            add("rightFoot", _rx(-max(0.0, -c) * 16 * r + max(0.0, -s) * 8 * r))
            P["leftUpperArm"] = q_mul(_rx(s * 24 * r), P["leftUpperArm"])
            P["rightUpperArm"] = q_mul(_rx(-s * 24 * r), P["rightUpperArm"])
            add("leftLowerArm", _ry(-max(0.0, s) * 18 * r))
            add("rightLowerArm", _ry(max(0.0, -s) * 18 * r))
            add("hips", q_mul(_ry(s * 7 * r), _rz(c * 3.5 * r)))     # pelvis turns and drops toward the swing leg
            add("spine", q_mul(_ry(-s * 6 * r), _rx(3)))            # shoulders counter-rotate, slight lean
            add("head", _ry(s * 3 * r))                              # head stays steady
            hip[1] = -abs(s) * 0.02 * self.k * r                     # lowest when both feet are down
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
        elif state == KNOCK:
            k, ts = ctx.knock, ctx.state_t
            falling = ts < 0.35
            for side, sgn in (("left", 1), ("right", -1)):
                if falling:   # arms flail up as she tips over
                    P[f"{side}UpperArm"] = _rz(sgn * (40 + math.sin(t * 16 + sgn) * 25))
                else:         # lying: arms spread out, knees a little bent
                    P[f"{side}UpperArm"] = q_mul(_rx(20), _rz(sgn * -35))
                    P[f"{side}LowerArm"] = _ry(sgn * -20)
                add(f"{side}UpperLeg", _rx(-18 * k + math.sin(t * 2 + sgn) * 3))
                add(f"{side}LowerLeg", _rx(25 * k))
            add("head", _rz(math.sin(t * 3.0) * 12 * k))      # dizzy head roll
        elif state == FALL and ctx.jump:
            for side, sgn in (("left", 1), ("right", -1)):
                P[f"{side}UpperArm"] = _rz(sgn * 62)
                P[f"{side}LowerArm"] = _rz(sgn * 15)
                add(f"{side}UpperLeg", _rx(-35))
                add(f"{side}LowerLeg", _rx(70))
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
        if ctx.music and state in (IDLE, REACT, SIT, PET) and not ctx.pose.startswith("groove_"):
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
        elif self.gesture and state in (IDLE, REACT, PET, SIT):
            self._blend_gesture(P)
        if ctx.pose and state in (IDLE, REACT, PET):
            drop, _legs = body_pose(ctx.pose, t, add, P, ctx.beat_ph)
            if drop:
                hips = self.m.bone_pos(self.m.rest_world, "hips")
                hh = (hips[1] - self.m.min_y) if hips is not None else 0.5 * self.m.height
                hip[1] = -hh * drop
        elif state not in (DRAG, FALL, SLEEP, KNOCK) and (ctx.holding or ctx.angry > 0.3):
            P.update(hold_pose("hug" if ctx.holding else "cross", t))
            if not ctx.holding and state in (IDLE, REACT):
                hip[1] -= abs(math.sin(t * 5.0)) * 0.006 * self.k if int(t * 0.8) % 3 == 0 else 0.0   # stomp stomp
        if ctx.land > 0.01 and state not in (DRAG, FALL, SLEEP, KNOCK, SIT):
            L = ctx.land
            for leg, sgn in (("left", 1), ("right", -1)):
                add(f"{leg}UpperLeg", _rx(-L * 38))
                add(f"{leg}LowerLeg", _rx(L * 70))
                add(f"{leg}Foot", _rx(-L * 32))
                P[f"{leg}UpperArm"] = q_mul(_rz(sgn * L * 22), P.get(f"{leg}UpperArm", IDENTITY_Q))   # arms out for balance
            add("spine", _rx(L * 12))
            add("head", _rx(-L * 6))
            hips = self.m.bone_pos(self.m.rest_world, "hips")
            hh = (hips[1] - self.m.min_y) if hips is not None else 0.5 * self.m.height
            hip[1] -= hh * 0.16 * L
        if state not in (DRAG, FALL):
            self._manip_pose(P, add)

        if ctx.sick > 0.3 and state not in (SLEEP,):
            add("head", q_mul(_rz(math.sin(t * 4.0) * 12 * ctx.sick), _rx(math.cos(t * 4.0) * 6 * ctx.sick)))
            add("spine", _rz(math.sin(t * 4.0 + 1) * 4 * ctx.sick))
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

    # ------------------------------------------------------------------ cute accessories (variants)
    def _head_point(self, v) -> np.ndarray:
        """A point given in the head's own (canonical) frame -> world, following every head movement."""
        hi = self.m.humanoid.get("head")
        hc = self._head_center()
        if hi is None or self._W is None:
            return hc + self._canon(v)
        from .vrm.math3d import m3_to_q
        q = q_mul(m3_to_q(self._W[hi][:3, :3]), q_inv(self.m.rest_world_rot[hi]))
        if self.m.version == 0:
            q = q_mul(q, R180)
        scale = float(self.variant.get("bone_scale", {}).get("head", 1.0))
        return hc + q_rotate(q, np.asarray(v, float) * self.k * scale)

    def _draw_accessories(self, p: QPainter) -> None:
        acc = self.variant.get("accessories", [])
        if not acc:
            return
        from PySide6.QtGui import QPolygonF
        col = QColor(self.variant.get("accessory_color", "#f7a3c6"))
        line = col.darker(150)
        if "cat_ears" in acc:
            for sx in (1, -1):
                base_a = self.project(self._head_point((sx * 0.025, 0.105, -0.01)))
                base_b = self.project(self._head_point((sx * 0.085, 0.075, -0.01)))
                tip = self.project(self._head_point((sx * 0.08, 0.175, -0.015)))
                p.setPen(_pen(line, 1.3))
                p.setBrush(col)
                p.drawPolygon(QPolygonF([QPointF(*base_a), QPointF(*tip), QPointF(*base_b)]))
                inner = [QPointF(base_a[0] * 0.7 + tip[0] * 0.3 + (base_b[0] - base_a[0]) * 0.18,
                                 base_a[1] * 0.7 + tip[1] * 0.3 + (base_b[1] - base_a[1]) * 0.18),
                         QPointF(tip[0] * 0.8 + (base_a[0] + base_b[0]) * 0.1, tip[1] * 0.8 + (base_a[1] + base_b[1]) * 0.1),
                         QPointF(base_b[0] * 0.7 + tip[0] * 0.3 - (base_b[0] - base_a[0]) * 0.18,
                                 base_b[1] * 0.7 + tip[1] * 0.3 - (base_b[1] - base_a[1]) * 0.18)]
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ffd6e7"))
                p.drawPolygon(QPolygonF(inner))
        if "bow" in acc:
            cx, cy = self.project(self._head_point((0.075, 0.07, 0.035)))
            s = 0.022 * self.k * self.ppm
            p.setPen(_pen(QColor("#c2185b"), 1.0))
            p.setBrush(QColor("#ff5c8a"))
            for dx in (-1, 1):
                path = QPainterPath()
                path.moveTo(cx, cy)
                path.cubicTo(cx + dx * s * 1.6, cy - s * 1.2, cx + dx * s * 1.9, cy + s * 1.1, cx, cy)
                p.drawPath(path)
            p.drawEllipse(QPointF(cx, cy), s * 0.35, s * 0.35)

    # ------------------------------------------------------------------ touch & move every part
    def manipulate(self, zone: str, delta) -> None:
        """You drag one part of her: that part follows the cursor (None = let go, it springs back)."""
        if zone in ("hand_l", "hand_r"):
            self.tug(zone, delta)
        elif zone in ("leg_l", "leg_r"):
            self._move_leg = None if delta is None else (zone, self._press_local + delta)
        elif zone in ("head", "eye_l", "eye_r", "nose", "mouth", "cheek_l", "cheek_r"):
            self._move_head = None if delta is None else (delta.x(), delta.y())
        elif zone in ("hair_l", "hair_r", "skirt", "scarf"):
            self.tug(zone, delta)
            if zone in ("hair_l", "hair_r"):
                self._move_head = None if delta is None else (delta.x() * 0.25, delta.y() * 0.15)
        else:
            self._move_body = None if delta is None else (delta.x(), delta.y())
        if delta is None:
            self._jelly_v += 2.0

    def brush(self, local, vel) -> None:
        """The cursor passing through her hair pushes the strands aside (and drags them along)."""
        self._brush = None if local is None else (QPointF(local), QPointF(vel) if vel is not None else QPointF())

    def _apply_brush(self) -> None:
        if not self._brush or self._W is None:
            return
        pt, vel = self._brush
        self._brush = None                      # one push per mouse move
        radius = 0.11 * self.k * self.ppm
        speed = min(2500.0, (vel.x() ** 2 + vel.y() ** 2) ** 0.5)
        for j in self.springs.joints:
            if id(j) not in self.hair_springs or j.pin is not None:
                continue
            sx, sy = self.project(j.cur)
            dx, dy = sx - pt.x(), sy - pt.y()
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > radius:
                continue
            w = (1.0 - dist / radius) ** 1.5
            away = np.array([dx, -dy, 0.0]) / max(dist, 1.0)          # pushed away from the cursor...
            drag = np.array([vel.x(), -vel.y(), 0.0]) / max(speed, 1.0)  # ...and swept along with it
            push = (away * 0.012 + drag * min(1.0, speed / 900.0) * 0.02) * w * self.k
            j.cur = j.cur + push                # prev stays put -> the strand gains momentum

    def squeeze(self, local) -> None:
        """Hold on a spot: it squishes. None = let go (springs back with a little boing)."""
        if local is None:
            self._sq_target = 0.0
            return
        ndc = np.array([local.x() / self.W * 2 - 1, 1 - local.y() / self.H * 2, 0.0, 1.0])
        w = np.linalg.inv(self._vp) @ ndc
        self._sq_center = w[:3] / w[3]
        zone = self.zone_at(local)
        small = zone in ("eye_l", "eye_r", "nose", "mouth", "cheek_l", "cheek_r", "hand_l", "hand_r")
        self._sq_radius = (0.05 if small else 0.085) * self.m.height
        self._sq_target = 1.0

    def feet_y(self) -> float:
        """Window y where her feet touch the ground."""
        return self.project(np.array([self.off[0] + self.m.center_x, self.off[1] + self.m.min_y, 0.0]))[1]

    def hands_point(self, which: str | None = None) -> tuple[float, float] | None:
        """Where her hands are (window coordinates), for drawing what she holds. which: left / right / None=both."""
        if self._W is None:
            return None
        names = [f"{which}Hand"] * 2 if which else ["leftHand", "rightHand"]
        pts = [self.m.bone_pos(self._W, b) for b in names]
        if any(p is None for p in pts):
            return None
        a, b = self.project(pts[0]), self.project(pts[1])
        return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2

    def _cloth_uniforms(self) -> dict | None:
        if self.cloth is None or self._W is None:
            return None
        m, W = self.m, self._W
        hip = m.bone_pos(W, "hips")
        parts = [m.bone_pos(W, b) for b in ("leftUpperLeg", "leftFoot", "rightUpperLeg", "rightFoot")]
        knee = m.bone_pos(W, "leftLowerLeg")
        if hip is None or knee is None or any(p is None for p in parts):
            return None
        return {"hip_y": hip[1] - 0.02 * self.k, "length": max(0.1, hip[1] - knee[1]),
                "uLegL0": parts[0], "uLegL1": parts[1], "uLegR0": parts[2], "uLegR1": parts[3],
                "offset": self.cloth.off, "flutter": self.cloth.flutter, "time": self.t,
                "leg_radius": self._cloth_leg_r}

    def _update_squish(self, dt: float) -> None:
        # squeeze: springy (overshoots into a little bulge when released); jelly: whole-body wobble
        for _ in range(2):
            h = dt / 2
            self._sq_v += (170.0 * (self._sq_target - self._sq) - 9.0 * self._sq_v) * h
            self._sq += self._sq_v * h
            self._jelly_v += (-150.0 * self._jelly - 7.0 * self._jelly_v) * h
            self._jelly += self._jelly_v * h

    def _manip_pose(self, P: dict, add) -> None:
        clamp = lambda v, a: max(-a, min(a, v))   # noqa: E731
        if self._move_head:
            dx, dy = self._move_head
            add("neck", q_mul(q_mul(_ry(clamp(dx * 0.2, 25)), _rx(clamp(dy * 0.15, 20))), _rz(clamp(-dx * 0.12, 15))))
            add("head", q_mul(q_mul(_ry(clamp(dx * 0.3, 38)), _rx(clamp(dy * 0.25, 28))), _rz(clamp(-dx * 0.28, 30))))
            add("spine", _rz(clamp(-dx * 0.04, 6)))
        if self._move_body:
            dx, dy = self._move_body
            add("spine", q_mul(_rz(clamp(-dx * 0.12, 20)), _rx(clamp(dy * 0.1, 18))))
            add("chest", q_mul(_rz(clamp(-dx * 0.08, 14)), _rx(clamp(dy * 0.06, 12))))
        if self._move_leg and self._W is not None:
            zone, pt = self._move_leg
            side = "left" if zone == "leg_l" else "right"
            hp = self.m.bone_pos(self._W, f"{side}UpperLeg")
            if hp is not None:
                sx, sy = self.project(hp)
                d_world = np.array([pt.x() - sx, -(pt.y() - sy), 0.3 * self.ppm * self.k])
                d = q_rotate(q_inv(self._root_rot), normalize(d_world))
                if d[1] > 0.2:                      # can't bend that far up: keep it a high kick
                    d[1] = 0.2
                P[f"{side}UpperLeg"] = q_from_vectors(np.array([0.0, -1.0, 0.0]), d)
                P[f"{side}LowerLeg"] = _rx(8)

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
            ph = self._walk_ph
            add("leftLowerArm", _ry(-18 - max(0.0, math.sin(ph)) * 15))
            add("rightLowerArm", _ry(18 + max(0.0, -math.sin(ph)) * 15))
            add("chest", _ry(math.sin(ph) * 6))
            add("spine", _rx(4))
            add("head", _rx(-abs(math.cos(ph)) * 2))
        # gestures (blend in/out smoothly)
        if self.gesture and state in (IDLE, REACT, PET, SIT):
            self._blend_gesture(P)

    def _blend_gesture(self, P: dict) -> None:
        if True:
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
        if (ctx.holding or ctx.angry > 0.3 or ctx.pose) and self.gesture not in ("nya", "heart", "kiss", "wave_l", "wave_r"):
            self.gesture = None          # arms are busy hugging / crossed
            self._next_gesture = max(self._next_gesture, 3.0)
            return
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
        if ctx.pose.startswith("peer_") and not ctx.blink:
            tgt["blink"] = max(tgt.get("blink", 0.0), 0.45)            # squinting at your screen
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
        elif self.gesture:
            self.gesture_t += dt
            if self.gesture_t >= GESTURE_LEN.get(self.gesture, 2.0) or state in (DRAG, FALL, WALK, SLEEP):
                self.gesture = None
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
        elif ctx.angry > 0.3 and state in (IDLE, REACT):
            target_yaw = (-38.0 if self.look[0] >= 0 else 38.0) + ctx.user_yaw   # "hmph!" turns away
        elif ctx.pose == "sick" and state in (IDLE, REACT, PET):
            target_yaw = 42.0                                                  # side-on, so you see her double over
        elif ctx.pose.rpartition("_")[0] in ("pet", "eww", "spray", "mop", "peer") and state in (IDLE, REACT, PET):
            target_yaw = 32.0 if ctx.pose.endswith("_l") else -32.0            # turns toward her pet / the mess
        elif state in (SIT, SLEEP):
            target_yaw = 8.0 + ctx.user_yaw
        else:
            target_yaw = self.look[0] * 14 + ctx.user_yaw
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
        self._pitch += (ctx.user_pitch - self._pitch) * min(1.0, dt * 10)
        kn = max(ctx.knock, ctx.lie * 0.92)
        root = (translate(*self.off) @ rot_m4(_rx(self._pitch * (1 - kn)))
                @ translate(*pivot) @ rot_m4(_rz(tilt)) @ translate(*-pivot)
                @ translate(0, hop, 0) @ rot_m4(q_mul(_rz(ctx.knock_dir * 14 * ctx.knock), _rx(-82 * kn)))
                @ rot_m4(_ry((self.yaw + ctx.spin) * (1 - kn))))
        self._update_squish(dt)
        j = max(-0.6, min(0.6, self._jelly))
        S = np.diag([1 - j * 0.035, 1 + j * 0.06, 1 - j * 0.035, 1.0])
        root = root @ S
        if self.m.version == 0:
            root = root @ rot_m4(R180)

        # pose (smoothly blended)
        if state == WALK:
            hips_r = self.m.bone_pos(self.m.rest_world, "hips")
            leg = max(0.2, (hips_r[1] - self.m.min_y) if hips_r is not None else 0.45 * self.m.height)
            speed = abs(vx) / max(1.0, self.ppm)                       # m/s
            f = 1.15 + min(0.7, speed * 0.6)                            # steps per second (cadence)
            amp = math.degrees(math.asin(min(0.85, speed / (f * 4 * leg) + 1e-6)))
            self._walk_amp += (max(10.0, min(30.0, amp)) - self._walk_amp) * min(1.0, dt * 4)
            self._walk_ph += f * math.tau * dt
        targets, hip = self._pose_targets(state, ctx)
        a = min(1.0, dt * (22 if state in (WALK, DRAG) or ctx.music else 12))
        bones = set(targets) | set(self.cur_pose)
        for b in bones:
            goal = targets.get(b, IDENTITY_Q)
            mid = q_slerp(self.mid_pose.get(b, self.cur_pose.get(b, IDENTITY_Q)), goal, a)
            self.mid_pose[b] = mid
            self.cur_pose[b] = q_slerp(self.cur_pose.get(b, IDENTITY_Q), mid, a)
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
        # breeze with occasional gusts that make her hair flutter
        self._next_gust -= dt
        if self._next_gust <= 0:
            self._gust = random.uniform(2.0, 3.5) * random.choice((-1, 1))
            self._next_gust = random.uniform(5.0, 11.0)
        self._gust *= max(0.0, 1.0 - dt * 0.9)
        flutter = math.sin(self.t * 9.0) * 0.35 * abs(self._gust)
        wind = self._canon((math.sin(self.t * 1.3) * 0.9 + math.sin(self.t * 3.1) * 0.35 + self._gust,
                            0.15 + flutter * 0.3, math.cos(self.t * 0.9) * 0.7 + flutter))
        self._apply_brush()
        self._wind = wind
        if self.cloth is not None:
            acc = np.array([ax, -ay, 0.0]) / self.ppm
            vel = np.array([vx, -vy, 0.0]) / self.ppm
            phase = self.t * math.tau * 1.7 if state == WALK else None
            self.cloth.step(dt, acc, vel, wind, phase)
        for _ in range(n):
            self.springs.update(W, dt / n, wind)
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
        sq = (self._sq_center, self._sq_radius, self._sq * 0.85) if abs(self._sq) > 0.01 else None
        img = self.gl.render(self._W, self._vp, self._morph_weights(), squeeze=sq, cloth=self._cloth_uniforms())
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
        blush = max(blush, float(self.variant.get("blush", 0.0)))
        self._draw_accessories(p)
        if ctx.expr is not None:
            blush = max(blush, ctx.expr.blush)
        if state == PET or self.timers["blush"] > 0:
            blush = 0.9
        if (ctx.expr is not None and ctx.expr.sparkle) or (self.variant.get("sparkle_eyes") and state != SLEEP
                                                            and not ctx.blink and self.timers["eye_shut"] <= 0):
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
        if self.gesture == "kiss" and self.gesture_t > 0.9:
            ph = min(1.0, (self.gesture_t - 0.9) / 1.0)
            mx, my = self.project(self._head_center() + self._canon((-0.02, -0.06, 0.08)))
            col = QColor("#ff5c7a")
            col.setAlphaF(1 - ph * 0.8)
            _heart(p, mx - ph * 40, my - ph * 30, 10 + ph * 8, col)
        if self.timers["puni"] > 0:
            ph = 1 - self.timers["puni"]
            x, y = self.project(self._sq_center)
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            col = QColor("#ff5c8a")
            col.setAlphaF(max(0.0, 1 - ph))
            p.setPen(col)
            p.drawText(QPointF(x + 8, y - 10 - ph * 18), "puni♡")
        if self.timers["steam"] > 0 or ctx.emotion == "flustered" or ctx.angry > 0.5:
            draw_steam(p, top[0], top[1] + 4, t, 0.9 + 0.3 * ctx.angry)
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
        if ctx.emotion == "angry" or ctx.angry > 0.3:
            x, y = self.project(hc + self._canon((-0.07, 0.08, 0)))
            s = 5.0 + ctx.angry * (2.5 + math.sin(t * 9) * 1.2)     # throbbing anger vein
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
