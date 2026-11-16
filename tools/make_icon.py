"""Render the app icon (assets/icon.png + assets/icon.ico) from Seed-chan's face.

    python tools/make_icon.py
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="waifu_icon_"))     # don't touch the real settings

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer                     # noqa: E402
from PySide6.QtGui import (QColor, QImage, QPainter, QPainterPath, QPen,      # noqa: E402
                           QRadialGradient)
from PySide6.QtWidgets import QApplication                                  # noqa: E402

SIZE = 512


def main() -> None:
    app = QApplication([])
    from waifu.sprites import FrameContext, IDLE, load_sprites
    from waifu.config import Config
    cfg = Config()
    cfg.builtin_style = "vrm"
    cfg.vrm_path = str(ROOT / "assets" / "models" / "Seed-chan.variant.json")
    cfg.scale = 2.4
    sp = load_sprites("", cfg.scale, 2.0, "vrm", "", cfg.vrm_path)
    ctx = FrameContext()
    ctx.emotion = "happy"
    for i in range(45):
        ctx.t = i / 30
        sp.step(1 / 30, 0, 0, 0, 0, IDLE, ctx)
    sp.look_at(None)
    img = sp.render(IDLE, ctx, 2.0)
    hr = sp.head_rect(IDLE)
    d = img.devicePixelRatio()
    # crop a square around her head (a little extra above for the cat ears)
    side = hr.width() * 1.75
    cx, cy = hr.center().x(), hr.center().y() - hr.height() * 0.05
    crop = img.copy(int((cx - side / 2) * d), int((cy - side / 2) * d), int(side * d), int(side * d))

    out = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    circle = QRectF(16, 16, SIZE - 32, SIZE - 32)
    g = QRadialGradient(circle.center() + QPointF(-60, -80), SIZE * 0.6)
    g.setColorAt(0, QColor("#fff0f6"))
    g.setColorAt(0.55, QColor("#ffc7de"))
    g.setColorAt(1, QColor("#ff8fbd"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(circle)
    clip = QPainterPath()
    clip.addEllipse(circle)
    p.setClipPath(clip)
    p.drawImage(QRectF(circle.left() - 10, circle.top() + 10, circle.width() + 20, circle.height() + 20), crop)
    p.setClipping(False)
    p.setPen(QPen(QColor("#ffffff"), 14))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(circle.adjusted(7, 7, -7, -7))
    p.setPen(QPen(QColor("#e05a93"), 6))
    p.drawEllipse(circle)
    # a little heart + sparkle
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ff4f8b"))
    hx, hy, s = SIZE * 0.82, SIZE * 0.80, 70
    heart = QPainterPath()
    heart.moveTo(hx, hy + s * 0.3)
    heart.cubicTo(hx - s * 0.5, hy - s * 0.2, hx - s * 0.1, hy - s * 0.55, hx, hy - s * 0.2)
    heart.cubicTo(hx + s * 0.1, hy - s * 0.55, hx + s * 0.5, hy - s * 0.2, hx, hy + s * 0.3)
    p.setPen(QPen(QColor("#ffffff"), 6))
    p.drawPath(heart)
    p.setPen(QPen(QColor("#ffffff"), 5))
    sx, sy = SIZE * 0.17, SIZE * 0.2
    for a in range(4):
        ang = a * math.pi / 2
        p.drawLine(QPointF(sx, sy), QPointF(sx + math.cos(ang) * 22, sy + math.sin(ang) * 22))
    p.end()

    png = ROOT / "assets" / "icon.png"
    out.save(str(png))
    from PIL import Image
    Image.open(png).save(ROOT / "assets" / "icon.ico",
                         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    out.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation) \
        .save(str(ROOT / "docs" / "favicon.png"))
    print("wrote", png, ROOT / "assets" / "icon.ico")
    QTimer.singleShot(0, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
