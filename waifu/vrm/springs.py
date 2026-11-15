"""VRM spring-bone physics (hair, skirt, ribbons...). Verlet integration like UniVRM / three-vrm.

Tails live in world space, and the model's root is moved through the world as the
desktop window moves, so dragging, throwing and walking make hair and cloth swing.
"""
from __future__ import annotations

import numpy as np

from .math3d import m3_to_q, normalize, q_from_vectors, q_mul, q_rotate, trs
from .model import VrmModel


class _J:
    __slots__ = ("node", "child", "stiff", "grav", "gdir", "drag", "hit", "axis", "length",
                 "cur", "prev", "init_r", "child_local", "pin", "spring", "grab")


class SpringSystem:
    def __init__(self, model: VrmModel):
        self.m = model
        self.joints: list[_J] = []
        self.spring_cols: list[list[int]] = []
        for si, s in enumerate(model.springs):
            self.spring_cols.append(s.colliders)
            for sj in s.joints:
                j = _J()
                j.node, j.child, j.spring = sj.node, sj.child, si
                j.stiff, j.grav, j.gdir, j.drag, j.hit = sj.stiffness, sj.gravity, normalize(sj.gdir), sj.drag, sj.hit_radius
                n = model.nodes[sj.node]
                if sj.child is not None:
                    j.child_local = model.nodes[sj.child].rest_t * model.nodes[sj.child].rest_s
                else:
                    j.child_local = normalize(n.rest_t) * 0.07 if np.linalg.norm(n.rest_t) > 1e-6 else np.array([0, -0.07, 0])
                j.axis = normalize(j.child_local)
                j.init_r = n.rest_r.copy()
                j.pin = None
                j.grab = None
                self.joints.append(j)
        self._ready = False

    def reset(self, W: np.ndarray) -> None:
        for j in self.joints:
            tail = (W[j.node] @ np.append(j.child_local, 1.0))[:3]
            j.length = float(np.linalg.norm(tail - W[j.node][:3, 3])) or 0.05
            j.cur = tail.copy()
            j.prev = tail.copy()
        self._ready = True

    def shift(self, d: np.ndarray) -> None:
        """Teleport all tails (e.g. when the window jumps) so nothing explodes."""
        for j in self.joints:
            j.cur = j.cur + d
            j.prev = j.prev + d

    def _collider_world(self, W):
        out = []
        for c in self.m.colliders:
            M = W[c.node]
            p = (M @ np.append(c.offset, 1.0))[:3]
            t = (M @ np.append(c.tail, 1.0))[:3] if c.tail is not None else None
            out.append((p, c.radius, t))
        return out

    def update(self, W: np.ndarray, dt: float) -> None:
        if not self.joints:
            return
        if not self._ready:
            self.reset(W)
        cols = self._collider_world(W)
        m = self.m
        for j in self.joints:
            n = m.nodes[j.node]
            PW = W[n.parent]
            M = PW @ trs(n.t, j.init_r, n.rest_s)
            pos = M[:3, 3]
            prot = m3_to_q(PW[:3, :3])
            if j.pin is not None:
                nxt = j.pin
            else:
                inertia = (j.cur - j.prev) * (1.0 - j.drag)
                stiff = q_rotate(q_mul(prot, j.init_r), j.axis) * (j.stiff * dt)
                nxt = j.cur + inertia + stiff + j.gdir * (j.grav * dt)
            nxt = pos + normalize(nxt - pos) * j.length
            for ci in self.spring_cols[j.spring]:
                if ci >= len(cols):
                    continue
                cp, cr, ct = cols[ci]
                if ct is not None:  # capsule: closest point on segment
                    seg = ct - cp
                    tt = np.clip(np.dot(nxt - cp, seg) / max(1e-9, np.dot(seg, seg)), 0, 1)
                    cp = cp + seg * tt
                r = cr + j.hit
                d = nxt - cp
                dist = float(np.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]))
                if dist < r and dist > 1e-9:
                    nxt = cp + d / dist * r
                    nxt = pos + normalize(nxt - pos) * j.length
            j.prev = j.cur
            j.cur = nxt
            # rotate the bone so its child points at the new tail
            local_to = np.linalg.inv(M) @ np.append(nxt, 1.0)
            to = normalize(local_to[:3])
            n.r = q_mul(j.init_r, q_from_vectors(j.axis, to))
            W[j.node] = PW @ trs(n.t, n.r, n.rest_s)
            # keep this joint's children in step for the next joint in the chain
            for c in n.children:
                cn = m.nodes[c]
                W[c] = W[j.node] @ trs(cn.t, cn.r, cn.rest_s)

    # ------------------------------------------------------------------ interaction
    def tails_near(self, project, point_xy, radius_px: float) -> list[_J]:
        out = []
        for j in self.joints:
            sx, sy = project(j.cur)
            if (sx - point_xy[0]) ** 2 + (sy - point_xy[1]) ** 2 <= radius_px ** 2:
                out.append(j)
        return out

    def kick(self, joints: list[_J], d: np.ndarray) -> None:
        for j in joints:
            j.cur = j.cur + d   # prev unchanged -> gains velocity
