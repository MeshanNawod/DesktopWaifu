"""The frameless, transparent desktop character: behaviour, physics and mouse interaction.

Click-through: the window uses WA_TranslucentBackground, which on Windows becomes a
per-pixel-alpha layered window, so clicks on fully transparent pixels fall through to
whatever is underneath. `force_hit_mask` additionally applies a window region (SetWindowRgn)
for setups where that native behaviour is unavailable.
"""
from __future__ import annotations

import random
import time
from collections import deque

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBitmap, QCursor, QGuiApplication, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from .config import Config
from .expressions import EMOTION_MOOD, ExpressionEngine, build
from .gestures import CLICK, HOLD, LIMB_ALIAS, LIMBS, PULL, GestureListener
from .sprites import (DRAG, FALL, IDLE, PET, REACT, SIT, SLEEP, WALK, FrameContext,
                      load_sprites)

TUGGABLE = {"hand_face", "hand_low", "hand_l", "hand_r", "scarf", "hair_l", "hair_r", "skirt"}
TUG_RANGE = 70        # px you can pull a part before she gets dragged along
ZONE_EMOTION = {
    "eye_l": "angry", "eye_r": "angry", "cheek_l": "shy", "cheek_r": "shy",
    "hand_face": "shy", "hand_low": "happy", "hand_l": "shy", "hand_r": "happy", "scarf": "surprised",
    "hair_l": "angry", "hair_r": "angry", "skirt": "angry", "chest": "angry", "body": "surprised",
    "mouth": "shy", "head": "happy",
}
SOFT_ZONES = {"head", "cheek_l", "cheek_r", "hand_face", "hand_low", "hand_l", "hand_r", "mouth"}
# hovering back and forth over these counts as stroking too (hair, and sensitive areas: gentle = shy smile)
STROKE_HOVER = SOFT_ZONES | {"hair_l", "hair_r", "chest", "skirt"}
BREAK_FREE_AFTER = (4.0, 7.5)   # seconds dangling from a limb before she wriggles loose
GRAVITY = 2600.0      # px/s^2
TICK_MS = 33          # ~30 fps
SLEEP_AFTER = 240.0   # seconds without interaction before she may nap


class CharacterWindow(QWidget):
    moved = Signal()
    petted = Signal()
    poked = Signal()
    touched = Signal(str)            # zone name, e.g. "eye_l", "scarf:tug"
    picked_up = Signal(str)          # what she was grabbed by: hand_l, hand_r, leg_l, leg_r or body
    broke_free = Signal()            # she wriggled out of a limb grab
    dropped = Signal(float)          # impact speed
    double_clicked = Signal()
    menu_requested = Signal(QPoint)

    def __init__(self, cfg: Config):
        super().__init__(None)
        self.cfg = cfg
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._apply_flags()

        self.ctx = FrameContext()
        self.state = FALL
        self.px = self.py = 0.0
        self.vx = self.vy = 0.0
        self.target_x = 0.0
        self.next_action = 2.0
        self.busy = False            # chat open etc. -> no wandering
        self.talking = False
        self.last_interaction = time.monotonic()
        self._emotion_left = 0.0
        self._blink_left, self._next_blink = 0.0, random.uniform(2, 5)
        self._frame: QImage | None = None

        # mouse
        self._pressed = False
        self._dragging = False
        self._press_global = QPointF()
        self._grab_offset = QPointF()
        self._drag_samples: deque[tuple[float, QPointF]] = deque(maxlen=8)
        self._rub = 0.0
        self._last_hover: QPointF | None = None
        self._press_zone: str | None = None
        self._hover_zone: str | None = None
        self._cursor_hist: deque[tuple[float, QPointF]] = deque(maxlen=40)
        self.sulking = False
        self._press_local = QPointF()
        self._pulling = False
        self._broke_free = False
        self._lift_t = 0.0
        self._free_after = 1e9
        self.gestures = GestureListener(self)
        self.gestures.gesture.connect(self._on_gesture)
        self.mood_label = "neutral"
        self.expressions = ExpressionEngine()
        self._prev_pos = (0.0, 0.0)
        self._prev_vel = (0.0, 0.0)
        self._acc = (0.0, 0.0)

        self.reload_sprites()
        self.reset_position()

        self._last_tick = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)
        self._raise_timer = QTimer(self)
        self._raise_timer.timeout.connect(self._keep_on_top)
        self._raise_timer.start(4000)

    # ----------------------------------------------------------------- setup
    def _apply_flags(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint
        if self.cfg.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def reload_sprites(self) -> None:
        dpr = (self.screen() or QGuiApplication.primaryScreen()).devicePixelRatio()
        self.sprites = load_sprites(self.cfg.sprite_pack, self.cfg.scale, dpr,
                                    self.cfg.builtin_style, self.cfg.image_pack, self.cfg.vrm_path)
        self.setFixedSize(self.sprites.size())
        if not self.cfg.force_hit_mask:
            self.clearMask()

    def apply_config(self) -> None:
        visible = self.isVisible()
        self._apply_flags()
        self.reload_sprites()
        if visible:
            self.show()
        self.state = FALL

    def reset_position(self) -> None:
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.px = g.x() + g.width() * 0.75
        self.py = g.y() + 40
        self.vx = self.vy = 0.0
        self._prev_pos, self._prev_vel = (self.px, self.py), (0.0, 0.0)
        self._set_state(FALL)
        self.move(round(self.px), round(self.py))

    # ----------------------------------------------------------------- public API
    def set_emotion(self, emotion: str, seconds: float = 6.0) -> None:
        self.ctx.emotion = emotion or "neutral"
        self._emotion_left = seconds
        if emotion and emotion != "neutral":
            self.ctx.expr = self.expressions.snap(EMOTION_MOOD.get(emotion, self.mood_label))
            if emotion == "pout":   # a proper pout: side glance + pouting lips
                self.ctx.expr = self.expressions.current = build("side_glance", "pout", random.choice((-1.0, 1.0)))

    def set_mood(self, label: str) -> None:
        """Her mood label from the emotion engine: happy, shy, flustered, sleepy, upset, playful, neutral."""
        self.mood_label = label

    def set_mouth(self, value: float) -> None:
        self.ctx.mouth = max(0.0, min(1.0, value))

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy and self.state == WALK:
            self._set_state(IDLE)
        if busy and self.state == SLEEP:
            self.wake()

    def set_talking(self, talking: bool) -> None:
        self.talking = talking
        if not talking:
            self.ctx.mouth = 0.0
        elif self.state == WALK:
            self._set_state(IDLE)

    def wake(self) -> None:
        if self.state == SLEEP:
            self._set_state(REACT, duration=1.5, emotion="surprised")
        self.last_interaction = time.monotonic()

    @property
    def asleep(self) -> bool:
        return self.state == SLEEP

    def head_anchor(self) -> QPoint:
        """Global point just above her head (for the speech bubble)."""
        r = self._head_rect_local()
        return self.mapToGlobal(QPoint(round(r.center().x()), round(r.top())))

    def global_rect(self) -> QRect:
        return QRect(self.pos(), self.size())

    # ----------------------------------------------------------------- state machine
    def _set_state(self, state: str, duration: float | None = None, emotion: str | None = None) -> None:
        self.state = state
        self.ctx.state_t = 0.0
        if emotion:
            self.set_emotion(emotion, 3.0)
        if duration is not None:
            self.next_action = duration

    def _floor(self) -> tuple[float, float, float]:
        """(left, right, ground_y) for the window, using the screen under her centre."""
        c = QPoint(round(self.px + self.width() / 2), round(self.py + self.height() / 2))
        screen = QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()  # excludes the taskbar -> its top edge is the floor
        ground = g.y() + g.height() - self.height() + self.sprites.foot_offset()
        return float(g.left()), float(g.right() - self.width()), float(ground)

    def _decide(self) -> None:
        left, right, _ = self._floor()
        a = max(0.0, min(1.0, self.cfg.activity))
        idle_for = time.monotonic() - self.last_interaction
        if self.sulking:
            self._set_state(SIT, duration=1e9)
            return
        if self.busy or self.talking or self.ctx.music:
            self._set_state(IDLE, duration=3.0)   # stays put (and dances if music is on)
            return
        r = random.random()
        if r < 0.15 + 0.45 * a:
            span = (right - left)
            self.target_x = max(left, min(right, self.px + random.uniform(-0.5, 0.5) * span))
            self.ctx.facing_left = self.target_x < self.px
            self._set_state(WALK, duration=30)
        elif r < 0.75:
            self._set_state(IDLE, duration=random.uniform(3, 9) / (0.5 + a))
        else:
            self._set_state(SIT, duration=random.uniform(8, 22) * (1.5 - a))

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = max(1e-3, min(now - self._last_tick, 0.1))
        self._last_tick = now
        ctx = self.ctx
        ctx.t += dt
        ctx.state_t += dt

        # blinking / emotion decay / hearts
        if self._blink_left > 0:
            self._blink_left -= dt
        else:
            self._next_blink -= dt
            if self._next_blink <= 0:
                self._blink_left = 0.13
                self._next_blink = random.uniform(2.0, 5.5)
        ctx.blink = self._blink_left > 0
        if self._emotion_left > 0:
            self._emotion_left -= dt
            if self._emotion_left <= 0:
                ctx.emotion = "neutral"
        ctx.pet_level = max(0.0, ctx.pet_level - dt * 0.5)
        ctx.beat = max(0.0, ctx.beat - dt * 4.0)
        # struggling settles to a steady wriggle while she's held, and stops once she's down
        held = time.monotonic() - self._lift_t if self.state == DRAG else 0.0
        base = (0.4 + min(0.5, held * 0.12) if ctx.grab else 0.4) if self.state == DRAG else 0.0
        ctx.struggle += (base - ctx.struggle) * min(1.0, dt * (0.8 if self.state == DRAG else 3.0))
        if self.state == DRAG and ctx.grab and held > self._free_after:
            self._break_free()
        mood = "sleepy" if self.state == SLEEP else self.mood_label
        ctx.expr = self.expressions.update(dt, mood, ctx.emotion if self._emotion_left > 0 else None)
        self._rub = max(0.0, self._rub - dt * 120)

        left, right, ground = self._floor()
        st = self.state

        if st == DRAG:
            pass  # position driven by mouse
        elif st == FALL or self.py < ground - 1:
            if st != FALL:
                self._set_state(FALL)
            self.vy += GRAVITY * dt
            self.vx *= 0.995
            self.px += self.vx * dt
            self.py += self.vy * dt
            if self.px < left or self.px > right:
                self.px = max(left, min(right, self.px))
                self.vx = -self.vx * 0.5
            if self.py >= ground:
                self.py = ground
                impact = self.vy
                if impact > 1100:
                    self.vy = -impact * 0.28
                    self.vx *= 0.6
                else:
                    self.vy = self.vx = 0.0
                    self._set_state(REACT if impact > 600 else IDLE, duration=1.5)
                    self.dropped.emit(impact)
        else:
            self.py = ground
            self.next_action -= dt
            if st == WALK:
                if self.busy or self.talking:
                    self._set_state(IDLE, duration=2)
                else:
                    step = self.cfg.walk_speed * dt * (-1 if self.ctx.facing_left else 1)
                    self.px += step
                    if (self.ctx.facing_left and self.px <= self.target_x) or \
                            (not self.ctx.facing_left and self.px >= self.target_x) or \
                            self.px <= left or self.px >= right:
                        self.px = max(left, min(right, self.px))
                        self._set_state(IDLE, duration=random.uniform(2, 5))
            elif st in (PET, REACT) and self.next_action <= 0:
                self._set_state(IDLE, duration=random.uniform(2, 5))
            if self.next_action <= 0 and self.state in (IDLE, SIT, WALK):
                self._decide()
            self.px = max(left, min(right, self.px))

        # window motion drives hair/scarf/skirt physics
        wvx, wvy = (self.px - self._prev_pos[0]) / dt, (self.py - self._prev_pos[1]) / dt
        wax, way = (wvx - self._prev_vel[0]) / dt, (wvy - self._prev_vel[1]) / dt
        self._acc = (self._acc[0] * 0.5 + wax * 0.5, self._acc[1] * 0.5 + way * 0.5)
        self._prev_pos, self._prev_vel = (self.px, self.py), (wvx, wvy)
        if hasattr(self.sprites, "step"):
            self.sprites.step(dt, self._acc[0], self._acc[1], wvx, wvy, self.state, ctx)
            cur = QPointF(self.mapFromGlobal(QCursor.pos()))
            near = (cur - QPointF(self.width() / 2, self.height() / 3)).manhattanLength() < 1400
            self.sprites.look_at(cur if near and self.state != SLEEP else None)

        self._frame = self.sprites.render(self.state, ctx, self.devicePixelRatioF())
        if self.cfg.force_hit_mask and int(ctx.t / (TICK_MS / 1000)) % 3 == 0:
            self._apply_hit_mask()
        pos = QPoint(round(self.px), round(self.py))
        if pos != self.pos():
            self.move(pos)
            self.moved.emit()
        self.update()

    def _keep_on_top(self) -> None:
        if self.cfg.always_on_top and not self._dragging and self.isVisible():
            self.raise_()

    def _apply_hit_mask(self) -> None:
        if self._frame is None:
            return
        small = self._frame.scaled(self.size())
        mask = small.createAlphaMask(Qt.ImageConversionFlag.ThresholdAlphaDither)
        self.setMask(QRegion(QBitmap.fromImage(mask)))

    # ----------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        if self._frame is None:
            return
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawImage(0, 0, self._frame)
        p.end()

    # ----------------------------------------------------------------- mouse
    def _head_rect_local(self) -> QRectF:
        r = self.sprites.head_rect(self.state)
        if self.ctx.facing_left and not getattr(self.sprites, "mirrors_itself", False):
            r = QRectF(self.width() - r.right(), r.top(), r.width(), r.height())
        return r

    def _zone_at(self, pos: QPointF) -> str:
        if hasattr(self.sprites, "zone_at"):
            return self.sprites.zone_at(pos) or "body"
        return "head" if self._head_rect_local().contains(pos) else "body"

    # mouse -> gesture listener ------------------------------------------------
    def _on_gesture(self, kind: str, zone: str) -> None:
        if self.state in (DRAG, FALL):
            return
        if kind == PULL:
            self.touch(zone, "tug")         # pulling her hair / scarf: pouty "don't pull!"
        elif kind == CLICK:
            self.touch(zone, "gentle")      # short press: soft smile, sweet greeting
        elif kind == HOLD:
            self.touch(zone, "rough")       # long firm press: it hurts / startles her
        else:
            self.touch(zone, "pet")         # double click or stroking: petting

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.MiddleButton:
            self.double_clicked.emit()      # middle click opens / closes the chat
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.last_interaction = time.monotonic()
        self._pressed = True
        self._dragging = False
        self._press_global = e.globalPosition()
        self._grab_offset = e.globalPosition() - QPointF(self.px, self.py)
        self._press_zone = self._zone_at(e.position())
        self._press_local = e.position()
        self.gestures.press(self._press_zone, e.globalPosition())

    def mouseMoveEvent(self, e) -> None:
        gp = e.globalPosition()
        self._cursor_hist.append((time.perf_counter(), gp))
        if self._pressed:
            mode = self.gestures.move(gp)
            if mode == "pull" and hasattr(self.sprites, "tug"):
                self._pulling = True
                self.sprites.tug(self._press_zone, gp - self._press_global)
                return
            if mode == "lift" and not self._dragging and not self._broke_free:
                self._start_lift(self._press_zone)
            if self._dragging:
                now = time.perf_counter()
                self._drag_samples.append((now, gp))
                inst_vx = 0.0
                if len(self._drag_samples) >= 2:
                    (t0, p0), (t1, p1) = self._drag_samples[0], self._drag_samples[-1]
                    if t1 - t0 > 1e-3:
                        inst_vx = (p1.x() - p0.x()) / (t1 - t0)
                self.ctx.vx = inst_vx
                self.ctx.tilt += (max(-35.0, min(35.0, -inst_vx * 0.035)) - self.ctx.tilt) * 0.3
                # shaking her makes her struggle harder
                self.ctx.struggle = min(1.0, self.ctx.struggle + abs(inst_vx) * 0.00002)
                target = gp - self._grab_offset
                self.px, self.py = target.x(), target.y()
                self.move(round(self.px), round(self.py))
                self.moved.emit()
            return

        # hovering: slowly stroking back and forth over her head / face = petting
        zone = self._zone_at(e.position())
        soft = zone in STROKE_HOVER
        self.setCursor(Qt.CursorShape.OpenHandCursor if soft or zone in LIMBS else Qt.CursorShape.ArrowCursor)
        if zone in ("eye_l", "eye_r", "nose") and hasattr(self.sprites, "squint"):
            self.sprites.squint(True)   # she squints when your cursor comes near her eyes / nose
        if soft and zone == self._hover_zone and self._last_hover is not None:
            self._rub += (e.position() - self._last_hover).manhattanLength()
            if self._rub > 260 * self.cfg.scale:
                self._rub = 0.0
                self.gestures.hover_stroke(zone)
        self._hover_zone = zone
        self._last_hover = e.position()

    def _start_lift(self, zone: str) -> None:
        """Picked up. Grabbing a hand or leg lifts her by that limb: she dangles from it and struggles."""
        self._dragging = True
        self._drag_samples.clear()
        limb = LIMB_ALIAS.get(zone, zone) if zone in LIMBS else ""
        self.ctx.grab = limb
        self.ctx.struggle = 0.35
        self._lift_t = time.monotonic()
        self._free_after = random.uniform(*BREAK_FREE_AFTER) if limb else 1e9
        if limb:
            # hold her from near the top of the window so her body can hang below the grabbed limb
            # keep the grabbed limb on the side it was on, so her body hangs centred in the window
            cx = self.width() / 2
            pin = QPointF(cx + (self._press_local.x() - cx) * 0.6, self.height() * 0.07)
            self._grab_offset = pin
            self.ctx.grab_pt = (pin.x(), pin.y())
            target = self._press_global - pin
            self.px, self.py = target.x(), target.y()
            self.move(round(self.px), round(self.py))
        else:
            self.ctx.grab_pt = (self._press_local.x(), self._press_local.y())
        self._set_state(DRAG, emotion="surprised")
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.picked_up.emit(limb or "body")

    def _break_free(self) -> None:
        """She wriggles out of your grip and drops (until you let go and grab her again)."""
        self._dragging = False
        self._broke_free = True
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.vx = random.choice((-1, 1)) * random.uniform(150, 350)
        self.vy = -random.uniform(250, 450)          # a little hop out of your hand
        self.ctx.grab = ""
        self.ctx.tilt = 0.0
        self._set_state(FALL)
        self.broke_free.emit()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self._pressed:
            return
        self._pressed = False
        self.gestures.release()
        self._broke_free = False
        if self._pulling:
            self._pulling = False
            self.sprites.tug(self._press_zone, None)     # let go: the hair springs back
            return
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            vx = vy = 0.0
            if len(self._drag_samples) >= 2:
                (t0, p0), (t1, p1) = self._drag_samples[0], self._drag_samples[-1]
                if time.perf_counter() - t1 < 0.08 and t1 - t0 > 1e-3:
                    vx = (p1.x() - p0.x()) / (t1 - t0)
                    vy = (p1.y() - p0.y()) / (t1 - t0)
            self.vx = max(-2500.0, min(2500.0, vx))
            self.vy = max(-2500.0, min(2500.0, vy))
            self.ctx.tilt = 0.0
            self.ctx.grab = ""
            self._set_state(FALL)

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._press_global = e.globalPosition()
            self._grab_offset = e.globalPosition() - QPointF(self.px, self.py)
            self.gestures.double(self._zone_at(e.position()))

    def contextMenuEvent(self, e) -> None:
        self.menu_requested.emit(e.globalPos())

    def leaveEvent(self, _e) -> None:
        self._last_hover = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ----------------------------------------------------------------- reactions
    def pet(self) -> None:
        self.touch("head", "pet")

    def poke(self) -> None:
        self.touch("body", "gentle")

    def touch(self, zone: str, kind: str = "gentle") -> None:
        """A touch on one part of her body.

        kind: "gentle" (short click), "rough" (long firm press), "pet" (double click / stroking),
              "tug" (legacy: hold-and-pull on a soft part).
        """
        self.last_interaction = time.monotonic()
        if self.state in (DRAG, FALL):
            return
        zone = zone or "body"
        if hasattr(self.sprites, "react") and kind != "tug":
            self.sprites.react(zone, kind)
        if kind == "rough":
            self._set_state(REACT, duration=1.8)
            self.set_emotion("hurt", 5.0)
        elif kind == "pet":
            self.ctx.pet_level = 1.0
            self._set_state(PET, duration=3.0)
            self.set_emotion(random.choice(["shy", "happy"]), 5.0)
            if zone == "head":
                self.petted.emit()
        else:
            if zone in SOFT_ZONES:
                self.ctx.pet_level = max(self.ctx.pet_level, 0.3)
            self._set_state(REACT, duration=1.4)
            self.set_emotion("happy" if zone in SOFT_ZONES or zone == "head" else ZONE_EMOTION.get(zone, "surprised"), 3.5)
        self.touched.emit(f"{zone}:{kind}")
        if zone == "body":
            self.poked.emit()


    # ----------------------------------------------------------------- moods driven by the app
    def go_to_sleep(self) -> None:
        if self.state not in (DRAG, FALL):
            self._set_state(SLEEP, duration=1e9)

    def set_sulk(self, on: bool) -> None:
        """Hurt: she sits down, faces away and won't wander."""
        self.sulking = on
        if on and self.state in (IDLE, WALK, SIT):
            self._set_state(SIT, duration=1e9)
        elif not on and self.state == SIT:
            self._set_state(IDLE, duration=2)

    def set_music(self, on: bool, beat: float = 0.0) -> None:
        self.ctx.music = on
        self.ctx.beat = max(self.ctx.beat, beat)
        if on and self.state == WALK:
            self._set_state(IDLE, duration=8)
