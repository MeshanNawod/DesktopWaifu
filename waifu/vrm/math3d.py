"""Small 3D math helpers. Quaternions are (x, y, z, w) like glTF; matrices are 4x4 numpy, column vectors."""
from __future__ import annotations

import math

import numpy as np

IDENTITY_Q = np.array([0.0, 0.0, 0.0, 1.0])


def q_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = float(a[0]), float(a[1]), float(a[2]), float(a[3])
    bx, by, bz, bw = float(b[0]), float(b[1]), float(b[2]), float(b[3])
    return np.array([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ])


def q_inv(q: np.ndarray) -> np.ndarray:
    n = float(np.dot(q, q)) or 1.0
    return np.array([-q[0], -q[1], -q[2], q[3]]) / n


def q_norm(q: np.ndarray) -> np.ndarray:
    n = math.sqrt(float(np.dot(q, q)))
    return q / n if n > 1e-12 else IDENTITY_Q.copy()


def q_axis(axis, deg: float) -> np.ndarray:
    a = np.asarray(axis, float)
    a = a / (np.linalg.norm(a) or 1.0)
    h = math.radians(deg) / 2
    s = math.sin(h)
    return np.array([a[0] * s, a[1] * s, a[2] * s, math.cos(h)])


def q_euler(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> np.ndarray:
    """Degrees; applied as Y * X * Z (yaw, then pitch, then roll in local)."""
    return q_mul(q_mul(q_axis((0, 1, 0), y), q_axis((1, 0, 0), x)), q_axis((0, 0, 1), z))


def q_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    qx, qy, qz, qw = float(q[0]), float(q[1]), float(q[2]), float(q[3])
    vx, vy, vz = float(v[0]), float(v[1]), float(v[2])
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return np.array([vx + qw * tx + (qy * tz - qz * ty),
                     vy + qw * ty + (qz * tx - qx * tz),
                     vz + qw * tz + (qx * ty - qy * tx)])


def q_from_vectors(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az = float(a[0]), float(a[1]), float(a[2])
    bx, by, bz = float(b[0]), float(b[1]), float(b[2])
    la = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
    lb = math.sqrt(bx * bx + by * by + bz * bz) or 1.0
    ax, ay, az, bx, by, bz = ax / la, ay / la, az / la, bx / lb, by / lb, bz / lb
    d = ax * bx + ay * by + az * bz
    if d < -0.999999:
        axis = np.cross((1.0, 0.0, 0.0), (ax, ay, az))
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross((0.0, 1.0, 0.0), (ax, ay, az))
        return q_axis(axis, 180.0)
    cx, cy, cz, cw = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx, 1.0 + d
    n = math.sqrt(cx * cx + cy * cy + cz * cz + cw * cw)
    return np.array([cx / n, cy / n, cz / n, cw / n])


def q_slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        return q_norm(a + (b - a) * t)
    th = math.acos(max(-1.0, min(1.0, d)))
    s = math.sin(th)
    return (a * math.sin((1 - t) * th) + b * math.sin(t * th)) / s


def q_to_m3(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def m3_to_q(m: np.ndarray) -> np.ndarray:
    m = m / np.linalg.norm(m, axis=0, keepdims=True).clip(1e-9)  # strip scale
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        return q_norm(np.array([(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]))
    if m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        return q_norm(np.array([0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]))
    if m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        return q_norm(np.array([(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]))
    s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
    return q_norm(np.array([(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]))


def trs(t, r, s) -> np.ndarray:
    x, y, z, w = float(r[0]), float(r[1]), float(r[2]), float(r[3])
    sx, sy, sz = float(s[0]), float(s[1]), float(s[2])
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array([
        [(1 - 2 * (yy + zz)) * sx, 2 * (xy - wz) * sy, 2 * (xz + wy) * sz, float(t[0])],
        [2 * (xy + wz) * sx, (1 - 2 * (xx + zz)) * sy, 2 * (yz - wx) * sz, float(t[1])],
        [2 * (xz - wy) * sx, 2 * (yz + wx) * sy, (1 - 2 * (xx + yy)) * sz, float(t[2])],
        [0.0, 0.0, 0.0, 1.0],
    ])


def translate(x: float, y: float, z: float) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def rot_m4(q: np.ndarray) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = q_to_m3(q)
    return m


def ortho(l: float, r: float, b: float, t: float, n: float, f: float) -> np.ndarray:
    m = np.eye(4)
    m[0, 0] = 2 / (r - l)
    m[1, 1] = 2 / (t - b)
    m[2, 2] = -2 / (f - n)
    m[0, 3] = -(r + l) / (r - l)
    m[1, 3] = -(t + b) / (t - b)
    m[2, 3] = -(f + n) / (f - n)
    return m


def normalize(v: np.ndarray) -> np.ndarray:
    n = math.sqrt(float(v[0]) ** 2 + float(v[1]) ** 2 + float(v[2]) ** 2)
    return v / n if n > 1e-12 else v
