"""Render the website screenshots of the newer features into docs/images/.

    python tools/make_site_images.py

Runs the real app offscreen with a throwaway settings folder, sets up scenes and composites her window plus
the pet / toy / plant windows onto a soft background with a taskbar strip.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="waifu_site_")
os.environ.pop("GEMINI_API_KEY", None)

from PySide6.QtCore import QRect, QRectF, Qt, QTimer          # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter   # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

OUT = ROOT / "docs" / "images"


def main() -> None:
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from waifu.app import WaifuApp
    from waifu.sprites import list_characters
    w = WaifuApp(app)
    c, pl = w.character, w.play
    w.say = lambda *a, **k: None
    w._next_song = 1e18

    def ticks(n):
        for _ in range(n):
            c._last_tick -= 1 / 30
            c._tick()
            pl._last -= 1 / 30
            pl._tick()
            app.processEvents()

    def scene(wide=300, top_cut=0.0, label=""):
        r = c.global_rect()
        box = QRect(r.left() - wide, r.top() + int(r.height() * top_cut), r.width() + 2 * wide,
                    int(r.height() * (1 - top_cut)) + 10)
        img = QImage(box.size(), QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        g = QLinearGradient(0, 0, 0, box.height())
        g.setColorAt(0, QColor("#fdeef5"))
        g.setColorAt(1, QColor("#e9e6fb"))
        p.fillRect(img.rect(), g)
        p.fillRect(0, box.height() - 10, box.width(), 10, QColor("#2b2d3a"))          # the taskbar
        for t in pl.things:
            if t.isVisible():
                p.drawImage(t.x() - box.left(), t.y() - box.top(), t.grab().toImage())
        p.drawImage(c.x() - box.left(), c.y() - box.top(), c.grab().toImage())
        if label:
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            p.setPen(QColor("#7a3a5c"))
            p.drawText(QRectF(0, 8, box.width(), 24), Qt.AlignmentFlag.AlignHCenter, label)
        p.end()
        return img

    def sheet(images, cols, name):
        W = max(i.width() for i in images)
        H = max(i.height() for i in images)
        rows = (len(images) + cols - 1) // cols
        s = QImage(W * cols, H * rows, QImage.Format.Format_ARGB32_Premultiplied)
        s.fill(QColor("#ffffff"))
        p = QPainter(s)
        for i, im in enumerate(images):
            p.drawImage((i % cols) * W + (W - im.width()) // 2, (i // cols) * H, im)
        p.end()
        s.save(str(OUT / name))
        print("wrote", OUT / name)

    def go():
        try:
            style, path = next((s, p) for l, s, p in list_characters() if "Seed-chan" in l)
            w.choose_character(style, path)
            ticks(40)
            pl._next_play = 1e18
            # ---- pets
            for k in ("cat", "puppy", "bunny", "ball", "teddy"):
                pl.add(k, announce=False)
            ticks(90)
            pl.start_activity("pet", pl.get("cat"))
            for _ in range(200):
                ticks(1)
                if pl._act and pl._act["stage"] == "do" and pl._act["k"] > 1.2:
                    break
            pets = scene(320, 0.25, "Pets & toys: she pets her kitten")
            pl._act = None
            c.stand_up()
            # ---- activities
            shots = []
            for name, lbl in (("cuddle", "Cuddles"), ("party", "Dance party"), ("read", "Reading"), ("tea", "Tea time")):
                c.release_hold()
                ticks(20)
                pl.start_activity(name, pl.get("puppy") if name == "cuddle" else None)
                for _ in range(240):
                    ticks(1)
                    if pl._act is None or (pl._act["stage"] == "do" and pl._act["k"] > 1.0):
                        break
                ticks(15)
                shots.append(scene(170, 0.12, lbl))
                pl._act = None
                c.stand_up()
            sheet(shots, 4, "activities.png")
            pets.save(str(OUT / "pets.png"))
            print("wrote", OUT / "pets.png")
            c.release_hold()
            pl.clear()
            ticks(20)
            # ---- garden
            for x in list(pl.plants()):
                pl.drop_thing(x)
            her = c.global_rect().center().x()
            for dx, kind, g in ((-330, "cherry", 1.0), (-210, "sunflower", 1.0), (-130, "tulip", 0.95),
                                (180, "apple", 1.0), (300, "daisy", 0.92), (380, "pine", 0.8)):
                pl._spawn_plant(her + dx, kind, g)
            ticks(10)
            pl.start_activity("water", pl.plants()[3])
            for _ in range(200):
                ticks(1)
                if pl._act and pl._act["stage"] == "do" and pl._act["k"] > 1.0:
                    break
            scene(430, 0.1, "Her garden: she plants and waters flowers and trees").save(str(OUT / "garden.png"))
            print("wrote", OUT / "garden.png")
            pl._act = None
            c.stand_up()
            c.release_hold()
            for x in list(pl.plants()):
                pl.drop_thing(x)
            # ---- poses
            shots = []
            for pose, lbl in (("kneel", "Kneel"), ("bow", "Bow"), ("peace", "Peace sign"), ("hands_on_hips", "Hero pose"),
                              ("dance", "Dance"), ("think", "Thinking")):
                w.do_command(pose, quiet=True)
                ticks(40)
                shots.append(scene(40, 0.0, lbl))
            w.do_command("stand", quiet=True)
            sheet(shots, 6, "poses.png")
        except Exception:
            traceback.print_exc()
        app.quit()

    QTimer.singleShot(1500, go)
    app.exec()
    pl.clear()


if __name__ == "__main__":
    main()
