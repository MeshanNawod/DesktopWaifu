"""Offscreen OpenGL 3.3 renderer for VRM models (GPU skinning, MToon-like toon shading, outlines).

Renders into a multisampled framebuffer owned by a hidden Qt context and returns a
premultiplied QImage, which the transparent desktop window then shows. Keeping the
window a plain layered window means click-through on empty pixels still works.
"""
from __future__ import annotations

import ctypes
import logging

import OpenGL

OpenGL.ERROR_CHECKING = False
OpenGL.ERROR_LOGGING = False
from OpenGL import GL as gl  # noqa: E402

import numpy as np  # noqa: E402
from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QImage, QOffscreenSurface, QOpenGLContext, QSurfaceFormat  # noqa: E402
from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat  # noqa: E402

from .model import VrmModel  # noqa: E402

log = logging.getLogger(__name__)

VS = """
#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNrm;
layout(location=2) in vec2 aUV;
layout(location=3) in vec4 aJoints;
layout(location=4) in vec4 aWeights;
uniform sampler2D uBones;
uniform mat4 uViewProj;
uniform mat4 uModel;
uniform int uSkinned;
uniform int uOutlineMode;
uniform float uOutlineWidth;
uniform float uAspect;
// procedural soft-body deformation for static meshes
uniform int uDeform;
uniform float uNeck;
uniform float uHeight;
uniform vec2 uPivot;
uniform mat3 uHeadRot;
uniform vec2 uBend;
uniform float uSquash;
uniform float uBreath;
uniform int uArmOn;
// squeeze: a soft squish around the point you're pressing (world space)
uniform vec3 uSqzCenter;
uniform float uSqzRadius;
uniform float uSqzAmount;
// procedural cloth: loose clothing below the waist that isn't hugging a leg swings and flutters
uniform int uClothMat;          // this material is clothing
uniform int uClothOn;
uniform float uHipY;
uniform float uClothLen;
uniform vec3 uLegL0; uniform vec3 uLegL1; uniform vec3 uLegR0; uniform vec3 uLegR1;
uniform vec3 uClothOff;         // spring offset (world): lag / swing
uniform float uFlutter;         // breeze ripple strength
uniform float uTime;
uniform float uLegRadius;
float segDist(vec3 p, vec3 a, vec3 b) {
    vec3 ab = b - a;
    float t = clamp(dot(p - a, ab) / max(dot(ab, ab), 1e-6), 0.0, 1.0);
    return length(p - (a + ab * t));
}
uniform float uArmY;
uniform float uShoulderX;
uniform vec2 uArmAng;
out vec3 vNrm;
out vec2 vUV;
mat4 bone(float f) {
    int i = int(f + 0.5);
    return mat4(texelFetch(uBones, ivec2(0, i), 0), texelFetch(uBones, ivec2(1, i), 0),
                texelFetch(uBones, ivec2(2, i), 0), texelFetch(uBones, ivec2(3, i), 0));
}
void main() {
    mat4 m = uModel;
    if (uSkinned == 1) {
        m = uModel * (aWeights.x * bone(aJoints.x) + aWeights.y * bone(aJoints.y) +
                      aWeights.z * bone(aJoints.z) + aWeights.w * bone(aJoints.w));
    }
    vec3 pos = aPos;
    vec3 nrm = aNrm;
    if (uDeform == 1) {
        if (uArmOn == 1) {   // procedural shoulder joints for T-pose meshes
            float side = pos.x >= 0.0 ? 1.0 : -1.0;
            float aw = smoothstep(uShoulderX - 0.02 * uHeight, uShoulderX + 0.035 * uHeight, abs(pos.x))
                     * (1.0 - smoothstep(0.055 * uHeight, 0.085 * uHeight, abs(pos.y - uArmY)));
            float a = -side * (side > 0.0 ? uArmAng.x : uArmAng.y) * aw;
            vec2 pv = vec2(side * uShoulderX, uArmY);
            vec2 d = pos.xy - pv;
            float ca = cos(a), sa = sin(a);
            pos.xy = pv + vec2(ca * d.x - sa * d.y, sa * d.x + ca * d.y);
            nrm.xy = vec2(ca * nrm.x - sa * nrm.y, sa * nrm.x + ca * nrm.y);
        }
        float h = clamp(pos.y / uHeight, 0.0, 1.0);
        // head: rotate everything above the neck around the neck pivot (soft blend)
        float w = smoothstep(uNeck / uHeight - 0.025, uNeck / uHeight + 0.02, h);
        vec3 piv = vec3(uPivot.x, uNeck, uPivot.y);
        pos = mix(pos, piv + uHeadRot * (pos - piv), w);
        nrm = normalize(mix(nrm, uHeadRot * nrm, w));
        // breathing: chest band expands a little
        float chest = exp(-pow((h - 0.70) / 0.07, 2.0));
        pos.xz = pos.xz + (pos.xz - uPivot) * uBreath * chest;
        pos.y += uBreath * 0.4 * uHeight * smoothstep(0.55, 0.8, h);
        // whole-body spring bend (grows with height^2) and squash
        pos.x += uBend.x * h * h * uHeight;
        pos.z += uBend.y * h * h * uHeight;
        pos.y -= uSquash * h * uHeight;
        pos.xz += (pos.xz - uPivot) * uSquash * 0.5 * (1.0 - h);
    }
    vec4 wp = m * vec4(pos, 1.0);
    vec3 n = normalize(mat3(m) * nrm);
    if (uClothOn == 1 && uClothMat == 1) {
        float below = clamp((uHipY - wp.y) / max(uClothLen, 1e-3), 0.0, 1.2);
        float legd = min(segDist(wp.xyz, uLegL0, uLegL1), segDist(wp.xyz, uLegR0, uLegR1));
        float loose = smoothstep(uLegRadius, uLegRadius * 1.6, legd);
        float w = below * below * loose;
        if (w > 0.0) {
            vec3 rip = vec3(sin(uTime * 6.0 + wp.y * 22.0 + wp.x * 14.0),
                            0.0,
                            cos(uTime * 5.0 + wp.y * 18.0 - wp.x * 11.0)) * uFlutter;
            wp.xyz += (uClothOff + rip) * w;
        }
    }
    if (abs(uSqzAmount) > 0.001) {
        vec2 d = wp.xy - uSqzCenter.xy;
        float f = (1.0 - smoothstep(0.0, uSqzRadius, length(d))) * uSqzAmount;
        wp.y -= d.y * f * 0.75;        // squash toward the finger...
        wp.x += d.x * f * 0.32;        // ...and puff out sideways (negative amount = springy bulge)
        wp.z -= f * uSqzRadius * 0.3;
    }
    if (uOutlineMode == 1) wp.xyz += n * uOutlineWidth;
    gl_Position = uViewProj * wp;
    if (uOutlineMode == 2) {
        vec2 cn = normalize((uViewProj * vec4(n, 0.0)).xy + 1e-6);
        gl_Position.xy += vec2(cn.x / uAspect, cn.y) * uOutlineWidth * 2.0 * gl_Position.w;
    }
    vNrm = n;
    vUV = aUV;
}
"""

FS = """
#version 330 core
in vec3 vNrm;
in vec2 vUV;
uniform sampler2D uTex;
uniform sampler2D uShadeTex;
uniform sampler2D uEmTex;
uniform int uHasTex;
uniform int uHasShadeTex;
uniform int uHasEmTex;
uniform vec4 uColor;
uniform vec3 uShade;
uniform vec3 uEmissive;
uniform int uAlphaMode;
uniform float uCutoff;
uniform int uUnlit;
uniform float uShadeShift;
uniform float uShadeToony;
uniform vec3 uLightDir;
uniform int uOutlinePass;
uniform vec3 uOutlineColor;
uniform mat3 uUVXf;
uniform float uRealism;
uniform vec4 uColorize;
uniform float uColorLift;
out vec4 frag;
vec3 recolor(vec3 c) {
    if (uColorize.a <= 0.0) return c;
    float l = dot(c, vec3(0.299, 0.587, 0.114));
    // keep the texture's shading but move it to the target colour; uColorLift brightens dark textures
    vec3 tinted = clamp(uColorize.rgb * mix(uColorLift, 1.0, l), 0.0, 1.0);
    return mix(c, tinted, uColorize.a);
}
void main() {
    vec2 uv = (uUVXf * vec3(vUV, 1.0)).xy;
    vec4 tex = uHasTex == 1 ? texture(uTex, uv) : vec4(1.0);
    tex.rgb = recolor(tex.rgb);
    vec4 base = uColor * tex;
    if (uAlphaMode == 1 && base.a < uCutoff) discard;
    if (uAlphaMode == 0) base.a = 1.0;
    if (uAlphaMode == 1) base.a = 1.0;
    if (uOutlinePass == 1) {
        frag = vec4(uOutlineColor * base.a, base.a);
        return;
    }
    vec3 col;
    if (uUnlit == 1) {
        col = base.rgb;
    } else {
        vec3 n = normalize(vNrm) * (gl_FrontFacing ? 1.0 : -1.0);
        float nl = dot(n, uLightDir) + uShadeShift;
        float t = clamp(uShadeToony, 0.0, 0.999);
        float shading = clamp((nl - (-1.0 + t)) / max(1e-3, (1.0 - t) - (-1.0 + t)), 0.0, 1.0);
        vec3 shadeTex = uHasShadeTex == 1 ? recolor(texture(uShadeTex, uv).rgb) : tex.rgb;
        vec3 shade = uShade * shadeTex;
        col = mix(shade, base.rgb, shading);
        col = mix(col, base.rgb, 0.12);           // a little ambient light
        if (uRealism > 0.0) {
            // softer, more natural light: wrapped diffuse, sky/ground ambient, rim light, gentle specular
            float wrap = clamp((dot(n, uLightDir) + 0.4) / 1.4, 0.0, 1.0);
            vec3 soft = mix(shade * 0.92, base.rgb, wrap * wrap * (3.0 - 2.0 * wrap));
            soft *= mix(vec3(0.86, 0.84, 0.93), vec3(1.06, 1.04, 1.0), n.y * 0.5 + 0.5);
            float rim = pow(1.0 - clamp(n.z, 0.0, 1.0), 3.0);
            vec3 hv = normalize(uLightDir + vec3(0.0, 0.0, 1.0));
            float spec = pow(max(dot(n, hv), 0.0), 40.0) * 0.16;
            col = mix(col, soft, 0.65 * uRealism) + base.rgb * rim * 0.3 * uRealism + vec3(spec) * uRealism;
        }
    }
    vec3 em = uEmissive * (uHasEmTex == 1 ? texture(uEmTex, uv).rgb : vec3(1.0));
    col = clamp(col + em, 0.0, 1.0);
    frag = vec4(col * base.a, base.a);            // premultiplied
}
"""


def _compile(src: str, kind) -> int:
    s = gl.glCreateShader(kind)
    gl.glShaderSource(s, src)
    gl.glCompileShader(s)
    if not gl.glGetShaderiv(s, gl.GL_COMPILE_STATUS):
        raise RuntimeError(gl.glGetShaderInfoLog(s).decode(errors="replace"))
    return s


class _GpuPrim:
    __slots__ = ("vao", "vbo_pos", "vbo_nrm", "ebo", "count", "prim", "dirty_key")


class VrmRenderer:
    def __init__(self, model: VrmModel, width: int, height: int, realism: float = 0.0):
        self.m = model
        self.realism = realism
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setAlphaBufferSize(8)
        self.ctx = QOpenGLContext()
        self.ctx.setFormat(fmt)
        if not self.ctx.create():
            raise RuntimeError("Could not create an OpenGL context")
        self.surface = QOffscreenSurface()
        self.surface.setFormat(self.ctx.format())
        self.surface.create()
        if not self.ctx.makeCurrent(self.surface):
            raise RuntimeError("Could not activate the OpenGL context")
        v = self.ctx.format()
        if (v.majorVersion(), v.minorVersion()) < (3, 3):
            raise RuntimeError(f"OpenGL 3.3 required, got {v.majorVersion()}.{v.minorVersion()}")

        prog = gl.glCreateProgram()
        gl.glAttachShader(prog, _compile(VS, gl.GL_VERTEX_SHADER))
        gl.glAttachShader(prog, _compile(FS, gl.GL_FRAGMENT_SHADER))
        gl.glLinkProgram(prog)
        if not gl.glGetProgramiv(prog, gl.GL_LINK_STATUS):
            raise RuntimeError(gl.glGetProgramInfoLog(prog).decode(errors="replace"))
        self.prog = prog
        self._loc: dict[str, int] = {}

        self._textures: dict[int, int] = {}
        self._white = self._make_tex(np.full((1, 1, 4), 255, np.uint8))
        self._bone_tex = gl.glGenTextures(1)
        self._gpu: dict[int, list[_GpuPrim]] = {}
        for mi, prims in model.mesh_prims.items():
            self._gpu[mi] = [self._upload(p) for p in prims]
        self.fbo = None
        self.resize(width, height)
        self.ctx.doneCurrent()

    # ------------------------------------------------------------------ resources
    def loc(self, name: str) -> int:
        if name not in self._loc:
            self._loc[name] = gl.glGetUniformLocation(self.prog, name)
        return self._loc[name]

    def resize(self, w: int, h: int) -> None:
        self.w, self.h = max(8, w), max(8, h)
        f = QOpenGLFramebufferObjectFormat()
        f.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        f.setSamples(8 if getattr(self, "realism", 0) > 0 else 4)
        self.fbo = QOpenGLFramebufferObject(QSize(self.w, self.h), f)

    def _make_tex(self, rgba: np.ndarray, sampler: dict | None = None) -> int:
        t = gl.glGenTextures(1)
        gl.glActiveTexture(gl.GL_TEXTURE7)   # scratch unit: never clobber the bone texture on unit 2
        gl.glBindTexture(gl.GL_TEXTURE_2D, t)
        h, w = rgba.shape[:2]
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, w, h, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, rgba)
        gl.glGenerateMipmap(gl.GL_TEXTURE_2D)
        s = sampler or {}
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, s.get("minFilter", gl.GL_LINEAR_MIPMAP_LINEAR))
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, s.get("magFilter", gl.GL_LINEAR))
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, s.get("wrapS", gl.GL_REPEAT))
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, s.get("wrapT", gl.GL_REPEAT))
        return t

    def texture(self, tex_index: int | None) -> int | None:
        if tex_index is None or tex_index < 0 or tex_index >= len(self.m.textures):
            return None
        if tex_index in self._textures:
            return self._textures[tex_index]
        t = self.m.textures[tex_index]
        src = t.get("source")
        if src is None:
            src = next(iter(t.get("extensions", {}).values()), {}).get("source")
        tid = None
        if src is not None:
            img = QImage.fromData(self.m.g.image_bytes(src))
            if not img.isNull():
                img = img.convertToFormat(QImage.Format.Format_RGBA8888)
                buf = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes())
                arr = buf.reshape(img.height(), img.bytesPerLine())[:, : img.width() * 4]
                arr = np.ascontiguousarray(arr.reshape(img.height(), img.width(), 4))
                samplers = self.m.g.j.get("samplers", [])
                si = t.get("sampler")
                tid = self._make_tex(arr, samplers[si] if si is not None and si < len(samplers) else None)
        self._textures[tex_index] = tid
        return tid

    def _vbo(self, data: np.ndarray, loc: int, dynamic: bool = False) -> int:
        b = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, b)
        data = np.ascontiguousarray(data, np.float32)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, data.nbytes, data,
                        gl.GL_DYNAMIC_DRAW if dynamic else gl.GL_STATIC_DRAW)
        gl.glEnableVertexAttribArray(loc)
        gl.glVertexAttribPointer(loc, data.shape[1], gl.GL_FLOAT, gl.GL_FALSE, 0, ctypes.c_void_p(0))
        return b

    def _upload(self, p) -> _GpuPrim:
        g = _GpuPrim()
        g.prim = p
        g.vao = gl.glGenVertexArrays(1)
        gl.glBindVertexArray(g.vao)
        dyn = bool(p.targets)
        g.vbo_pos = self._vbo(p.pos, 0, dyn)
        g.vbo_nrm = self._vbo(p.nrm, 1, dyn)
        self._vbo(p.uv, 2)
        if p.joints is not None and p.weights is not None:
            self._vbo(p.joints, 3)
            self._vbo(p.weights, 4)
        else:
            gl.glDisableVertexAttribArray(3)
            gl.glDisableVertexAttribArray(4)
        g.ebo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, g.ebo)
        gl.glBufferData(gl.GL_ELEMENT_ARRAY_BUFFER, p.indices.nbytes, p.indices, gl.GL_STATIC_DRAW)
        g.count = len(p.indices)
        g.dirty_key = None
        gl.glBindVertexArray(0)
        return g

    # ------------------------------------------------------------------ frame
    def _apply_morphs(self, g: _GpuPrim, weights: dict[int, float]) -> None:
        p = g.prim
        key = tuple(sorted((k, round(v, 3)) for k, v in weights.items() if v > 1e-3))
        if key == g.dirty_key:
            return
        g.dirty_key = key
        pos = p.pos.copy()
        nrm = p.nrm.copy()
        for ti, w in key:
            if ti < len(p.targets):
                dp, dn = p.targets[ti]
                if dp is not None:
                    pos += dp * w
                if dn is not None:
                    nrm += dn * w
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, g.vbo_pos)
        gl.glBufferSubData(gl.GL_ARRAY_BUFFER, 0, pos.nbytes, pos)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, g.vbo_nrm)
        gl.glBufferSubData(gl.GL_ARRAY_BUFFER, 0, nrm.nbytes, nrm)

    def _bind_tex(self, unit: int, tid: int | None, uniform: str, has: str) -> None:
        gl.glActiveTexture(gl.GL_TEXTURE0 + unit)
        gl.glBindTexture(gl.GL_TEXTURE_2D, tid if tid else self._white)
        gl.glUniform1i(self.loc(uniform), unit)
        if has:
            gl.glUniform1i(self.loc(has), 1 if tid else 0)

    def render(self, W: np.ndarray, view_proj: np.ndarray, morph: dict[int, dict[int, float]],
               light_dir=(-0.35, 0.55, 0.76), deform: dict | None = None,
               squeeze: tuple | None = None, cloth: dict | None = None) -> QImage:
        m = self.m
        self.ctx.makeCurrent(self.surface)
        self.fbo.bind()
        gl.glViewport(0, 0, self.w, self.h)
        gl.glClearColor(0, 0, 0, 0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glUseProgram(self.prog)
        gl.glUniformMatrix4fv(self.loc("uViewProj"), 1, gl.GL_TRUE, view_proj.astype(np.float32))
        ld = np.asarray(light_dir, np.float32)
        gl.glUniform3f(self.loc("uLightDir"), *(ld / np.linalg.norm(ld)))
        gl.glUniform1f(self.loc("uAspect"), self.w / self.h)
        gl.glUniform1f(self.loc("uRealism"), self.realism)
        c, r, a = squeeze if squeeze else ((0.0, 0.0, 0.0), 0.1, 0.0)
        gl.glUniform3f(self.loc("uSqzCenter"), float(c[0]), float(c[1]), float(c[2]))
        gl.glUniform1f(self.loc("uSqzRadius"), float(r))
        gl.glUniform1f(self.loc("uSqzAmount"), float(a))
        gl.glUniform1i(self.loc("uClothOn"), 1 if cloth else 0)
        if cloth:
            gl.glUniform1f(self.loc("uHipY"), float(cloth["hip_y"]))
            gl.glUniform1f(self.loc("uClothLen"), float(cloth["length"]))
            for name in ("uLegL0", "uLegL1", "uLegR0", "uLegR1"):
                gl.glUniform3f(self.loc(name), *[float(v) for v in cloth[name]])
            gl.glUniform3f(self.loc("uClothOff"), *[float(v) for v in cloth["offset"]])
            gl.glUniform1f(self.loc("uFlutter"), float(cloth["flutter"]))
            gl.glUniform1f(self.loc("uTime"), float(cloth["time"]))
            gl.glUniform1f(self.loc("uLegRadius"), float(cloth["leg_radius"]))
        gl.glUniform1i(self.loc("uDeform"), 1 if deform else 0)
        if deform:
            gl.glUniform1f(self.loc("uNeck"), deform["neck"])
            gl.glUniform1f(self.loc("uHeight"), deform["height"])
            gl.glUniform2f(self.loc("uPivot"), *deform["pivot"])
            gl.glUniformMatrix3fv(self.loc("uHeadRot"), 1, gl.GL_TRUE, np.asarray(deform["head"], np.float32))
            gl.glUniform2f(self.loc("uBend"), *deform["bend"])
            gl.glUniform1f(self.loc("uSquash"), deform["squash"])
            gl.glUniform1f(self.loc("uBreath"), deform["breath"])
            on, ay, sx, al, ar = deform.get("arms", (False, 0.0, 0.0, 0.0, 0.0))
            gl.glUniform1i(self.loc("uArmOn"), 1 if on else 0)
            gl.glUniform1f(self.loc("uArmY"), ay)
            gl.glUniform1f(self.loc("uShoulderX"), sx)
            gl.glUniform2f(self.loc("uArmAng"), al, ar)

        # build draw list: (queue, node, gpu prim, material)
        items = []
        for ni in m.draw_nodes:
            node = m.nodes[ni]
            for g in self._gpu.get(node.mesh, []):
                mat = m.materials[g.prim.material] if 0 <= g.prim.material < len(m.materials) else m.materials[0]
                queue = (2 if mat.alpha_mode == 2 else 0) * 1000 + mat.queue
                items.append((queue, ni, g, mat))
        items.sort(key=lambda it: it[0])

        last_skin = None
        for queue, ni, g, mat in items:
            node = m.nodes[ni]
            p = g.prim
            if p.targets:
                self._apply_morphs(g, morph.get(p.mesh, {}))
            skinned = node.skin is not None and p.joints is not None
            if skinned:
                if last_skin != (ni, node.skin):
                    sk = m.skins[node.skin]
                    inv_mesh = np.linalg.inv(W[ni])
                    mats = inv_mesh[None] @ W[sk["joints"]] @ sk["ibm"]
                    data = np.ascontiguousarray(mats.transpose(0, 2, 1), np.float32)
                    gl.glActiveTexture(gl.GL_TEXTURE2)
                    gl.glBindTexture(gl.GL_TEXTURE_2D, self._bone_tex)
                    gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA32F, 4, len(mats), 0, gl.GL_RGBA, gl.GL_FLOAT, data)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
                    last_skin = (ni, node.skin)
                gl.glActiveTexture(gl.GL_TEXTURE2)
                gl.glBindTexture(gl.GL_TEXTURE_2D, self._bone_tex)
                gl.glUniform1i(self.loc("uBones"), 2)
            gl.glUniform1i(self.loc("uSkinned"), 1 if skinned else 0)
            gl.glUniformMatrix4fv(self.loc("uModel"), 1, gl.GL_TRUE, W[ni].astype(np.float32))

            self._bind_tex(0, self.texture(mat.tex), "uTex", "uHasTex")
            self._bind_tex(1, self.texture(mat.shade_tex), "uShadeTex", "uHasShadeTex")
            self._bind_tex(3, self.texture(mat.emissive_tex), "uEmTex", "uHasEmTex")
            gl.glUniform4f(self.loc("uColor"), *mat.color)
            gl.glUniform3f(self.loc("uShade"), *mat.shade[:3])
            gl.glUniform3f(self.loc("uEmissive"), *mat.emissive[:3])
            gl.glUniform1i(self.loc("uAlphaMode"), mat.alpha_mode)
            gl.glUniform1f(self.loc("uCutoff"), mat.cutoff)
            gl.glUniform1i(self.loc("uUnlit"), 1 if mat.unlit else 0)
            gl.glUniform1f(self.loc("uShadeShift"), mat.shade_shift)
            gl.glUniform1f(self.loc("uShadeToony"), mat.shade_toony)
            cz = mat.colorize or [0.0, 0.0, 0.0, 0.0]
            gl.glUniform4f(self.loc("uColorize"), *[float(v) for v in cz[:4]])
            gl.glUniform1f(self.loc("uColorLift"), float(cz[4]) if len(cz) > 4 else 0.6)
            gl.glUniform1i(self.loc("uClothMat"), 1 if mat.cloth else 0)
            gl.glUniformMatrix3fv(self.loc("uUVXf"), 1, gl.GL_TRUE, mat.uv_xf.astype(np.float32))
            gl.glDepthMask(gl.GL_TRUE if mat.zwrite else gl.GL_FALSE)
            gl.glBindVertexArray(g.vao)

            if mat.outline_mode and mat.outline_width > 0:
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glCullFace(gl.GL_FRONT)
                gl.glUniform1i(self.loc("uOutlinePass"), 1)
                gl.glUniform1i(self.loc("uOutlineMode"), mat.outline_mode)
                gl.glUniform1f(self.loc("uOutlineWidth"), mat.outline_width)
                gl.glUniform3f(self.loc("uOutlineColor"), *mat.outline_color[:3])
                gl.glDrawElements(gl.GL_TRIANGLES, g.count, gl.GL_UNSIGNED_INT, ctypes.c_void_p(0))
            gl.glUniform1i(self.loc("uOutlinePass"), 0)
            gl.glUniform1i(self.loc("uOutlineMode"), 0)
            if mat.double_sided:
                gl.glDisable(gl.GL_CULL_FACE)
            else:
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glCullFace(gl.GL_BACK)
            gl.glDrawElements(gl.GL_TRIANGLES, g.count, gl.GL_UNSIGNED_INT, ctypes.c_void_p(0))

        gl.glBindVertexArray(0)
        gl.glDepthMask(gl.GL_TRUE)
        self.fbo.release()
        img = self.fbo.toImage()
        self.ctx.doneCurrent()
        return img
