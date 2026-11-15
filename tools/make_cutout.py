"""Turn a character picture on a plain light background into a transparent PNG.

Usage:
    .venv\\Scripts\\python tools\\make_cutout.py input.jpg output.png [--upscale 2]

Background = near-white pixels connected to the image border, plus large enclosed
near-white pockets (e.g. the gap between an arm and the body). Edges get a soft
alpha and the white fringe is removed.
"""
from __future__ import annotations

import argparse
from collections import deque

import numpy as np
from PIL import Image, ImageFilter


def cutout(img: Image.Image, upscale: int = 2, tol: int = 22) -> Image.Image:
    if upscale > 1:
        img = img.resize((img.width * upscale, img.height * upscale), Image.LANCZOS)
    rgb = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w, _ = rgb.shape
    mn = rgb.min(axis=2)
    spread = rgb.max(axis=2) - mn
    whiteish = (mn >= 255 - tol) & (spread <= 14)

    # flood fill from the border
    bg = np.zeros((h, w), bool)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if whiteish[y, x] and not bg[y, x]:
                bg[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if whiteish[y, x] and not bg[y, x]:
                bg[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < h and 0 <= nx < w and whiteish[ny, nx] and not bg[ny, nx]:
                bg[ny, nx] = True
                q.append((ny, nx))

    # enclosed pure-white pockets bigger than ~0.15% of the image
    pure = (mn >= 248) & (spread <= 6) & ~bg
    seen = np.zeros_like(pure)
    min_size = int(h * w * 0.0015)
    for sy, sx in zip(*np.nonzero(pure)):
        if seen[sy, sx]:
            continue
        comp, q = [], deque([(sy, sx)])
        seen[sy, sx] = True
        while q:
            y, x = q.popleft()
            comp.append((y, x))
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and pure[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(comp) >= min_size:
            ys, xs = zip(*comp)
            bg[list(ys), list(xs)] = True

    # soft edge: pixels near the background get alpha from how "non-white" they are
    bg_img = Image.fromarray((bg * 255).astype(np.uint8))
    near = np.asarray(bg_img.filter(ImageFilter.MaxFilter(5))) > 0
    alpha = np.where(bg, 0.0, 1.0)
    edge = near & ~bg
    darkness = (255 - mn).astype(np.float32)
    alpha[edge] = np.clip(darkness[edge] / 70.0, 0.0, 1.0)

    # remove white fringe: c = (c_obs - (1-a)*255) / a
    a = alpha[..., None]
    col = rgb.astype(np.float32)
    safe = np.maximum(a, 1e-3)
    col = np.where(a > 0, (col - (1 - a) * 255.0) / safe, 0)
    col = np.clip(col, 0, 255)
    out = np.dstack([col, alpha * 255]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--upscale", type=int, default=2)
    ap.add_argument("--tol", type=int, default=22)
    a = ap.parse_args()
    cutout(Image.open(a.input), a.upscale, a.tol).save(a.output)
    print("saved", a.output)


if __name__ == "__main__":
    main()
