"""Static 3D model character (FBX without a skeleton, e.g. a scanned / sculpted figure).

The mesh can't bend its joints, so she is animated procedurally in the vertex shader
("soft-body" deformation by height): her head turns and nods toward the cursor, her chest
breathes, and her whole body bends and wobbles on springs driven by window motion (drag,
throw, walk, fall). Touch zones are mapped from body height. Faces and tears are overlays.
"""
from __future__ import annotations

import logging
import math
import random
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap, QRadialGradient

from .fbx.mesh import load_fbx_mesh
from .sprites import DRAG, FALL, IDLE, PET, REACT, SIT, SLEEP, WALK, FrameContext, _heart, _pen, draw_steam
from .vrm.math3d import ortho, q_axis, q_mul, q_rotate, q_to_m3, rot_m4, translate
from .vrm.model import Material, Node, Prim
from .vrm.renderer import VrmRenderer

log = logging.getLogger(__name__)

TOP_M, BOT_M, WIDTH_M = 0.12, 0.04, 0.62
NECK = 0.845          # fraction of body height where the head starts (adult proportions)


def _meta(path) -> dict:
    """Optional character.json next to the model: {"front": "-x", "tpose": true, "name": "..."}."""
    import json
    f = Path(path).with_name("character.json")
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


class _Images:
    """Mimics the glTF image accessor the renderer uses."""

    def __init__(self, files: list[Path]):
        self.files = files
        self.j = {"samplers": []}

    def image_bytes(self, i: int) -> bytes:
        return self.files[i].read_bytes()


class MeshModel:
    """Adapter so VrmRenderer can draw a static mesh (one node, one primitive, no skin)."""

    def __init__(self, path: str | Path):
        mesh = load_fbx_mesh(path)
        meta = _meta(path)
        p = mesh.pos.copy()
        nrm = mesh.nrm.copy()
        # turn her so she faces the camera (+Z); "front" says which axis the model faces
        front = meta.get("front", "-x")
        rot = {"+z": 0, "-z": 180, "-x": 90, "+x": -90}.get(front, 0)
        if rot:
            c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            for arr in (p, nrm):
                x, z = arr[:, 0].copy(), arr[:, 2].copy()
                arr[:, 0] = c * x + s * z
                arr[:, 2] = -s * x + c * z
        mesh.nrm = nrm
        # stand her on y = 0, centred on x/z
        mn, mx = p.min(axis=0), p.max(axis=0)
        p[:, 0] -= (mn[0] + mx[0]) / 2
        p[:, 2] -= (mn[2] + mx[2]) / 2
        p[:, 1] -= mn[1]
        self.height = float(mx[1] - mn[1])
        self.min_y, self.max_y, self.center_x = 0.0, self.height, 0.0
        self.version = 1
        files = [mesh.textures[r] for r in ("basecolor",) if r in mesh.textures]
        self.g = _Images(files)
        self.textures = [{"source": i} for i in range(len(files))]
        mat = Material(tex=0 if files else None, shade=[0.62, 0.6, 0.66], shade_shift=0.15, shade_toony=0.55,
                       double_sided=True)
        self.materials = [mat]
        prim = Prim(0, p.astype(np.float32), mesh.nrm, mesh.uv, None, None, mesh.idx, 0)
        self.mesh_prims = {0: [prim]}
        self.nodes = [Node("mesh", -1, [], np.zeros(3), np.array([0, 0, 0, 1.0]), np.ones(3), 0, None)]
        self.draw_nodes = [0]
        self.skins = []
        self.positions = prim.pos
        self.meta = meta


class MeshCharacter:
    mirrors_itself = True

    def __init__(self, path: str | Path, scale: float, dpr: float):
        self.m = m = MeshModel(path)
        self.fig_px = round(300 * max(0.4, min(3.0, scale)))
        self.ppm = self.fig_px / m.height
        self.W = round(WIDTH_M * m.height * self.ppm)
        self.H = round((1 + TOP_M + BOT_M) * m.height * self.ppm)
        self.dpr = dpr
        self.gl = VrmRenderer(m, round(self.W * dpr), round(self.H * dpr))
        self._analyse()
        self.t = 0.0
        self.yaw = 0.0
        self.look = [0.0, 0.0]
        self._look_target = [0.0, 0.0]
        # springs: body bend (x, z), head (yaw, pitch offsets), squash
        self.bend = np.zeros(2)
        self.bend_v = np.zeros(2)
        self.squash = 0.0
        self.squash_v = 0.0
        self.head_kick = np.zeros(2)
        self.head_kick_v = np.zeros(2)
        self.timers = {"eye_shut": 0.0, "blush": 0.0, "tears": 0.0, "happy": 0.0, "steam": 0.0}
        self._press_local = QPointF()
        self._tug_hand = None
        self._grab_blend = 0.0
        self._grab_last = None
        self._frame: QImage | None = None
        self._vp = np.eye(4)
        self._root = np.eye(4)
        self._deform = {}
        self.step(1 / 30, 0, 0, 0, 0, IDLE, FrameContext())

    # ------------------------------------------------------------------ geometry analysis
    def _analyse(self) -> None:
        """Find where the face and hands are from the vertex cloud."""
        p, h = self.m.positions, self.m.height
        head = p[p[:, 1] > NECK * h]
        self.head_c = np.array([float(np.median(head[:, 0])), float(head[:, 1].mean()), float(np.median(head[:, 2]))]) \
            if len(head) else np.array([0, 0.92 * h, 0])
        self.face_z = float(np.percentile(head[:, 2], 97)) if len(head) else 0.08 * h   # front of the face
        self.head_r = max(0.06, float(np.percentile(np.abs(head[:, 0] - self.head_c[0]), 95))) if len(head) else 0.1
        self.hands = {}
        # T-pose: find the arms (thin geometry far out sideways) so the shader can lower them
        self.arm_on = bool(self.m.meta.get("tpose", False))
        self.arm_rest = math.radians(float(self.m.meta.get("arm_down_deg", 72)))
        self.arm_ang = np.array([self.arm_rest, self.arm_rest])
        if self.arm_on:
            far = p[np.abs(p[:, 0]) > 0.3 * h]
            self.arm_y = float(np.median(far[:, 1])) if len(far) else 0.78 * h
            chest = p[np.abs(p[:, 1] - (self.arm_y - 0.055 * h)) < 0.01 * h]
            self.shoulder_x = max(0.1 * h, float(np.percentile(np.abs(chest[:, 0]), 95)) + 0.01 * h) \
                if len(chest) else 0.15 * h
            for side, sgn in (("hand_l", 1), ("hand_r", -1)):
                tips = p[(p[:, 0] * sgn) > np.abs(p[:, 0]).max() * 0.9]
                if len(tips):
                    self.hands[side] = tips.mean(axis=0)
            return
        # otherwise hands: outermost points in the hip band on each side
        band = p[(p[:, 1] > 0.38 * h) & (p[:, 1] < 0.62 * h)]
        if len(band):
            for side, sel in (("hand_r", band[:, 0] < 0), ("hand_l", band[:, 0] > 0)):
                b = band[sel]
                if len(b):
                    far = b[np.argsort(np.abs(b[:, 0]))[-max(1, len(b) // 200):]]
                    self.hands[side] = far.mean(axis=0)

    # ------------------------------------------------------------------ interface
    def size(self) -> QSize:
        return QSize(self.W, self.H)

    def foot_offset(self) -> int:
        return round(BOT_M * self.m.height * self.ppm)

    def project(self, p) -> tuple[float, float]:
        c = self._vp @ np.append(np.asarray(p, float), 1.0)
        return (c[0] / c[3] + 1) / 2 * self.W, (1 - c[1] / c[3]) / 2 * self.H

    def _world(self, local) -> np.ndarray:
        """Body-local point (with head deformation for points above the neck) -> world."""
        p = np.asarray(local, float).copy()
        h = self.m.height
        if self.arm_on and abs(p[0]) > self.shoulder_x and abs(p[1] - self.arm_y) < 0.06 * h:
            side = 1.0 if p[0] >= 0 else -1.0
            a = -side * self.arm_ang[0 if side > 0 else 1]
            px, py = side * self.shoulder_x, self.arm_y
            dx, dy = p[0] - px, p[1] - py
            p[0], p[1] = px + math.cos(a) * dx - math.sin(a) * dy, py + math.sin(a) * dx + math.cos(a) * dy
        if p[1] > NECK * h:
            piv = np.array([self.head_c[0], NECK * h, self.head_c[2]])
            p = piv + self._deform["head_m3"] @ (p - piv)
        f = (p[1] / h) ** 2
        p[0] += self.bend[0] * f * h
        p[2] += self.bend[1] * f * h
        return (self._root @ np.append(p, 1.0))[:3]

    def _face_pt(self, dx: float, dy: float) -> np.ndarray:
        h = self.m.height
        return self._world([self.head_c[0] + dx * h, self.head_c[1] + dy * h, self.face_z])

    def head_rect(self, state: str) -> QRectF:
        cx, cy = self.project(self._world(self.head_c))
        r = self.head_r * 1.3 * self.ppm
        return QRectF(cx - r, cy - r, 2 * r, 2 * r)

    def icon(self) -> QPixmap:
        img = self._frame or self.render(IDLE, FrameContext(), self.dpr)
        r = self.head_rect(IDLE)
        d = img.devicePixelRatio()
        crop = img.copy(int(r.x() * d), int(r.y() * d), int(r.width() * d), int(r.height() * d))
        return QPixmap.fromImage(crop.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))

    def _zones(self):
        h = self.m.height
        z = [("eye_l", self._face_pt(0.022, 0.012), 0.016 * h), ("eye_r", self._face_pt(-0.022, 0.012), 0.016 * h),
             ("nose", self._face_pt(0.0, -0.012), 0.011 * h),
             ("mouth", self._face_pt(0.0, -0.03), 0.014 * h),
             ("cheek_l", self._face_pt(0.03, -0.012), 0.016 * h), ("cheek_r", self._face_pt(-0.03, -0.012), 0.016 * h)]
        for side, p in self.hands.items():
            z.append((side, self._world(p), 0.05 * h))
        z.append(("head", self._world(self.head_c), self.head_r * 1.15))
        for side, sgn in (("leg_l", 1), ("leg_r", -1)):   # legs: lower half, each side of centre
            z.append((side, self._world([sgn * 0.06 * h, 0.15 * h, 0]), 0.06 * h))
            z.append((side, self._world([sgn * 0.06 * h, 0.32 * h, 0]), 0.06 * h))
        z.append(("chest", self._world([0, 0.72 * h, self.face_z * 0.5]), 0.07 * h))
        z.append(("skirt", self._world([0, 0.5 * h, 0]), 0.09 * h))
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
        for name, p, r in self._zones():
            sx, sy = self.project(p)
            if (sx - local.x()) ** 2 + (sy - local.y()) ** 2 <= (r * self.ppm) ** 2:
                return name
        hx, hy = self.project(self._world(self.head_c))
        if local.y() < hy + self.head_r * 2.5 * self.ppm:
            return "hair_l" if local.x() > hx else "hair_r"
        return "body"

    # ------------------------------------------------------------------ interaction
    def look_at(self, local: QPointF | None) -> None:
        if local is None:
            self._look_target = [0.0, 0.0]
            return
        hx, hy = self.project(self._world(self.head_c))
        self._look_target = [math.tanh((local.x() - hx) / 350.0), math.tanh((hy - local.y()) / 350.0)]

    def squint(self, on: bool) -> None:
        pass

    def react(self, zone: str, kind: str = "tap") -> None:
        s = random.choice((-1.0, 1.0))
        if kind == "rough":
            self.timers["eye_shut"] = 1.2
            self.timers["tears"] = 4.0
            self.head_kick_v += np.array([s * 120.0, -160.0])
            self.bend_v += np.array([s * 0.6, -0.5])
            self.squash_v += 0.6
            return
        if zone in ("nose", "eye_l", "eye_r") and kind != "pet":
            self.timers["eye_shut"] = 0.45 if zone == "nose" else 0.9   # blink / squint
            self.head_kick_v += np.array([0.0, -140.0])                  # recoil
            return
        if zone in ("chest", "skirt"):
            self.timers["blush"] = 4.0
            if kind != "pet":
                self.timers["steam"] = 3.0
                self.bend_v += np.array([0.0, -0.6])                      # flustered recoil backwards
            return
        if zone in ("cheek_l", "cheek_r", "mouth", "hand_l", "hand_r"):
            self.timers["blush"] = 3.0
        if zone == "head":
            self.timers["happy"] = 2.0
            self.squash_v += 0.3
        elif zone in ("hair_l", "hair_r"):
            self.head_kick_v += np.array([(-1 if zone == "hair_l" else 1) * 60.0, 0.0])
        else:
            self.bend_v += np.array([s * 0.25, 0.0])

    def tug(self, zone: str, delta: QPointF | None) -> None:
        if zone in ("hand_l", "hand_r") and self.arm_on:
            self._tug_hand = None if delta is None else (zone, self._press_local + delta)
            return
        if delta is None:
            return
        self.bend_v += np.array([delta.x(), 0.0]) * 0.002   # pulls her over a little

    # ------------------------------------------------------------------ simulation
    def step(self, dt: float, ax: float, ay: float, vx: float, vy: float, state: str, ctx: FrameContext) -> None:
        self.t += dt
        t = self.t
        for k in self.timers:
            self.timers[k] = max(0.0, self.timers[k] - dt)
        ex = ctx.expr
        for i in (0, 1):
            goal = self._look_target[i]
            if ex is not None:
                goal = goal * ex.follow + ex.look[i] * 0.6
            self.look[i] += (goal - self.look[i]) * min(1.0, dt * 6)
        # body bend spring: lags behind window acceleration (soft-body wobble)
        ax, ay = max(-30000.0, min(30000.0, ax)), max(-30000.0, min(30000.0, ay))
        target = np.zeros(2)
        if state == WALK:
            target[0] = (-0.03 if ctx.facing_left else 0.03)
        if ctx.music:
            target[0] += math.sin(t * 2.2) * 0.025
        force = -40.0 * (self.bend - target) - 5.0 * self.bend_v + np.array([-ax * 0.00006, 0.0])
        self.bend_v += force * dt
        self.bend += self.bend_v * dt
        self.bend = np.clip(self.bend, -0.18, 0.18)
        # squash: falls and landings, breathing
        sq_t = 0.0
        if state in (SIT, SLEEP):
            sq_t = 0.04
        f = -60.0 * (self.squash - sq_t) - 7.0 * self.squash_v - ay * 0.000015
        self.squash_v += f * dt
        self.squash = max(-0.1, min(0.15, self.squash + self.squash_v * dt))
        self.head_kick_v += (-80.0 * self.head_kick - 10.0 * self.head_kick_v) * dt
        self.head_kick += self.head_kick_v * dt
        # head orientation
        yaw = self.look[0] * 35 + self.head_kick[0] * 0.2
        pitch = -self.look[1] * 20 + self.head_kick[1] * 0.2
        if state == SLEEP or ctx.emotion == "sleepy":
            pitch, yaw = 22.0, 8.0
        if state == PET:
            yaw += math.sin(t * 3.2) * 8
            pitch += 6
        if ctx.music:
            pitch += ctx.beat * 10 - 2
        if ctx.mouth > 0:
            pitch += ctx.mouth * 4
        if ex is not None:
            pitch += 8 if ex.gaze == "downcast" else 0
            roll = ex.roll
        else:
            roll = 0.0
        q = q_mul(q_mul(q_axis((0, 1, 0), yaw), q_axis((1, 0, 0), pitch)), q_axis((0, 0, 1), -roll))
        # arms: relaxed, raised when dragged/falling, waving with music, reaching when you pull a hand
        if self.arm_on:
            goal = np.array([self.arm_rest, self.arm_rest])
            if state == DRAG and ctx.grab.startswith("hand"):
                i = 0 if ctx.grab.endswith("_l") else 1
                goal[i] = -1.35                                   # held arm stretched up overhead
                goal[1 - i] = self.arm_rest - 0.5 + math.sin(t * 11) * 0.6 * ctx.struggle
            elif state == DRAG and ctx.grab.startswith("leg"):
                goal[:] = -1.1 + np.array([1, -1]) * math.sin(t * 10) * 0.4 * ctx.struggle   # arms dangle "down"
            elif state in (DRAG, FALL):
                goal[:] = math.radians(25) + math.sin(t * 9) * 0.15
            elif ctx.music:
                goal += np.array([1, -1]) * math.sin(t * 2.2) * 0.12 - ctx.beat * 0.15
            elif state == PET:
                goal[:] = self.arm_rest - 0.15
            if self._tug_hand:
                zone, pt = self._tug_hand
                i, sgn = (0, 1.0) if zone == "hand_l" else (1, -1.0)
                sh = self.project(self._world([sgn * self.shoulder_x, self.arm_y, 0]))
                dx, dy = (pt.x() - sh[0]) * (1 if self.yaw > -90 else -1), pt.y() - sh[1]
                goal[i] = max(-1.2, min(1.45, math.atan2(dy, abs(dx) + 1e-3)))
            self.arm_ang += (goal - self.arm_ang) * min(1.0, dt * 8)
        if state == DRAG:   # struggling: head shakes, body wriggles
            s = ctx.struggle
            q = q_mul(q_axis((0, 1, 0), math.sin(t * 11) * 16 * s), q)
            self.bend_v[0] += math.sin(t * 10) * s * dt * 14
        breath = math.sin(t * math.tau / (5.0 if state == SLEEP else 3.6)) * 0.008
        self._deform = {"head_m3": q_to_m3(q), "breath": breath}
        # body yaw
        if state == WALK:
            ty = -55.0 if ctx.facing_left else 55.0
        elif ctx.emotion == "hurt":
            ty = -35.0 if self.look[0] >= 0 else 35.0
        else:
            ty = self.look[0] * 12
        self.yaw += (ty - self.yaw) * min(1.0, dt * 5)
        hop = 0.0
        if state == WALK:
            hop = abs(math.sin(t * 7.0)) * 0.012 * self.m.height
        if state == REACT:
            hop = max(0.0, math.sin(min(1.0, ctx.state_t / 0.35) * math.pi)) * 0.03 * self.m.height
        tilt = -ctx.tilt * 0.5 if state == DRAG and not ctx.grab else 0.0
        if ctx.grab:
            self.yaw *= max(0.0, 1 - dt * 6)
        h = self.m.height
        piv = np.array([0, h * 0.85, 0])
        self._root = (translate(0, hop, 0) @ translate(*piv) @ rot_m4(q_axis((0, 0, 1), tilt)) @ translate(*-piv)
                      @ rot_m4(q_axis((0, 1, 0), self.yaw)))
        half = WIDTH_M * h / 2
        self._vp = ortho(-half, half, -BOT_M * h, (1 + TOP_M) * h, -10, 10)
        self._pin_limb(ctx, dt)

    def _pin_limb(self, ctx: FrameContext, dt: float) -> None:
        """Lifted by a hand or leg: hang from it, pinned under the cursor, swinging like a pendulum."""
        if ctx.grab:
            self._grab_last = (ctx.grab, ctx.grab_pt)
            self._grab_blend = min(1.0, self._grab_blend + dt * 7)
        else:
            self._grab_blend = max(0.0, self._grab_blend - dt * 5)
        if self._grab_blend <= 0 or not self._grab_last:
            return
        g, pt = self._grab_last
        h = self.m.height
        sgn = 1.0 if g.endswith("_l") else -1.0
        if g.startswith("leg"):
            local = np.array([sgn * 0.06 * h, 0.03 * h, 0.0])
        else:
            local = self.hands.get("hand_l" if sgn > 0 else "hand_r", np.array([sgn * 0.3 * h, 0.78 * h, 0.0]))
        limb = self._world(local)
        clip = self._vp @ np.append(limb, 1.0)
        ndc = np.array([pt[0] / self.W * 2 - 1, 1 - pt[1] / self.H * 2, clip[2] / clip[3], 1.0])
        w = np.linalg.inv(self._vp) @ ndc
        target = w[:3] / w[3]
        b = self._grab_blend * self._grab_blend * (3 - 2 * self._grab_blend)
        swing = (180.0 if g.startswith("leg") else 0.0) - ctx.tilt * 0.9 + math.sin(self.t * 7) * 6 * ctx.struggle
        M = translate(*(limb + (target - limb) * b)) @ rot_m4(q_axis((0, 0, 1), swing * b)) @ translate(*-limb)
        self._root = M @ self._root

    # ------------------------------------------------------------------ rendering
    def render(self, state: str, ctx: FrameContext, dpr: float) -> QImage:
        if abs(dpr - self.dpr) > 0.01:
            self.dpr = dpr
            self.gl.ctx.makeCurrent(self.gl.surface)
            self.gl.resize(round(self.W * dpr), round(self.H * dpr))
            self.gl.ctx.doneCurrent()
        h = self.m.height
        W = self._root[None].copy()
        deform = {"neck": NECK * h, "pivot": (float(self.head_c[0]), float(self.head_c[2])), "height": h,
                  "head": self._deform["head_m3"], "bend": (float(self.bend[0]), float(self.bend[1])),
                  "squash": float(self.squash), "breath": self._deform["breath"],
                  "arms": (self.arm_on, getattr(self, "arm_y", 0.0), getattr(self, "shoulder_x", 0.0),
                           float(self.arm_ang[0]), float(self.arm_ang[1]))}
        img = self.gl.render(W, self._vp, {}, deform=deform)
        img.setDevicePixelRatio(self.dpr)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._overlays(p, state, ctx)
        p.end()
        self._frame = img
        return img

    def _overlays(self, p: QPainter, state: str, ctx: FrameContext) -> None:
        t = ctx.t
        h = self.m.height
        closed = state == SLEEP or ctx.blink or self.timers["eye_shut"] > 0 or self.timers["happy"] > 0
        ex = ctx.expr
        happy_eyes = ex is not None and ex.happy_eyes
        if happy_eyes:
            self.timers["happy"] = max(self.timers["happy"], 0.05)
        if state == SLEEP or self.timers["eye_shut"] > 0 or self.timers["happy"] > 0 or state == PET:
            # closed-eye arcs drawn over the face (the scan has no eyelid shapes)
            for sx in (1, -1):
                x, y = self.project(self._face_pt(sx * 0.022, 0.012))
                r = 0.012 * h * self.ppm
                p.setPen(_pen(QColor(40, 25, 30, 220), max(1.2, r * 0.25)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                if self.timers["eye_shut"] > 0:
                    p.drawLine(QPointF(x - r * sx, y - r * 0.6), QPointF(x + r * 0.5 * sx, y))
                    p.drawLine(QPointF(x + r * 0.5 * sx, y), QPointF(x - r * sx, y + r * 0.6))
                elif self.timers["happy"] > 0 or state == PET:
                    p.drawArc(QRectF(x - r, y - r * 0.4, 2 * r, 1.4 * r), 20 * 16, 140 * 16)
        del closed
        blush = {"shy": 0.8, "flustered": 1.0, "happy": 0.35, "excited": 0.4, "playful": 0.3}.get(ctx.emotion, 0.0)
        if ex is not None:
            blush = max(blush, ex.blush)
            if ex.sparkle:
                for sx in (1, -1):
                    x, y = self.project(self._face_pt(sx * 0.02, 0.018))
                    tw = 0.6 + 0.4 * math.sin(t * 8 + sx)
                    p.setPen(_pen(QColor(255, 255, 255, int(230 * tw)), 1.2))
                    for dx, dy in ((2.5, 0), (0, 2.5)):
                        p.drawLine(QPointF(x - dx, y - dy), QPointF(x + dx, y + dy))
        if state == PET or self.timers["blush"] > 0:
            blush = 0.9
        if blush > 0.05:
            for sx in (1, -1):
                cx, cy = self.project(self._face_pt(sx * 0.03, -0.014))
                r = 0.016 * h * self.ppm
                g = QRadialGradient(QPointF(cx, cy), r)
                g.setColorAt(0, QColor(255, 90, 120, int(140 * blush)))
                g.setColorAt(1, QColor(255, 90, 120, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawEllipse(QPointF(cx, cy), r, r * 0.6)
        if self.timers["tears"] > 0 or ctx.emotion in ("hurt", "sad"):
            for sx in (1, -1):
                slide = (t * 0.7 + (0.5 if sx < 0 else 0)) % 1.0
                x, y = self.project(self._face_pt(sx * 0.024, -0.005 - slide * 0.03))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(140, 200, 255, int(230 * (1 - slide))))
                p.drawEllipse(QPointF(x, y), 2.2, 3.0)
        top = self.project(self._world(self.head_c + np.array([0, self.head_r * 1.4, 0])))
        if self.timers["steam"] > 0 or ctx.emotion == "flustered":
            draw_steam(p, top[0], top[1] + 6, t, 0.9)
        if ctx.emotion == "angry":
            x, y = top[0] - 16, top[1] + 10
            p.setPen(_pen(QColor("#ff3b3b"), 2.2))
            for a, b in (((-5, -1.5), (-1.5, -1.5)), ((-1.5, -5), (-1.5, -1.5)), ((5, 1.5), (1.5, 1.5)), ((1.5, 5), (1.5, 1.5))):
                p.drawLine(QPointF(x + a[0], y + a[1]), QPointF(x + b[0], y + b[1]))
        if ctx.pet_level > 0:
            for i in range(3):
                ph = (t * 0.7 + i / 3) % 1.0
                col = QColor("#ff5c7a")
                col.setAlphaF(max(0.0, min(1.0, ctx.pet_level)) * (1 - ph))
                _heart(p, top[0] + (i - 1) * 18 + math.sin(t * 3 + i) * 3, top[1] - ph * 24, 10, col)
        if ctx.music:
            p.setFont(QFont("Segoe UI Symbol", 12))
            for i in range(2):
                ph = (t * 0.45 + i / 2) % 1.0
                col = QColor("#a07bff")
                col.setAlphaF(1 - ph)
                p.setPen(col)
                p.drawText(QPointF(top[0] + (-30 if i else 26), top[1] + 24 - ph * 40), "♪" if i else "♫")
        if state == SLEEP:
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            for i in range(2):
                ph = (t * 0.4 + i / 2) % 1.0
                col = QColor("#7b8cff")
                col.setAlphaF(1 - ph)
                p.setPen(col)
                p.drawText(QPointF(top[0] + 22 + ph * 12, top[1] + 8 - ph * 26), "z")
