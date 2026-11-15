"""Low-level binary FBX parser: returns a tree of FbxNode(name, props, children)."""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MAGIC = b"Kaydara FBX Binary  \x00"
ARR = {b"f": np.float32, b"d": np.float64, b"l": np.int64, b"i": np.int32, b"b": np.bool_}


@dataclass
class FbxNode:
    name: str
    props: list
    children: list = field(default_factory=list)

    def find(self, name: str) -> "FbxNode | None":
        return next((c for c in self.children if c.name == name), None)

    def find_all(self, name: str) -> list["FbxNode"]:
        return [c for c in self.children if c.name == name]

    def value(self, name: str, default=None):
        n = self.find(name)
        return n.props[0] if n is not None and n.props else default


def _read_props(data: bytes, off: int, count: int):
    props = []
    for _ in range(count):
        t = data[off:off + 1]
        off += 1
        if t == b"Y":
            props.append(struct.unpack_from("<h", data, off)[0]); off += 2
        elif t == b"C":
            props.append(bool(data[off])); off += 1
        elif t == b"I":
            props.append(struct.unpack_from("<i", data, off)[0]); off += 4
        elif t == b"F":
            props.append(struct.unpack_from("<f", data, off)[0]); off += 4
        elif t == b"D":
            props.append(struct.unpack_from("<d", data, off)[0]); off += 8
        elif t == b"L":
            props.append(struct.unpack_from("<q", data, off)[0]); off += 8
        elif t in ARR:
            n, enc, clen = struct.unpack_from("<III", data, off)
            off += 12
            raw = data[off:off + clen]
            off += clen
            if enc == 1:
                raw = zlib.decompress(raw)
            props.append(np.frombuffer(raw, ARR[t], n).copy())
        elif t in (b"S", b"R"):
            n = struct.unpack_from("<I", data, off)[0]
            off += 4
            raw = data[off:off + n]
            off += n
            props.append(raw.decode("utf-8", "replace") if t == b"S" else raw)
        else:
            raise ValueError(f"Unknown FBX property type {t!r} at {off}")
    return props, off


def parse(path: str | Path) -> FbxNode:
    data = Path(path).read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError("Only binary FBX files are supported (re-export as binary FBX)")
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    hdr = 25 if wide else 13

    def read(off: int) -> tuple[FbxNode | None, int]:
        if wide:
            end, nprops, _plen = struct.unpack_from("<QQQ", data, off)
        else:
            end, nprops, _plen = struct.unpack_from("<III", data, off)
        if end == 0:
            return None, off + hdr
        nlen = data[off + hdr - 1]
        name = data[off + hdr: off + hdr + nlen].decode("ascii", "replace")
        p = off + hdr + nlen
        props, p = _read_props(data, p, nprops)
        node = FbxNode(name, props)
        while p < end:
            child, p = read(p)
            if child is None:
                break
            node.children.append(child)
        return node, end

    root = FbxNode("root", [])
    off = 27
    while off < len(data) - hdr:
        node, off = read(off)
        if node is None:
            break
        root.children.append(node)
    root.version = version  # type: ignore[attr-defined]
    return root
