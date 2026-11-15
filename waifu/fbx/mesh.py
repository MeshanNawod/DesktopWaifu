"""Load the static meshes + textures from a binary FBX into flat numpy arrays (cached as .npz)."""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import CACHE_DIR
from .binary import FbxNode, parse

log = logging.getLogger(__name__)


@dataclass
class StaticMesh:
    pos: np.ndarray            # (N,3) float32, metres, Y up
    nrm: np.ndarray            # (N,3)
    uv: np.ndarray             # (N,2), v flipped to top-left origin
    idx: np.ndarray            # (M,) uint32 triangles
    textures: dict = field(default_factory=dict)   # role -> file path (basecolor, normal, ...)


def _p70(node: FbxNode, name: str, default=None):
    props = node.find("Properties70")
    if props is None:
        return default
    for p in props.children:
        if p.props and p.props[0] == name:
            return p.props[4:] if len(p.props) > 4 else default
    return default


def _euler_xyz(r) -> np.ndarray:
    rx, ry, rz = (math.radians(float(a)) for a in r)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _layer(geo: FbxNode, layer: str, data: str, index: str, n_corners: int, vidx: np.ndarray, width: int):
    el = geo.find(layer)
    if el is None:
        return None
    arr = el.value(data)
    if arr is None:
        return None
    arr = np.asarray(arr, np.float64).reshape(-1, width)
    mapping = el.value("MappingInformationType", "ByPolygonVertex")
    ref = el.value("ReferenceInformationType", "Direct")
    if ref == "IndexToDirect":
        ii = el.value(index)
        if ii is not None:
            arr = arr[np.asarray(ii, np.int64)]
    if mapping in ("ByVertice", "ByVertex"):
        arr = arr[vidx]
    if len(arr) != n_corners:
        return None
    return arr


def load_fbx_mesh(path: str | Path) -> StaticMesh:
    path = Path(path)
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|v2".encode()).hexdigest()[:16]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"fbxmesh_{key}.npz"
    textures = _find_textures(path)
    if cache.exists():
        d = np.load(cache)
        return StaticMesh(d["pos"], d["nrm"], d["uv"], d["idx"], textures)

    root = parse(path)
    objs = root.find("Objects")
    gs = root.find("GlobalSettings")
    unit = float((_p70(gs, "UnitScaleFactor") or [1.0])[0]) * 0.01   # FBX units are cm by default
    models = {o.props[0]: o for o in objs.find_all("Model")}
    links = {}
    for c in root.find("Connections").children:
        if c.props[0] == "OO":
            links.setdefault(c.props[1], []).append(c.props[2])
    all_pos, all_nrm, all_uv, all_idx = [], [], [], []
    base = 0
    for geo in objs.find_all("Geometry"):
        if len(geo.props) < 3 or geo.props[2] != "Mesh":
            continue
        verts = np.asarray(geo.value("Vertices"), np.float64).reshape(-1, 3)
        pvi = np.asarray(geo.value("PolygonVertexIndex"), np.int64)
        ends = pvi < 0
        vidx = np.where(ends, ~pvi, pvi)
        n = len(pvi)
        # triangulate polygons as fans: (first, c, c+1) for every inner corner c
        corner = np.arange(n)
        starts = np.concatenate([[0], np.nonzero(ends)[0][:-1] + 1])
        poly_of = np.cumsum(np.concatenate([[0], ends[:-1].astype(np.int64)]))
        first = starts[poly_of]
        inner = (corner != first) & ~ends
        tri = np.stack([first[inner], corner[inner], corner[inner] + 1], axis=1)

        uv = _layer(geo, "LayerElementUV", "UV", "UVIndex", n, vidx, 2)
        uvi = np.zeros(n, np.int64) if uv is None else np.unique(uv.round(6), axis=0, return_inverse=True)[1].ravel()
        # merge corners sharing the same vertex and UV
        keyc = vidx * (int(uvi.max()) + 1 if uv is not None else 1) + uvi
        uniq, first_corner, inv = np.unique(keyc, return_index=True, return_inverse=True)
        inv = inv.ravel()
        p = verts[vidx[first_corner]]
        u = uv[first_corner] if uv is not None else np.zeros((len(uniq), 2))
        # smooth normals per original vertex (area-weighted), then gathered per merged vertex
        tv = vidx[tri]
        a, b, c = verts[tv[:, 0]], verts[tv[:, 1]], verts[tv[:, 2]]
        fn = np.cross(b - a, c - a)
        vn = np.zeros_like(verts)
        for k in range(3):
            np.add.at(vn, tv[:, k], fn)
        vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
        nrm = vn[vidx[first_corner]]
        # model transform (Lcl Translation / PreRotation / Lcl Rotation / Lcl Scaling)
        owner = next((models[m] for m in links.get(geo.props[0], []) if m in models), None)
        if owner is not None:
            T = np.array(_p70(owner, "Lcl Translation") or [0, 0, 0], float)
            R = _euler_xyz(_p70(owner, "Lcl Rotation") or [0, 0, 0])
            pre = _p70(owner, "PreRotation")
            if pre:
                R = _euler_xyz(pre) @ R
            S = np.array(_p70(owner, "Lcl Scaling") or [1, 1, 1], float)
            p = (p * S) @ R.T + T
            nrm = (nrm / S) @ R.T
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        all_pos.append(p * unit)
        all_nrm.append(nrm)
        all_uv.append(np.column_stack([u[:, 0], 1.0 - u[:, 1]]))
        all_idx.append(inv[tri].ravel() + base)
        base += len(uniq)
    if not all_pos:
        raise ValueError("No mesh geometry found in FBX")
    mesh = StaticMesh(np.concatenate(all_pos).astype(np.float32), np.concatenate(all_nrm).astype(np.float32),
                      np.concatenate(all_uv).astype(np.float32), np.concatenate(all_idx).astype(np.uint32), textures)
    np.savez(cache, pos=mesh.pos, nrm=mesh.nrm, uv=mesh.uv, idx=mesh.idx)
    log.info("FBX %s: %d vertices, %d triangles (cached)", path.name, len(mesh.pos), len(mesh.idx) // 3)
    return mesh


def _find_textures(path: Path) -> dict:
    """basecolor / normal / roughness / metallic images next to the FBX (or in Name.fbm/)."""
    out = {}
    dirs = [path.parent / f"{path.stem}.fbm", path.parent / "textures", path.parent]
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            low = f.name.lower()
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            for role, keys in (("basecolor", ("basecolor", "base_color", "albedo", "diffuse", "_col")),
                               ("normal", ("normal",)), ("roughness", ("rough",)), ("metallic", ("metal",))):
                if role not in out and any(k in low for k in keys):
                    out[role] = f
    return out
