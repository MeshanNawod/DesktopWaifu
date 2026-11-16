"""VRM model: scene graph, meshes, skins, materials, humanoid, expressions, spring-bone setup."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from .gltf import Gltf
from .math3d import IDENTITY_Q, m3_to_q, q_axis, q_inv, q_mul, trs

log = logging.getLogger(__name__)

VRM0_EXPR = {
    "joy": "happy", "sorrow": "sad", "fun": "relaxed", "angry": "angry", "surprised": "surprised",
    "a": "aa", "i": "ih", "u": "ou", "e": "ee", "o": "oh", "neutral": "neutral",
    "blink": "blink", "blink_l": "blinkLeft", "blink_r": "blinkRight",
    "lookup": "lookUp", "lookdown": "lookDown", "lookleft": "lookLeft", "lookright": "lookRight",
}
R180 = q_axis((0, 1, 0), 180.0)


def _to_srgb(c):
    return [max(0.0, float(v)) ** (1 / 2.2) for v in c]


@dataclass
class Node:
    name: str
    parent: int
    children: list[int]
    rest_t: np.ndarray
    rest_r: np.ndarray
    rest_s: np.ndarray
    mesh: int | None
    skin: int | None
    t: np.ndarray = None
    r: np.ndarray = None

    def local(self) -> np.ndarray:
        return trs(self.t, self.r, self.rest_s)


@dataclass
class Prim:
    mesh: int
    pos: np.ndarray
    nrm: np.ndarray
    uv: np.ndarray
    joints: np.ndarray | None
    weights: np.ndarray | None
    indices: np.ndarray
    material: int
    targets: list = field(default_factory=list)   # [(dpos, dnrm | None)]
    weights_now: dict = field(default_factory=dict)


@dataclass
class Material:
    color: list = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    tex: int | None = None
    shade: list = field(default_factory=lambda: [0.85, 0.85, 0.9])
    shade_tex: int | None = None
    emissive: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    emissive_tex: int | None = None
    alpha_mode: int = 0          # 0 opaque, 1 mask, 2 blend
    cutoff: float = 0.5
    double_sided: bool = False
    unlit: bool = False
    shade_shift: float = 0.0
    shade_toony: float = 0.9
    outline_mode: int = 0        # 0 none, 1 world, 2 screen
    outline_width: float = 0.0
    outline_color: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    zwrite: bool = True
    queue: int = 0
    uv_xf: np.ndarray = field(default_factory=lambda: np.eye(3))
    colorize: list | None = None   # [r, g, b, strength]: recolour the texture (e.g. pink hair)


@dataclass
class Expression:
    binds: list                  # [(mesh, target, weight)]
    binary: bool = False
    override_blink: str = "none"
    override_mouth: str = "none"
    override_look: str = "none"


@dataclass
class SpringJoint:
    node: int
    child: int | None
    stiffness: float
    gravity: float
    gdir: np.ndarray
    drag: float
    hit_radius: float


@dataclass
class Spring:
    joints: list
    colliders: list[int]


@dataclass
class Collider:
    node: int
    offset: np.ndarray
    radius: float
    tail: np.ndarray | None = None   # capsule end


def _uv_xf(texinfo: dict | None, vrm0_st: list | None = None) -> np.ndarray:
    m = np.eye(3)
    if vrm0_st:
        ox, oy, sx, sy = vrm0_st
        m = np.array([[sx, 0, ox], [0, sy, oy], [0, 0, 1.0]])
    tt = (texinfo or {}).get("extensions", {}).get("KHR_texture_transform")
    if tt:
        ox, oy = tt.get("offset", [0, 0])
        sx, sy = tt.get("scale", [1, 1])
        r = tt.get("rotation", 0.0)
        c, s = math.cos(r), math.sin(r)
        T = np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1.0]])
        R = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1.0]])
        S = np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1.0]])
        m = T @ R @ S
    return m


class VrmModel:
    def __init__(self, path, overrides: dict | None = None):
        self.g = g = Gltf(path)
        j = g.j
        ext = j.get("extensions", {})
        if "VRMC_vrm" in ext:
            self.version = 1
        elif "VRM" in ext:
            self.version = 0
        else:
            raise ValueError("Not a VRM file (no VRM / VRMC_vrm extension)")
        self.title = self._title(ext)

        # ---- nodes
        jn = j["nodes"]
        parents = [-1] * len(jn)
        for i, n in enumerate(jn):
            for c in n.get("children", []):
                parents[c] = i
        self.nodes: list[Node] = []
        for i, n in enumerate(jn):
            if "matrix" in n:
                M = np.array(n["matrix"], float).reshape(4, 4).T
                t = M[:3, 3].copy()
                s = np.linalg.norm(M[:3, :3], axis=0)
                r = m3_to_q(M[:3, :3])
            else:
                t = np.array(n.get("translation", [0, 0, 0]), float)
                r = np.array(n.get("rotation", [0, 0, 0, 1]), float)
                s = np.array(n.get("scale", [1, 1, 1]), float)
            self.nodes.append(Node(n.get("name", f"node{i}"), parents[i], list(n.get("children", [])),
                                   t, r, s, n.get("mesh"), n.get("skin"), t.copy(), r.copy()))
        scene = j.get("scenes", [{}])[j.get("scene", 0)] if j.get("scenes") else {}
        self.roots = scene.get("nodes") or [i for i, p in enumerate(parents) if p < 0]
        self.order: list[int] = []
        stack = list(reversed(self.roots))
        while stack:
            i = stack.pop()
            self.order.append(i)
            stack.extend(reversed(self.nodes[i].children))

        # ---- meshes
        self.mesh_prims: dict[int, list[Prim]] = {}
        for mi, m in enumerate(j.get("meshes", [])):
            prims = []
            for p in m["primitives"]:
                if p.get("mode", 4) != 4:
                    continue
                at = p["attributes"]
                pos = g.accessor(at["POSITION"]).astype(np.float32)
                nrm = g.accessor(at["NORMAL"]).astype(np.float32) if "NORMAL" in at else np.tile([0, 1, 0], (len(pos), 1)).astype(np.float32)
                uv = g.accessor(at["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in at else np.zeros((len(pos), 2), np.float32)
                joints = g.accessor(at["JOINTS_0"]).astype(np.float32) if "JOINTS_0" in at else None
                weights = g.accessor(at["WEIGHTS_0"]).astype(np.float32) if "WEIGHTS_0" in at else None
                if weights is not None:
                    s = weights.sum(axis=1, keepdims=True)
                    weights = np.where(s > 0, weights / np.maximum(s, 1e-8), weights)
                idx = (g.accessor(p["indices"]).ravel().astype(np.uint32) if "indices" in p
                       else np.arange(len(pos), dtype=np.uint32))
                targets = []
                for tg in p.get("targets", []):
                    dpos = g.accessor(tg["POSITION"]).astype(np.float32) if "POSITION" in tg else None
                    dnrm = g.accessor(tg["NORMAL"]).astype(np.float32) if "NORMAL" in tg else None
                    targets.append((dpos, dnrm))
                prims.append(Prim(mi, pos, nrm, uv, joints, weights, idx, p.get("material", -1), targets))
            self.mesh_prims[mi] = prims
        self.draw_nodes = [i for i in self.order if self.nodes[i].mesh is not None]

        # ---- skins
        self.skins = []
        for s in j.get("skins", []):
            n = len(s["joints"])
            if "inverseBindMatrices" in s:
                ibm = g.accessor(s["inverseBindMatrices"]).reshape(n, 4, 4).transpose(0, 2, 1).astype(float)
            else:
                ibm = np.tile(np.eye(4), (n, 1, 1))
            self.skins.append({"joints": np.array(s["joints"]), "ibm": ibm})

        # ---- textures / materials
        self.textures = j.get("textures", [])
        self.materials = [self._material(i, m, ext) for i, m in enumerate(j.get("materials", []))]
        # optional sidecar "<model>.json": parts to hide (e.g. a backpack you don't want)
        self.options = {}
        side = self.g.path.with_suffix(".json")
        if side.exists():
            try:
                import json as _json
                self.options = _json.loads(side.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                log.warning("Bad model options file %s", side)
        if overrides:                      # a character variant (e.g. Seed-chan) layered on top
            for k, v in overrides.items():
                if k.startswith("hide_") and isinstance(v, list):
                    self.options[k] = list(self.options.get(k, [])) + v
                else:
                    self.options[k] = v
        hide_mat = set(self.options.get("hide_materials", []))
        hide_mesh = set(self.options.get("hide_meshes", []))
        mat_names = [m.get("name", "") for m in j.get("materials", [])]
        for mi, prims in self.mesh_prims.items():
            mesh_name = j["meshes"][mi].get("name", "")
            self.mesh_prims[mi] = [p for p in prims if mesh_name not in hide_mesh
                                   and not (0 <= p.material < len(mat_names) and mat_names[p.material] in hide_mat)]
        for name, col in self.options.get("colorize", {}).items():
            for i, mn in enumerate(mat_names):
                if mn == name and i < len(self.materials):
                    self.materials[i].colorize = list(col)
        if not self.materials:
            self.materials = [Material()]

        # ---- VRM bits
        self.humanoid = self._humanoid(ext)
        for bone, sc in (overrides or {}).get("bone_scale", {}).items():   # e.g. a bigger, cuter head
            ni = self.humanoid.get(bone)
            if ni is not None:
                self.nodes[ni].rest_s = self.nodes[ni].rest_s * float(sc)
        self.expressions = self._expressions(ext, jn)
        self.colliders, self.springs = self._springs(ext)
        self.look_type = self._look_type(ext)

        # rest-pose world matrices (model space, no root transform)
        self.rest_world = self.world_matrices(np.eye(4), rest=True)
        self.rest_world_rot = {i: m3_to_q(self.rest_world[i][:3, :3]) for i in range(len(self.nodes))}

        # bounds from rest vertices (VRM meshes are in bind pose)
        ys = [p.pos[:, 1] for prims in self.mesh_prims.values() for p in prims]
        allp = np.concatenate([p.pos for prims in self.mesh_prims.values() for p in prims])
        self.min_y = float(min(y.min() for y in ys))
        self.max_y = float(max(y.max() for y in ys))
        self.height = self.max_y - self.min_y
        hips = self.humanoid.get("hips")
        self.center_x = (float(self.rest_world[hips][0, 3]) if hips is not None
                         else float((allp[:, 0].min() + allp[:, 0].max()) / 2))

    # ------------------------------------------------------------------ parse helpers
    def _title(self, ext) -> str:
        if self.version == 1:
            return ext["VRMC_vrm"].get("meta", {}).get("name", "VRM")
        return ext["VRM"].get("meta", {}).get("title", "VRM")

    def _material(self, i: int, m: dict, ext: dict) -> Material:
        pbr = m.get("pbrMetallicRoughness", {})
        mat = Material()
        mat.color = list(pbr.get("baseColorFactor", [1, 1, 1, 1]))
        mat.color = _to_srgb(mat.color[:3]) + [mat.color[3]]
        bt = pbr.get("baseColorTexture")
        mat.tex = bt["index"] if bt else None
        mat.uv_xf = _uv_xf(bt)
        mat.alpha_mode = {"OPAQUE": 0, "MASK": 1, "BLEND": 2}.get(m.get("alphaMode", "OPAQUE"), 0)
        mat.cutoff = m.get("alphaCutoff", 0.5)
        mat.double_sided = m.get("doubleSided", False)
        mat.emissive = _to_srgb(m.get("emissiveFactor", [0, 0, 0]))
        et = m.get("emissiveTexture")
        mat.emissive_tex = et["index"] if et else None
        mext = m.get("extensions", {})
        mat.unlit = "KHR_materials_unlit" in mext
        mt = mext.get("VRMC_materials_mtoon")
        if mt:
            mat.unlit = False
            mat.shade = _to_srgb(mt.get("shadeColorFactor", [1, 1, 1]))
            st = mt.get("shadeMultiplyTexture")
            mat.shade_tex = st["index"] if st else None
            mat.shade_shift = mt.get("shadingShiftFactor", 0.0)
            mat.shade_toony = mt.get("shadingToonyFactor", 0.9)
            mat.outline_mode = {"none": 0, "worldCoordinates": 1, "screenCoordinates": 2}.get(
                mt.get("outlineWidthMode", "none"), 0)
            mat.outline_width = mt.get("outlineWidthFactor", 0.0)
            mat.outline_color = _to_srgb(mt.get("outlineColorFactor", [0, 0, 0]))
            mat.zwrite = mt.get("transparentWithZWrite", False) or mat.alpha_mode != 2
            mat.queue = mt.get("renderQueueOffsetNumber", 0)
        if self.version == 0:
            props = ext["VRM"].get("materialProperties", [])
            mp = props[i] if i < len(props) else None
            if mp and mp.get("shader") == "VRM/MToon":
                fp, vp, tp = mp.get("floatProperties", {}), mp.get("vectorProperties", {}), mp.get("textureProperties", {})
                mat.unlit = False
                mat.color = list(vp.get("_Color", [1, 1, 1, 1]))
                mat.shade = list(vp.get("_ShadeColor", [0.97, 0.81, 0.86, 1]))[:3]
                mat.tex = tp.get("_MainTex", mat.tex)
                mat.shade_tex = tp.get("_ShadeTexture")
                mat.emissive = list(vp.get("_EmissionColor", [0, 0, 0, 1]))[:3]
                mat.emissive_tex = tp.get("_EmissionMap")
                mat.shade_shift = fp.get("_ShadeShift", 0.0)
                mat.shade_toony = fp.get("_ShadeToony", 0.9)
                mat.cutoff = fp.get("_Cutoff", 0.5)
                blend = int(fp.get("_BlendMode", 0))
                mat.alpha_mode = {0: 0, 1: 1, 2: 2, 3: 2}.get(blend, 0)
                mat.zwrite = blend != 2
                mat.double_sided = int(fp.get("_CullMode", 2)) == 0
                mat.outline_mode = int(fp.get("_OutlineWidthMode", 0))
                mat.outline_width = fp.get("_OutlineWidth", 0.0) * 0.01
                mat.outline_color = list(vp.get("_OutlineColor", [0, 0, 0, 1]))[:3]
                mat.uv_xf = _uv_xf(None, vp.get("_MainTex"))
                mat.queue = int(mp.get("renderQueue", 2000)) - 2000
        return mat

    def _humanoid(self, ext) -> dict[str, int]:
        if self.version == 1:
            hb = ext["VRMC_vrm"].get("humanoid", {}).get("humanBones", {})
            return {k: v["node"] for k, v in hb.items() if "node" in v}
        return {b["bone"]: b["node"] for b in ext["VRM"].get("humanoid", {}).get("humanBones", []) if "node" in b}

    def _expressions(self, ext, jn) -> dict[str, Expression]:
        out: dict[str, Expression] = {}
        if self.version == 1:
            ex = ext["VRMC_vrm"].get("expressions", {})
            for group in ("preset", "custom"):
                for name, e in ex.get(group, {}).items():
                    binds = []
                    for b in e.get("morphTargetBinds", []):
                        mesh = jn[b["node"]].get("mesh")
                        if mesh is not None:
                            binds.append((mesh, b["index"], b.get("weight", 1.0)))
                    out[name] = Expression(binds, e.get("isBinary", False), e.get("overrideBlink", "none"),
                                           e.get("overrideMouth", "none"), e.get("overrideLookAt", "none"))
        else:
            groups = ext["VRM"].get("blendShapeMaster", {}).get("blendShapeGroups", [])
            for gr in groups:
                preset = gr.get("presetName", "unknown").lower()
                name = VRM0_EXPR.get(preset) or gr.get("name", preset)
                binds = [(b["mesh"], b["index"], b.get("weight", 100) / 100.0) for b in gr.get("binds", [])]
                over = "block" if name in ("happy", "angry", "sad", "relaxed", "surprised") else "none"
                out[name] = Expression(binds, gr.get("isBinary", False), over)
        return out

    def _springs(self, ext):
        colliders: list[Collider] = []
        springs: list[Spring] = []
        if self.version == 1:
            sb = ext.get("VRMC_springBone", {})
            for c in sb.get("colliders", []):
                shape = c.get("shape", {})
                if "sphere" in shape:
                    s = shape["sphere"]
                    colliders.append(Collider(c["node"], np.array(s.get("offset", [0, 0, 0]), float), s.get("radius", 0.0)))
                elif "capsule" in shape:
                    s = shape["capsule"]
                    colliders.append(Collider(c["node"], np.array(s.get("offset", [0, 0, 0]), float), s.get("radius", 0.0),
                                              np.array(s.get("tail", [0, 0, 0]), float)))
            groups = [g.get("colliders", []) for g in sb.get("colliderGroups", [])]
            for s in sb.get("springs", []):
                js = s.get("joints", [])
                joints = []
                for k, jd in enumerate(js):
                    if k == len(js) - 1 and len(js) > 1:
                        break  # last joint is the tail
                    child = js[k + 1]["node"] if k + 1 < len(js) else None
                    joints.append(SpringJoint(jd["node"], child, jd.get("stiffness", 1.0), jd.get("gravityPower", 0.0),
                                              np.array(jd.get("gravityDir", [0, -1, 0]), float),
                                              jd.get("dragForce", 0.5), jd.get("hitRadius", 0.0)))
                cols = [ci for gi in s.get("colliderGroups", []) if gi < len(groups) for ci in groups[gi]]
                if joints:
                    springs.append(Spring(joints, cols))
        else:
            sa = ext["VRM"].get("secondaryAnimation", {})
            group_cols: list[list[int]] = []
            for cg in sa.get("colliderGroups", []):
                ids = []
                for c in cg.get("colliders", []):
                    o = c.get("offset", {})
                    colliders.append(Collider(cg["node"], np.array([o.get("x", 0), o.get("y", 0), -o.get("z", 0)], float),
                                              c.get("radius", 0.0)))
                    ids.append(len(colliders) - 1)
                group_cols.append(ids)
            for bg in sa.get("boneGroups", []):
                gd = bg.get("gravityDir", {"x": 0, "y": -1, "z": 0})
                params = dict(stiffness=bg.get("stiffiness", 1.0), gravity=bg.get("gravityPower", 0.0),
                              gdir=np.array([gd.get("x", 0), gd.get("y", -1), -gd.get("z", 0)], float),
                              drag=bg.get("dragForce", 0.4), hit_radius=bg.get("hitRadius", 0.02))
                cols = [ci for gi in bg.get("colliderGroups", []) if gi < len(group_cols) for ci in group_cols[gi]]
                for root in bg.get("bones", []):
                    joints = []
                    stack = [root]
                    while stack:
                        n = stack.pop()
                        ch = self.nodes[n].children
                        joints.append(SpringJoint(n, ch[0] if ch else None, **params))
                        stack.extend(reversed(ch))
                    springs.append(Spring(joints, cols))
        return colliders, springs

    def _look_type(self, ext) -> str:
        if self.version == 1:
            return ext["VRMC_vrm"].get("lookAt", {}).get("type", "bone")
        t = ext["VRM"].get("firstPerson", {}).get("lookAtTypeName", "Bone")
        return "expression" if t == "BlendShape" else "bone"

    # ------------------------------------------------------------------ posing
    def reset_pose(self) -> None:
        for n in self.nodes:
            n.t = n.rest_t.copy()
            n.r = n.rest_r.copy()

    def set_bone(self, bone: str, q_normalized: np.ndarray) -> None:
        """Rotate a humanoid bone by a rotation expressed in the VRM 1.0 canonical frame
        (model faces +Z, Y up, character's left = +X), relative to the T-pose."""
        i = self.humanoid.get(bone)
        if i is None:
            return
        q = q_normalized
        if self.version == 0:   # VRM0 models face -Z
            q = q_mul(q_mul(R180, q), q_inv(R180))
        n = self.nodes[i]
        p = self.rest_world_rot[n.parent] if n.parent >= 0 else IDENTITY_Q
        n.r = q_mul(q_mul(q_mul(q_inv(p), q), p), n.rest_r)

    def offset_bone(self, bone: str, d: np.ndarray) -> None:
        """Translate a humanoid bone (e.g. hips) by d in model space."""
        i = self.humanoid.get(bone)
        if i is None:
            return
        n = self.nodes[i]
        if self.version == 0:
            d = np.array([-d[0], d[1], -d[2]])
        p = self.rest_world_rot[n.parent] if n.parent >= 0 else IDENTITY_Q
        from .math3d import q_rotate
        n.t = n.rest_t + q_rotate(q_inv(p), d)

    def world_matrices(self, root: np.ndarray, rest: bool = False) -> np.ndarray:
        W = np.zeros((len(self.nodes), 4, 4))
        for i in self.order:
            n = self.nodes[i]
            L = trs(n.rest_t, n.rest_r, n.rest_s) if rest else trs(n.t, n.r, n.rest_s)
            W[i] = (W[n.parent] if n.parent >= 0 else root) @ L
        return W

    def update_subtree(self, W: np.ndarray, i: int) -> None:
        stack = [i]
        while stack:
            k = stack.pop()
            n = self.nodes[k]
            W[k] = W[n.parent] @ trs(n.t, n.r, n.rest_s)
            stack.extend(n.children)

    def bone_pos(self, W: np.ndarray, bone: str) -> np.ndarray | None:
        i = self.humanoid.get(bone)
        return None if i is None else W[i][:3, 3].copy()

    def descendants(self, i: int) -> set[int]:
        out, stack = set(), [i]
        while stack:
            k = stack.pop()
            out.add(k)
            stack.extend(self.nodes[k].children)
        return out
