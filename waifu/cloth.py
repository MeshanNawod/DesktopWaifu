"""Procedural cloth for 3D characters (skirts, tunic flaps, hanging straps, jacket hems).

Many models have no physics bones in their clothes, so the vertex shader deforms "loose" cloth:
clothing below the waist that isn't hugging a leg. The amount grows toward the hem. This class is
the physics behind it: a damped 3D spring (in metres, world space) that

  * lags behind when she's dragged / walks / turns (inertia + air drag),
  * lifts a little while she falls or jumps, swings back on landing,
  * is pushed by the breeze and gusts, and ripples (flutter) in the wind,
  * gets flicked when you touch / squeeze / smack her.
"""
from __future__ import annotations

import math

import numpy as np


class ClothSim:
    def __init__(self, height: float, softness: float = 1.0, flutter: float = 0.006):
        self.h = height
        self.soft = max(0.2, softness)
        self.base_flutter = flutter
        self.off = np.zeros(3)
        self.vel = np.zeros(3)
        self.flutter = flutter
        self.t = 0.0

    def kick(self, v) -> None:
        self.vel += np.asarray(v, float) * self.h

    def step(self, dt: float, accel: np.ndarray, velocity: np.ndarray, wind: np.ndarray,
             walk_phase: float | None = None) -> None:
        self.t += dt
        k = 60.0 / self.soft                     # stiffness: softer cloth swings further and slower
        c = 6.5 / math.sqrt(self.soft)           # damping
        target = np.zeros(3)
        if walk_phase is not None:               # legs pushing the hem back and forth while walking
            target[2] = math.sin(walk_phase) * 0.012 * self.h
            target[0] = math.cos(walk_phase * 2) * 0.004 * self.h
        accel = np.clip(accel, -40.0, 40.0)      # m/s^2 in the window frame
        force = (-k * (self.off - target) - c * self.vel
                 - accel * 0.55                 # inertia: lags behind acceleration (lifts while falling)
                 - velocity * 0.9               # air drag: trails behind while moving
                 + wind * 0.35)
        steps = 2
        for _ in range(steps):
            h = dt / steps
            self.vel += force * h
            self.off += self.vel * h
        lim = 0.09 * self.h
        n = float(np.linalg.norm(self.off))
        if n > lim:
            self.off *= lim / n
            self.vel *= 0.5
        self.off[1] = min(max(self.off[1], -0.02 * self.h), 0.045 * self.h)   # never flips up indecently
        gust = float(np.linalg.norm(wind))
        self.flutter = self.base_flutter * (0.6 + min(2.5, gust * 0.8)) * self.h / 1.58
