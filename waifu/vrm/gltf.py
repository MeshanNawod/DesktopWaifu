"""Minimal glTF 2.0 / GLB reader (enough for VRM): accessors incl. sparse, buffer views, images."""
from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

import numpy as np

COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
NORM_DIV = {np.int8: 127.0, np.uint8: 255.0, np.int16: 32767.0, np.uint16: 65535.0}


class Gltf:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = self.path.read_bytes()
        self.bin = b""
        if data[:4] == b"glTF":
            off = 12
            while off < len(data):
                clen, ctype = struct.unpack_from("<II", data, off)
                chunk = data[off + 8: off + 8 + clen]
                if ctype == 0x4E4F534A:
                    self.j = json.loads(chunk.decode("utf-8"))
                elif ctype == 0x004E4942:
                    self.bin = chunk
                off += 8 + clen
        else:
            self.j = json.loads(data.decode("utf-8"))
        self._buffers: dict[int, bytes] = {}

    # ------------------------------------------------------------------ raw data
    def buffer(self, i: int) -> bytes:
        if i not in self._buffers:
            b = self.j["buffers"][i]
            uri = b.get("uri")
            if uri is None:
                self._buffers[i] = self.bin
            elif uri.startswith("data:"):
                self._buffers[i] = base64.b64decode(uri.split(",", 1)[1])
            else:
                self._buffers[i] = (self.path.parent / uri).read_bytes()
        return self._buffers[i]

    def view_bytes(self, i: int) -> bytes:
        bv = self.j["bufferViews"][i]
        off = bv.get("byteOffset", 0)
        return self.buffer(bv["buffer"])[off: off + bv["byteLength"]]

    def _read(self, view: int, byte_offset: int, dtype, n: int, count: int) -> np.ndarray:
        bv = self.j["bufferViews"][view]
        buf = self.buffer(bv["buffer"])
        off = bv.get("byteOffset", 0) + byte_offset
        item = np.dtype(dtype).itemsize
        stride = bv.get("byteStride", item * n)
        arr = np.ndarray(shape=(count, n), dtype=dtype, buffer=buf, offset=off, strides=(stride, item))
        return np.array(arr)

    def accessor(self, i: int) -> np.ndarray:
        a = self.j["accessors"][i]
        dt = COMP[a["componentType"]]
        n = NCOMP[a["type"]]
        count = a["count"]
        if "bufferView" in a:
            arr = self._read(a["bufferView"], a.get("byteOffset", 0), dt, n, count)
        else:
            arr = np.zeros((count, n), dt)
        norm = a.get("normalized", False)
        if norm and dt in NORM_DIV:
            arr = np.maximum(arr.astype(np.float32) / NORM_DIV[dt], -1.0)
        if "sparse" in a:
            s = a["sparse"]
            idx = self._read(s["indices"]["bufferView"], s["indices"].get("byteOffset", 0),
                             COMP[s["indices"]["componentType"]], 1, s["count"]).ravel()
            vals = self._read(s["values"]["bufferView"], s["values"].get("byteOffset", 0), dt, n, s["count"])
            if norm and dt in NORM_DIV:
                vals = np.maximum(vals.astype(np.float32) / NORM_DIV[dt], -1.0)
            arr = arr.astype(vals.dtype) if arr.dtype != vals.dtype else arr
            arr[idx.astype(np.int64)] = vals
        return arr

    def image_bytes(self, image_index: int) -> bytes:
        img = self.j["images"][image_index]
        if "bufferView" in img:
            return self.view_bytes(img["bufferView"])
        uri = img["uri"]
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        return (self.path.parent / uri).read_bytes()
