"""The frameless, transparent desktop character: behaviour, physics and mouse interaction.

Click-through: the window uses WA_TranslucentBackground, which on Windows becomes a
per-pixel-alpha layered window, so clicks on fully transparent pixels fall through to
whatever is underneath. `force_hit_mask` additionally applies a window region (SetWindowRgn)
for setups where that native behaviour is unavailable.
"""
from __future__ import annotations

import math
import random
import time
from collections import deque

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBitmap, QColor, QCursor, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient, QRegion)
from PySide6.QtWidgets import QApplication, QToolTip, QWidget

from .config import Config
from .expressions import EMOTION_MOOD, ExpressionEngine, build
from .gestures import CLICK, DOUBLE, HARD, LIMB_ALIAS, LIMBS, MOVE, PULL, SQUEEZE, STROKE, GestureListener
from .sprites import (DRAG, FALL, IDLE, KNOCK, PET, REACT, SIT, SLEEP, WALK, FrameContext,
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
# what each part does, shown as a little hint when you hover over it
ZONE_HINTS = {
    "head": "♡ Head: click = hello · double-click / stroke = headpat · drag = tilt her head · hold = squish",
    "hair": "♡ Hair: drag = play with / pull her hair · double-click = pat",
    "eyes": "Eyes: click = poke (she blinks!) · hold = squish · drag = turn her head",
    "nose": "Nose: click = boop · hold = squish",
    "cheek": "♡ Cheek: hold = squish (puni puni~) · double-click = stroke · drag = tilt her head",
    "mouth": "Mouth: click = boop · hold = shush",
    "hand": "♡ Hand: drag = move her arm · drag far = lift her by the hand · double-click = hold hands",
    "leg": "Leg: drag = move her leg · drag far = lift her upside down",
    "body": "Body: drag = make her lean · drag far = pick her up · hold = squeeze-hug · right-drag = rotate her",
    "sensitive": "Careful… gentle stroke = shy smile · sudden touch = flustered!",
}


def _hint_group(zone):
    if not zone:
        return None
    for prefix, group in (("hair", "hair"), ("eye", "eyes"), ("cheek", "cheek"), ("hand", "hand"), ("leg", "leg")):
        if zone.startswith(prefix):
            return group
    return {"chest": "sensitive", "skirt": "sensitive", "scarf": "hair"}.get(zone, zone)
BREAK_FREE_AFTER = (4.0, 7.5)   # seconds dangling from a limb before she wriggles loose
SMACK_SPEED = 1700.0            # px/s: the cursor hits her this fast -> she's knocked over
TWIRL_TIME = 0.9
CUTE_ACTIONS = ["jump", "hop_hop", "twirl", "nya", "heart", "kiss", "wave"]
KNOCK_UP = 2.6                  # seconds lying down before she gets back up


def _star(x: float, y: float, r: float):
    from PySide6.QtGui import QPolygonF
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(QPointF(x + math.cos(a) * rr, y + math.sin(a) * rr))
    return QPolygonF(pts)
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
    knocked = Signal()               # a hard hit knocked her over
    cute = Signal(str)               # she did something cute (jump, twirl, nya, heart, kiss, wave)
    got_up = Signal()                # ...and she's back on her feet
    held_done = Signal(str)          # she put down the pet / toy she was hugging
    sick = Signal(str)               # shaken too hard: "queasy", "puke", "better"
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
        self._last_brush_t = 0.0
        self.sulking = False
        self.user_pose: str | None = None    # "sit" while you've told her to sit
        self._spin_left = 0.0
        self._press_local = QPointF()
        self._pulling = False
        self._manipulating = False
        self._broke_free = False
        self._lift_t = 0.0
        self._free_after = 1e9
        self.gestures = GestureListener(self)
        self.gestures.gesture.connect(self._on_gesture)
        self.gestures.squeeze_started.connect(self._on_squeeze_start)
        self.mood_label = "neutral"
        self.expressions = ExpressionEngine()
        self._prev_pos = (0.0, 0.0)
        self._prev_vel = (0.0, 0.0)
        self._acc = (0.0, 0.0)
        self.angry_on = False
        self._hold_left = 0.0
        self._walk_mul = 1.0
        self._rot_press: QPointF | None = None
        self._rot_base = (0.0, 0.0)
        self._rot_moved = False
        self._suppress_menu = False
        self._pose_left = 0.0
        self._jump_prep: tuple[float, float] | None = None
        self.watching = ""           # coding / working: she peers at your screen
        self.singing = False
        self.music_on = False
        self._shake = 0.0            # how hard she's been shaken (decays)
        self._shake_dir = 0
        self._shake_prev: tuple[float, QPointF] | None = None
        self._queasy_said = False
        self._puke_t = 0.0           # > 0 while she's throwing up
        self._puddle_t = 0.0
        self._puddle_x = 0.0
        self._splashed = False
        self._splash_x = 0.0

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
            if emotion == "squeezed":   # squished: eyes squeezed shut, pouty lips
                self.ctx.expr = self.expressions.current = build("closed", "pout")
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
        if state != KNOCK:
            self.ctx.knock = 0.0
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
        if self.sulking or self.user_pose == "sit":
            self._set_state(SIT, duration=1e9)
            return
        if self.user_pose:                 # holding the pose you asked for
            self._set_state(IDLE, duration=1e9)
            return
        self._walk_mul = 1.0
        if self.angry_on:
            # angry: stomps off a few steps (away from your cursor), or stands there, arms crossed, "hmph!"
            if random.random() < 0.45 and not (self.busy or self.talking):
                cur = QCursor.pos().x()
                away = -1 if cur > self.px + self.width() / 2 else 1
                self.target_x = max(left, min(right, self.px + away * random.uniform(80, 220)))
                self.ctx.facing_left = self.target_x < self.px
                self._walk_mul = 1.7
                self._set_state(WALK, duration=8)
            else:
                self._set_state(IDLE, duration=random.uniform(3, 6))
            return
        if getattr(self, "in_activity", False):     # busy with an activity: no random hops / wandering off
            self._set_state(IDLE, duration=2.0)
            return
        cute_chance = 0.28 if self.mood_label in ("happy", "playful") else 0.1
        if not (self.busy or self.talking or self.ctx.music) and random.random() < cute_chance:
            self.do_cute(random.choice(CUTE_ACTIONS))
            self.next_action = random.uniform(4, 8)
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
                ctx.emotion = "angry" if self.angry_on else "neutral"
        elif self.angry_on and ctx.emotion == "neutral":
            ctx.emotion = "angry"
        ctx.angry += ((1.0 if self.angry_on else 0.0) - ctx.angry) * min(1.0, dt * 3)
        ctx.lie += ((1.0 if ctx.pose == "lie" and self.state in (IDLE, REACT, PET) else 0.0) - ctx.lie) * min(1.0, dt * 4)
        self._update_sick(dt)
        # dancing to your music: a beat clock, and a groove that matches the song's mood
        self.ctx.beat_ph += dt * max(60.0, min(180.0, getattr(self, "bpm", 0.0) or 110.0)) / 60.0 * math.tau
        grooving = (self.music_on and not self.singing and self.ctx.groove and not self.user_pose
                    and not ctx.holding and self.state in (IDLE, REACT, PET) and ctx.angry < 0.3
                    and getattr(ctx, "sick", 0.0) < 0.3)
        peering = (self.watching and not grooving and not self.user_pose and not ctx.holding
                   and self.state in (IDLE, REACT, PET) and ctx.angry < 0.3 and not getattr(self, "in_activity", False))
        if peering and (not ctx.pose or ctx.pose.startswith("peer_")):
            scr = QGuiApplication.screenAt(self.global_rect().center()) or QGuiApplication.primaryScreen()
            ctx.pose = "peer_l" if scr.availableGeometry().center().x() > self.px + self.width() / 2 else "peer_r"
        elif ctx.pose.startswith("peer_") and not peering:
            ctx.pose = ""
        if grooving and (not ctx.pose or ctx.pose.startswith("groove_") or ctx.pose.startswith("peer_")):
            ctx.pose = f"groove_{self.ctx.groove}"
        elif ctx.pose.startswith("groove_") and not grooving:
            ctx.pose = ""
        if self._jump_prep is not None:                 # crouch... then spring up
            left_t, power = self._jump_prep
            left_t -= dt
            ctx.land = max(ctx.land, 0.8)
            if left_t <= 0 or self.state in (DRAG, FALL, KNOCK, SLEEP):
                self._jump_prep = None
                if self.state not in (DRAG, FALL, KNOCK, SLEEP):
                    self._launch(power)
            else:
                self._jump_prep = (left_t, power)
        else:
            ctx.land = max(0.0, ctx.land - dt * 1.5)
        if self.user_pose and self.user_pose != "sit":
            self._pose_left -= dt
            if self._pose_left <= 0:
                self.stand_up()
        if ctx.holding:
            self._hold_left -= dt
            if self._hold_left <= 0 or self.state == KNOCK:
                self.release_hold()
        ctx.pet_level = max(0.0, ctx.pet_level - dt * 0.5)
        ctx.beat = max(0.0, ctx.beat - dt * 4.0)
        # struggling settles to a steady wriggle while she's held, and stops once she's down
        held = time.monotonic() - self._lift_t if self.state == DRAG else 0.0
        base = (0.4 + min(0.5, held * 0.12) if ctx.grab else 0.4) if self.state == DRAG else 0.0
        ctx.struggle += (base - ctx.struggle) * min(1.0, dt * (0.8 if self.state == DRAG else 3.0))
        if self.state == DRAG and ctx.grab and held > self._free_after:
            self._break_free()
        if self._spin_left > 0:          # twirl: one full turn, eased
            self._spin_left = max(0.0, self._spin_left - dt)
            k = 1.0 - self._spin_left / TWIRL_TIME
            ctx.spin = 360.0 * (k * k * (3 - 2 * k)) if self._spin_left > 0 else 0.0
        if ctx.jump and self.state not in (FALL, DRAG):
            ctx.jump = False
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
                if impact > 350:                         # knees bend to absorb the landing
                    self.ctx.land = max(self.ctx.land, min(1.0, impact / 1500))
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
                    step = self.cfg.walk_speed * self._walk_mul * dt * (-1 if self.ctx.facing_left else 1)
                    self.px += step
                    if (self.ctx.facing_left and self.px <= self.target_x) or \
                            (not self.ctx.facing_left and self.px >= self.target_x) or \
                            self.px <= left or self.px >= right:
                        self.px = max(left, min(right, self.px))
                        self._set_state(IDLE, duration=random.uniform(2, 5))
            elif st == KNOCK:
                self._update_knock()
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
            if ctx.pose.startswith("peer_"):              # she looks at your work, not the cursor
                scr = QGuiApplication.screenAt(self.global_rect().center()) or QGuiApplication.primaryScreen()
                cur = QPointF(self.mapFromGlobal(scr.availableGeometry().center()))
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
        if self.ctx.holding:
            self._paint_held(p)
        if getattr(self.ctx, "sick", 0.0) > 0.05 or self._puke_t > 0:
            self._paint_sick(p)
        if self.state == KNOCK and 0.25 < self.ctx.state_t < KNOCK_UP:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = self._head_rect_local()
            cx, cy = r.center().x(), r.top() + r.height() * 0.15
            for i in range(3):          # dizzy stars circling her head
                a = self.ctx.t * 4.5 + i * 2.094
                x, y = cx + math.cos(a) * r.width() * 0.55, cy + math.sin(a) * r.height() * 0.18
                p.setPen(QColor("#e0a800"))
                p.setBrush(QColor("#ffe066"))
                p.drawPolygon(_star(x, y, 6.5))
        p.end()

    # ----------------------------------------------------------------- shaken too hard: dizzy -> rainbow puke
    def _detect_shake(self, now: float, gp: QPointF) -> None:
        if self._shake_prev is not None:
            t0, p0 = self._shake_prev
            dt = max(1e-3, now - t0)
            d = gp - p0
            ax = d.x() if abs(d.x()) >= abs(d.y()) else d.y()
            speed = abs(ax) / dt
            if speed > 450:
                direction = 1 if ax > 0 else -1
                if self._shake_dir and direction != self._shake_dir:      # a back-and-forth swing
                    self._shake = min(4.0, self._shake + 0.16 + min(0.3, speed / 9000))
                self._shake_dir = direction
        self._shake_prev = (now, gp)

    def _update_sick(self, dt: float) -> None:
        ctx = self.ctx
        held = self.state == DRAG
        self._shake = max(0.0, self._shake - dt * (0.12 if held else 0.3))
        target = max(0.0, min(1.0, (self._shake - 0.7) / 1.3))
        if self._puke_t > 0:
            target = 1.0
        ctx.sick = getattr(ctx, "sick", 0.0) + (target - getattr(ctx, "sick", 0.0)) * min(1.0, dt * 3)
        if self._shake > 1.4 and not self._queasy_said:
            self._queasy_said = True
            self.sick.emit("queasy")
        if self._shake > 2.3 and self.state in (IDLE, REACT, PET, SIT, WALK) and self._puke_t <= 0:
            self._start_puke()                       # back on the ground after being shaken silly: bleh
        if self._puke_t > 0:
            self._puke_t -= dt
            if not self._splashed and 3.2 - self._puke_t > 0.62:      # it reaches the taskbar
                self._splashed = True
                self._splash_x = self.px + self._head_rect_local().center().x()
                self.sick.emit("splash")
            ctx.mouth = 0.75 if 0.5 < self._puke_t < 2.6 else ctx.mouth
            if self._puke_t <= 0:
                self._shake = 0.0
                self._queasy_said = False
                if ctx.pose == "sick":
                    ctx.pose = getattr(self, "_pose_before", "") or ""
                self.set_emotion("pout", 5.0)
                self.sick.emit("better")
        self._puddle_t = max(0.0, self._puddle_t - dt)

    def _start_puke(self) -> None:
        self._puke_t = 3.2
        self._splashed = False
        self._puddle_t = 0.0
        self._puddle_x = self.width() / 2
        self._pose_before = self.ctx.pose
        self.ctx.pose = "sick"
        self._set_state(REACT, duration=3.4)
        self.set_emotion("hurt", 4.0)
        self.sick.emit("puke")

    def _paint_sick(self, p: QPainter) -> None:
        ctx = self.ctx
        sick = getattr(ctx, "sick", 0.0)
        hr = self._head_rect_local()
        cx, cy, hw, hh = hr.center().x(), hr.center().y(), hr.width(), hr.height()
        t = ctx.t
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if sick > 0.05 and self._puke_t <= 0.4:
            # green-faced, swirly-eyed, a sweat drop
            g = QRadialGradient(QPointF(cx, cy + hh * 0.18), hw * 0.42)
            g.setColorAt(0, QColor(110, 200, 90, int(80 * sick)))
            g.setColorAt(1, QColor(110, 200, 90, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(cx, cy + hh * 0.18), hw * 0.42, hh * 0.3)
            if sick > 0.45:
                for sx in (-1, 1):
                    ex, ey = cx + sx * hw * 0.16, cy + hh * 0.08
                    path = QPainterPath()
                    for i in range(40):
                        a = i / 39 * math.tau * 2.2 + t * 7 * sx
                        r = 0.6 + i / 39 * hw * 0.075
                        pt = QPointF(ex + math.cos(a) * r, ey + math.sin(a) * r)
                        path.moveTo(pt) if i == 0 else path.lineTo(pt)
                    p.setPen(QPen(QColor(60, 40, 80, int(230 * sick)), 1.4))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(140, 200, 255, int(200 * sick)))
            p.drawEllipse(QPointF(cx + hw * 0.36, cy - hh * 0.05 + (t * 20) % 8), 2.6, 3.6)
        floor = self._feet_y()
        k = 3.2 - self._puke_t                              # time since it started
        heaving = 0.55 < k < 1.35 or 1.75 < k < 2.5        # two heaves
        if self._puke_t > 0 and heaving:
            mx, my = cx, cy + hh * 0.42
            body, dark = QColor(204, 190, 96, 235), QColor(150, 132, 52, 240)
            path = QPainterPath()
            left, right = [], []
            steps = 18
            for i in range(steps + 1):
                f = i / steps
                y = my + (floor - my) * f
                x = mx + math.sin(f * 7 + t * 14) * 1.8 * f + f * f * hw * 0.04
                wdt = 2.2 + f * 3.2 + math.sin(t * 25 + f * 9) * 0.6
                left.append(QPointF(x - wdt, y))
                right.append(QPointF(x + wdt, y))
            path.moveTo(left[0])
            for q in left[1:]:
                path.lineTo(q)
            for q in reversed(right):
                path.lineTo(q)
            path.closeSubpath()
            p.setPen(QPen(dark, 1.0))
            p.setBrush(body)
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(7):                              # lumpy bits flowing down
                f = (t * 1.7 + i / 7) % 1.0
                x = mx + math.sin(f * 7 + t * 14) * 1.8 * f + (i % 3 - 1) * 1.6
                p.setBrush(QColor(176, 150, 64) if i % 2 else QColor(232, 214, 140))
                p.drawEllipse(QPointF(x, my + (floor - my) * f), 1.6 + (i % 3) * 0.5, 1.3)
            for i in range(6):                              # splashing on the taskbar
                ph = (t * 2.6 + i / 6) % 1.0
                dx = (i - 2.5) * 4.5 * ph * 2
                p.setBrush(QColor(204, 190, 96, int(220 * (1 - ph))))
                p.drawEllipse(QPointF(mx + dx, floor - 2 - math.sin(ph * math.pi) * 9), 1.8, 1.8)

    def _feet_y(self) -> float:
        try:
            return float(self.sprites.feet_y()) if hasattr(self.sprites, "feet_y") else self.height() - 3.0
        except Exception:
            return self.height() - 3.0

    def set_singing(self, on: bool) -> None:
        self.singing = on
        self.ctx.music = on or self.music_on
        if on and self.state == WALK:
            self._set_state(IDLE, duration=6)

    def _paint_held(self, p: QPainter) -> None:
        from .companions import draw_thing
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hr = self._head_rect_local()
        cx, cy = self.width() / 2, hr.bottom() + hr.height() * 0.95
        s = getattr(self.sprites, "_zones", None)
        if s is not None and hasattr(self.sprites, "project"):
            try:
                z = {n: self.sprites.project(pt) for n, pt, _r in self.sprites._zones()}
                if "chest" in z:
                    cx, cy = z["chest"][0], z["chest"][1] + hr.height() * 0.35
            except Exception:
                pass
        size = hr.height() * (1.05 if self.ctx.holding == "teddy" else 1.15)
        k = self.ctx.holding
        side = 1.0 if self.ctx.pose.endswith("_l") else -1.0 if self.ctx.pose.endswith("_r") else 1.0
        if k == "can":
            from .companions import draw_can
            hp = self.sprites.hands_point("left" if side > 0 else "right") if hasattr(self.sprites, "hands_point") else None
            if hp is None:
                hp = (cx + side * hr.width() * 0.5, cy)
            draw_can(p, hp[0], hp[1], side, hr.height() * 0.6, self.ctx.t, self._feet_y() - 20)
            return
        if k in ("mop", "spray"):
            from .companions import draw_mop, draw_spray
            which = ("left" if side > 0 else "right") if k == "spray" else None
            hp = self.sprites.hands_point(which) if hasattr(self.sprites, "hands_point") else None
            if hp is None:
                hp = (cx + side * hr.width() * 0.5, cy)
            if k == "mop":
                draw_mop(p, hp[0], hp[1], self.width() / 2 + side * hr.width() * 0.95, self._feet_y() - 1,
                         hr.height(), self.ctx.t)
            else:
                draw_spray(p, hp[0], hp[1], side, hr.height() * 0.55, self.ctx.t)
            return
        hp = self.sprites.hands_point() if hasattr(self.sprites, "hands_point") else None
        if hp is not None:               # in her hands
            k = self.ctx.holding
            drop = {"tea": 0.05, "book": 0.12, "ball": 0.3}.get(k, 0.32)
            cx, cy = hp[0], hp[1] + size * drop
        bob = math.sin(self.ctx.t * 2.2) * 1.5
        draw_thing(p, self.ctx.holding, cx, cy + bob, size)

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
    def _approach(self) -> tuple[float, float]:
        """Cursor speed (px/s) and horizontal velocity just before a press."""
        now = time.perf_counter()
        pts = [(t, q) for t, q in self._cursor_hist if now - t <= 0.09]
        if len(pts) < 2:
            return 0.0, 0.0
        (t0, p0), (t1, p1) = pts[0], pts[-1]
        dt = max(1e-3, t1 - t0)
        return (p1 - p0).manhattanLength() / dt, (p1.x() - p0.x()) / dt

    def knock_down(self, direction: float = 1.0) -> None:
        """Too hard! She topples over, lies dizzy for a moment, then gets back up."""
        if self.state in (DRAG, FALL, SLEEP, KNOCK):
            return
        self.ctx.knock_dir = direction
        self._set_state(KNOCK, duration=KNOCK_UP + 1.0)
        self.set_emotion("hurt", KNOCK_UP + 1.5)
        self.knocked.emit()

    def _update_knock(self) -> None:
        t = self.ctx.state_t
        if t < 0.35:                                   # falling
            k = t / 0.35
            self.ctx.knock = k * k
        elif t < KNOCK_UP:                             # lying there, a little bounce on impact
            self.ctx.knock = 1.0 - max(0.0, math.sin((t - 0.35) * 18) * math.exp(-(t - 0.35) * 8)) * 0.15
        elif t < KNOCK_UP + 0.8:                       # getting back up
            k = (t - KNOCK_UP) / 0.8
            self.ctx.knock = 1.0 - k * k * (3 - 2 * k)
        else:
            self.ctx.knock = 0.0
            self._set_state(IDLE, duration=3.0)
            self.set_emotion("pout", 4.0)
            self.got_up.emit()

    def _on_gesture(self, kind: str, zone: str) -> None:
        if self.state in (DRAG, FALL):
            return
        if self.state == KNOCK:
            if kind in (DOUBLE, STROKE) and self.ctx.state_t < KNOCK_UP:
                self.ctx.state_t = KNOCK_UP           # petting helps her back up
            return
        if kind == HARD:                               # squeezed far too hard: she falls over
            self.touch(zone, "rough")
            cx = self.width() / 2
            self.knock_down(-1.0 if self._press_local.x() > cx else 1.0)
            return
        kinds = {CLICK: "gentle", HARD: "rough", SQUEEZE: "squeeze", MOVE: "move", PULL: "tug"}
        k = kinds.get(kind, "pet")
        if k == "pet" and zone in ("hair_l", "hair_r"):
            zone = "head"                    # stroking her hair = headpats
        self.touch(zone, k)

    def _on_squeeze_start(self, zone: str) -> None:
        """Holding still on a part: squish it right away (the reaction comes when you let go)."""
        if hasattr(self.sprites, "squeeze"):
            self.sprites.squeeze(self._press_local)
        self.set_emotion("flustered" if zone in ("chest", "skirt") else "squeezed", 2.5)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.MiddleButton:
            self.double_clicked.emit()      # middle click opens / closes the chat
            return
        if e.button() == Qt.MouseButton.RightButton:
            # right-drag = rotate her (left / right spins her round, up / down leans her); a plain right-click = menu
            self._rot_press = e.globalPosition()
            self._rot_base = (self.ctx.user_yaw, self.ctx.user_pitch)
            self._rot_moved = False
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        QToolTip.hideText()
        self.last_interaction = time.monotonic()
        self._pressed = True
        self._dragging = False
        self._press_global = e.globalPosition()
        self._grab_offset = e.globalPosition() - QPointF(self.px, self.py)
        self._press_zone = self._zone_at(e.position())
        self._press_local = e.position()
        self.gestures.press(self._press_zone, e.globalPosition())
        speed, vx = self._approach()
        if speed > SMACK_SPEED and self.state not in (DRAG, FALL, KNOCK):
            # the cursor slammed into her: a smack knocks her over
            self.gestures.cancel()
            self.touch(self._press_zone, "rough")
            self.knock_down(1.0 if vx >= 0 else -1.0)

    def mouseMoveEvent(self, e) -> None:
        gp = e.globalPosition()
        self._cursor_hist.append((time.perf_counter(), gp))
        if self._rot_press is not None and e.buttons() & Qt.MouseButton.RightButton:
            d = gp - self._rot_press
            if not self._rot_moved and d.manhattanLength() > 5:
                self._rot_moved = True
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                QToolTip.hideText()
            if self._rot_moved:
                self.ctx.user_yaw = (self._rot_base[0] + d.x() * 0.7 + 180.0) % 360.0 - 180.0
                self.ctx.user_pitch = max(-35.0, min(35.0, self._rot_base[1] + d.y() * 0.25))
                if not getattr(self.sprites, "mirrors_itself", False):
                    self.ctx.facing_left = abs(self.ctx.user_yaw) > 90
                self.last_interaction = time.monotonic()
                if self.state == WALK:
                    self._set_state(IDLE, duration=5)
            return
        if self._pressed:
            mode = self.gestures.move(gp)
            if mode in ("move", "pull") and not self._dragging:
                # you're moving THIS part of her: head tilts, arm / leg follows, hair is pulled, body leans
                self._manipulating = True
                if hasattr(self.sprites, "manipulate"):
                    self.sprites.manipulate(self._press_zone, gp - self._press_global)
                elif hasattr(self.sprites, "tug"):
                    self.sprites.tug(self._press_zone, gp - self._press_global)
                return
            if mode == "lift" and not self._dragging and not self._broke_free:
                self._end_manipulation()
                self._start_lift(self._press_zone)
            if self._dragging:
                now = time.perf_counter()
                self._detect_shake(now, gp)
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

        # hovering: the cursor brushes her hair aside; stroking back and forth = petting
        if hasattr(self.sprites, "brush"):
            now = time.perf_counter()
            if self._last_hover is not None and now - self._last_brush_t < 0.2:
                vel = (e.position() - self._last_hover) / max(1e-3, now - self._last_brush_t)
            else:
                vel = QPointF()
            self._last_brush_t = now
            self.sprites.brush(e.position(), vel)
        zone = self._zone_at(e.position())
        soft = zone in STROKE_HOVER
        self.setCursor(Qt.CursorShape.OpenHandCursor if zone != "body" or soft else Qt.CursorShape.ArrowCursor)
        if zone in ("eye_l", "eye_r", "nose") and hasattr(self.sprites, "squint"):
            self.sprites.squint(True)   # she squints when your cursor comes near her eyes / nose
        if zone != self._hover_zone and self.cfg.touch_hints and self.state not in (DRAG, FALL):
            hint = ZONE_HINTS.get(_hint_group(zone))
            if hint:
                QToolTip.showText(gp.toPoint() + QPoint(14, 18), hint, self)
        if soft and zone == self._hover_zone and self._last_hover is not None:
            self._rub += (e.position() - self._last_hover).manhattanLength()
            if self._rub > 260 * self.cfg.scale:
                self._rub = 0.0
                self.gestures.hover_stroke(zone)
        self._hover_zone = zone
        self._last_hover = e.position()

    def _end_manipulation(self) -> None:
        if self._manipulating:
            self._manipulating = False
            if hasattr(self.sprites, "manipulate"):
                self.sprites.manipulate(self._press_zone, None)      # let go: the part springs back
            elif hasattr(self.sprites, "tug"):
                self.sprites.tug(self._press_zone, None)

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
            # hold her from near the top of the window so her body can hang below the grabbed limb,
            # keeping the grabbed limb on the side it was on so her body hangs centred
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
        if e.button() == Qt.MouseButton.RightButton:
            if self._rot_moved:
                self._suppress_menu = True        # it was a rotation, not a right-click
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self.touched.emit("body:turn")
            self._rot_press = None
            self._rot_moved = False
            return
        if e.button() != Qt.MouseButton.LeftButton or not self._pressed:
            return
        self._pressed = False
        if hasattr(self.sprites, "squeeze"):
            self.sprites.squeeze(None)                    # boing: the squish springs back
        self.gestures.release()
        self._broke_free = False
        if self._manipulating:
            self._end_manipulation()
            return
        if self._dragging:
            self._dragging = False
            self._shake_prev, self._shake_dir = None, 0
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
        if self._suppress_menu or self._rot_moved:
            self._suppress_menu = False
            return
        self.menu_requested.emit(e.globalPos())

    def leaveEvent(self, _e) -> None:
        if hasattr(self.sprites, "brush"):
            self.sprites.brush(None, None)
        self._last_hover = None
        self._hover_zone = None
        QToolTip.hideText()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ----------------------------------------------------------------- reactions
    def pet(self) -> None:
        self.touch("head", "pet")

    def poke(self) -> None:
        self.touch("body", "gentle")

    def touch(self, zone: str, kind: str = "gentle") -> None:
        """A touch on one part of her body.

        kind: "gentle" (click), "squeeze" (hold), "rough" (squeezed too long / too hard),
              "pet" (double click / stroking), "move" (dragging a part), "tug" (pulling hair / cloth).
        """
        self.last_interaction = time.monotonic()
        if self.state in (DRAG, FALL):
            return
        zone = zone or "body"
        if hasattr(self.sprites, "react") and kind not in ("tug", "move"):
            self.sprites.react(zone, kind)
        if kind == "rough":
            self._react_state(REACT, 1.8)
            self.set_emotion("hurt", 5.0)
        elif kind == "pet":
            self.ctx.pet_level = 1.0
            self._react_state(PET, 3.0)
            self.set_emotion(random.choice(["shy", "happy"]), 5.0)
            if zone == "head":
                self.petted.emit()
        elif kind == "squeeze":
            self.ctx.pet_level = max(self.ctx.pet_level, 0.6)
            self._react_state(REACT, 1.4)
            self.set_emotion("squeezed", 3.0)
        elif kind in ("move", "tug"):
            if self.state in (WALK, SLEEP):
                self._set_state(IDLE, duration=4.0)
        else:
            if zone in SOFT_ZONES:
                self.ctx.pet_level = max(self.ctx.pet_level, 0.3)
            self._react_state(REACT, 1.4)
            self.set_emotion("happy" if zone in SOFT_ZONES or zone == "head" else ZONE_EMOTION.get(zone, "surprised"), 3.5)
        self.touched.emit(f"{zone}:{kind}")
        if zone == "body":
            self.poked.emit()

    def _react_state(self, st: str, seconds: float) -> None:
        """React to a touch. While she's sitting or holding a pose she stays put (face, blush, hearts only)."""
        if self.state == SIT or (self.user_pose and self.state not in (WALK, SLEEP)):
            self.next_action = max(self.next_action, seconds)
            return
        self._set_state(st, duration=seconds)

    def act_pose(self, name: str, seconds: float) -> bool:
        """A pose she takes on her own for an activity (petting her pet, reading...), for `seconds`."""
        if self.do_pose(name):
            self._pose_left = seconds
            return True
        return False

    def do_pose(self, name: str) -> bool:
        """Hold a pose you asked for (see poses.HELD). Returns False if she can't right now."""
        if name == "stand":
            self.stand_up()
            return True
        if name == "sit":
            self.ctx.pose = ""
            self.sit_down()
            return True
        if self.state in (DRAG, FALL, KNOCK):
            return False
        self.user_pose = name
        self.ctx.pose = name
        self._pose_left = 300.0 if name == "lie" else 150.0
        self._spin_left = 0.0
        self._set_state(IDLE, duration=1e9)
        return True

    # ----------------------------------------------------------------- poses you control / cute actions
    def wheelEvent(self, e) -> None:
        """Mouse wheel over her: turn her around."""
        steps = e.angleDelta().y() / 120.0
        if e.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
            self.ctx.user_pitch = max(-35.0, min(35.0, self.ctx.user_pitch + steps * 6.0))   # shift+wheel: lean
            return
        yaw = self.ctx.user_yaw + steps * 20.0
        self.ctx.user_yaw = (yaw + 180.0) % 360.0 - 180.0
        self.last_interaction = time.monotonic()
        if not getattr(self.sprites, "mirrors_itself", False) and abs(steps) >= 1:
            self.ctx.facing_left = abs(self.ctx.user_yaw) > 90      # 2D characters just flip
        if self.state == WALK:
            self._set_state(IDLE, duration=5)

    def rotate_by(self, degrees: float) -> None:
        self.ctx.user_yaw = (self.ctx.user_yaw + degrees + 180.0) % 360.0 - 180.0
        if not getattr(self.sprites, "mirrors_itself", False):
            self.ctx.facing_left = abs(self.ctx.user_yaw) > 90

    def face_me(self) -> None:
        self.ctx.user_yaw = 0.0
        self.ctx.user_pitch = 0.0
        if not getattr(self.sprites, "mirrors_itself", False):
            self.ctx.facing_left = False

    def sit_down(self) -> None:
        self.ctx.pose = ""
        self.user_pose = "sit"
        if self.state not in (DRAG, FALL, KNOCK):
            self._set_state(SIT, duration=1e9)

    def stand_up(self) -> None:
        self.user_pose = None
        self.ctx.pose = ""
        if self.state in (SIT, SLEEP):
            self._set_state(REACT, duration=1.0)

    def jump(self, power: float = 1.0) -> None:
        """A real jump: up she goes (gravity brings her down), arms up, 'Yay~!'."""
        if self.state in (DRAG, FALL, KNOCK, SLEEP):
            return
        if self._jump_prep is not None:
            return
        self.user_pose = None
        self.ctx.pose = ""
        self._jump_prep = (0.17, power)   # anticipation: a quick crouch first
        self.set_emotion("happy", 3.0)

    def _launch(self, power: float) -> None:
        self.ctx.jump = True
        self.vy = -950.0 * power
        self.vx = 0.0
        self.py -= 2                      # leave the ground so physics takes over
        self._set_state(FALL)

    def twirl(self) -> None:
        if self.state in (DRAG, FALL, KNOCK):
            return
        self._spin_left = TWIRL_TIME
        self._set_state(REACT, duration=TWIRL_TIME + 0.4)
        self.set_emotion("happy", 3.0)

    def do_cute(self, name: str) -> None:
        """jump, twirl, nya, heart, kiss, wave, hop_hop."""
        if self.state in (DRAG, FALL, KNOCK, SLEEP):
            return
        s = self.sprites
        if name == "jump":
            self.jump()
        elif name == "hop_hop":
            self.jump(0.55)
            QTimer.singleShot(800, lambda: self.jump(0.55))
        elif name == "twirl":
            self.twirl()
        elif name in ("nya", "heart", "kiss", "wave"):
            g = "wave_l" if name == "wave" else name
            if hasattr(s, "start_gesture"):
                self._set_state(IDLE, duration=4.0)
                s.start_gesture(g)
            else:
                self.jump(0.5)               # 2D characters: a happy little hop instead
            self.set_emotion({"nya": "playful", "heart": "shy", "kiss": "shy"}.get(name, "happy"), 3.5)
        self.cute.emit(name)

    # ----------------------------------------------------------------- anger, pets & toys
    def set_angry(self, on: bool) -> None:
        """Angry: arms crossed, turns her back on you, steam + anger vein, stomps around, won't do cute stuff."""
        self.angry_on = on
        if on:
            self.set_emotion("angry", 4.0)
            if self.state in (SIT, WALK) and self.user_pose != "sit":
                self._set_state(IDLE, duration=3.0)
        else:
            self._walk_mul = 1.0
            if self.ctx.emotion == "angry":
                self.ctx.emotion = "neutral"

    def hold(self, kind: str, seconds: float = 14.0) -> None:
        """Hug a pet / toy for a while."""
        self.ctx.holding = kind
        self._hold_left = seconds
        if self.state == WALK:
            self._set_state(IDLE, duration=seconds)
        self.ctx.pet_level = max(self.ctx.pet_level, 0.8)
        self.set_emotion("happy", 4.0)

    def release_hold(self) -> None:
        kind = self.ctx.holding
        if kind:
            self.ctx.holding = ""
            self._hold_left = 0.0
            self.held_done.emit(kind)

    def kick(self) -> None:
        if self.state not in (DRAG, FALL, KNOCK, SLEEP):
            self._set_state(REACT, duration=0.8)
            if hasattr(self.sprites, "react"):
                self.sprites.react("legs", "tap")

    def pet_animal(self) -> None:
        """She pets her pet: happy closed eyes, hearts."""
        if self.state not in (DRAG, FALL, KNOCK, SLEEP):
            self._set_state(REACT, duration=2.0)
            self.ctx.pet_level = 0.8
            self.set_emotion("happy", 3.5)

    def walk_to(self, gx: float) -> None:
        """Walk so that her centre ends up at global x."""
        left, right, _ = self._floor()
        tx = max(left, min(right, gx - self.width() / 2))
        if abs(tx - self.px) < 10 or self.state in (DRAG, FALL, SLEEP, KNOCK):
            return
        self.target_x = tx
        self.ctx.facing_left = tx < self.px
        self._set_state(WALK, duration=30)

    # ----------------------------------------------------------------- attention-seeking moves
    def walk_to_cursor(self) -> float:
        """Walk along the taskbar to just under your cursor. Returns roughly how long it takes."""
        left, right, _ = self._floor()
        cur = QCursor.pos()
        tx = max(left, min(right, cur.x() - self.width() / 2))
        if abs(tx - self.px) < 25 or self.state in (DRAG, FALL, SLEEP):
            return 0.0
        self.target_x = tx
        self.ctx.facing_left = tx < self.px
        self.busy = False
        self._set_state(WALK, duration=60)
        return abs(tx - self.px) / max(10.0, self.cfg.walk_speed)

    def hop(self) -> None:
        if self.state not in (DRAG, FALL, SLEEP):
            self._set_state(REACT, duration=1.0)

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
        self.music_on = on
        if beat > 0.3:                                    # a beat just hit: pull the dance clock onto it
            ph = self.ctx.beat_ph % math.tau
            self.ctx.beat_ph -= (ph if ph < math.pi else ph - math.tau) * 0.35
        on = on or self.singing
        self.ctx.music = on
        self.ctx.beat = max(self.ctx.beat, beat)
        if on and self.state == WALK:
            self._set_state(IDLE, duration=8)
