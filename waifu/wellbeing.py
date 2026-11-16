"""Looking after you: reminders for your schedule, breaks, water, eyes, bedtime, and watching you work.

Everything here is local. Activity comes from "seconds since the last key / mouse input"; "watch me work"
only reads the *program name* of the window you're using (e.g. Code.exe) -- no window titles, no
screenshots -- and only when you turn it on.
"""
from __future__ import annotations

import ctypes
import os
import re
import time
from datetime import datetime, timedelta

CODING = {"code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "devenv.exe", "pycharm64.exe",
          "idea64.exe", "webstorm64.exe", "clion64.exe", "rider64.exe", "goland64.exe", "phpstorm64.exe",
          "studio64.exe", "sublime_text.exe", "notepad++.exe", "gvim.exe", "nvim-qt.exe", "zed.exe",
          "windowsterminal.exe", "wt.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "eclipse.exe",
          "arduino ide.exe", "godot.exe", "unity.exe", "unrealeditor.exe", "claude.exe"}
HEAVY = {"excel.exe", "winword.exe", "powerpnt.exe", "onenote.exe", "photoshop.exe", "illustrator.exe",
         "afterfx.exe", "adobe premiere pro.exe", "resolve.exe", "blender.exe", "figma.exe", "acad.exe",
         "matlab.exe", "rstudio.exe", "obsidian.exe", "notion.exe", "krita.exe", "clipstudiopaint.exe",
         "solidworks.exe", "fusion360.exe", "inventor.exe", "revit.exe", "lightroom.exe", "audacity.exe",
         "fl64.exe", "ableton live 12 suite.exe", "reaper.exe"}


def foreground_program() -> str:
    """Lower-case exe name of the foreground window's program (e.g. 'code.exe'), or ''."""
    if os.name != "nt":
        return ""
    try:
        user32, k32, psapi = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.psapi
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return ""
        h = k32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = ctypes.c_ulong(520)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""
    return ""


def work_kind(exe: str) -> str:
    return "coding" if exe in CODING else "working" if exe in HEAVY else ""


# ------------------------------------------------------------------ reminders
_UNITS = {"sec": 1, "second": 1, "min": 60, "minute": 60, "hour": 3600, "hr": 3600, "day": 86400}


def _clock(h: int, m: int, ampm: str | None) -> tuple[int, int] | None:
    if ampm:
        ampm = ampm.lower().replace(".", "")
        if h == 12:
            h = 0
        if ampm.startswith("p"):
            h += 12
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h, m


def parse_reminder(text: str, now: datetime | None = None) -> dict | None:
    """'remind me at 5:30 pm to call mom' / 'remind me in 20 minutes to stretch' / 'remind me to X tomorrow at 9'
    / 'remind me every day at 8am to take my medicine' -> {"at": epoch, "text": "...", "repeat": "" | "daily"}."""
    now = now or datetime.now()
    low = text.strip().rstrip(".!?")
    m0 = re.match(r"^(?:hey\s+|please\s+|can you\s+|could you\s+)*remind me\s+(.*)$", low, re.I)
    if not m0:
        return None
    rest = m0.group(1)
    repeat = ""
    if re.search(r"\b(every ?day|daily|each day)\b", rest, re.I):
        repeat = "daily"
        rest = re.sub(r"\b(every ?day|daily|each day)\b", "", rest, flags=re.I)
    when = None
    task = rest
    m = re.search(r"\bin\s+(\d+(?:\.\d+)?|an?|half an?)\s*(sec(?:ond)?s?|min(?:ute)?s?|hours?|hrs?|days?)\b", rest, re.I)
    if m:
        qty = {"a": 1, "an": 1}.get(m.group(1).lower(), None)
        if qty is None:
            qty = 0.5 if m.group(1).lower().startswith("half") else float(m.group(1))
        unit = re.sub(r"s$", "", m.group(2).lower())
        unit = {"sec": "sec", "second": "sec", "min": "min", "minute": "min", "hr": "hour", "hour": "hour",
                "day": "day"}.get(unit, unit)
        when = now + timedelta(seconds=qty * _UNITS.get(unit, 60))
        task = rest[:m.start()] + rest[m.end():]
    else:
        m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?=\s|$)", rest, re.I)
        has_at = re.search(r"\bat\s+\d", rest, re.I)
        if m and (has_at or m.group(3) or m.group(2)):
            hm = _clock(int(m.group(1)), int(m.group(2) or 0), m.group(3))
            if hm:
                when = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
                tomorrow = re.search(r"\btomorrow\b", rest, re.I)
                if tomorrow or when <= now:
                    if not m.group(3) and not tomorrow and hm[0] < 12 and when.replace(hour=hm[0] + 12) > now:
                        when = when.replace(hour=hm[0] + 12)      # "at 5" in the afternoon means 5 pm
                    else:
                        when += timedelta(days=1)
                task = rest[:m.start()] + rest[m.end():]
                task = re.sub(r"\btomorrow\b", "", task, flags=re.I)
    if when is None:
        return None
    task = re.sub(r"\s+", " ", task).strip(" ,.")
    task = re.sub(r"^(to|that|about)\s+", "", task, flags=re.I)
    task = re.sub(r"\s+(at|on)$", "", task, flags=re.I)
    task = re.sub(r"\bmy\b", "your", task, flags=re.I)
    task = re.sub(r"\bI\b", "you", task)
    if not task:
        task = "the thing you asked me about"
    return {"at": when.timestamp(), "text": task, "repeat": repeat}


def describe(at: float, now: float | None = None) -> str:
    now = now or time.time()
    d = datetime.fromtimestamp(at)
    clock = d.strftime("%I:%M %p").lstrip("0")
    if at - now < 3600 and d.date() == datetime.now().date():
        mins = max(1, round((at - now) / 60))
        return f"in {mins} min ({clock})"
    day = "today" if d.date() == datetime.now().date() else "tomorrow" if \
        d.date() == (datetime.now() + timedelta(days=1)).date() else d.strftime("%a %d %b")
    return f"{day} at {clock}"


class Wellbeing:
    """Keeps time for her: due reminders, breaks, water, eyes, bedtime, and how long you've been focused."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.active = 0.0           # seconds of activity since your last real break
        self.since_water = 0.0
        self.since_eyes = 0.0
        self.since_night = 1e9
        self.focus = 0.0            # continuous coding / heavy work
        self.work = ""              # coding | working | ""
        self.break_snooze = 0.0
        self.since_tea = 0.0

    def tick(self, dt: float, idle: float) -> list[tuple[str, object]]:
        """Called every few seconds. Returns events: ("reminder", r), ("break", None), ("water", None),
        ("eyes", None), ("night", None), ("welcome_break", None), ("tea", None), ("watch", kind), ("unwatch", None)."""
        ev: list[tuple[str, object]] = []
        now = time.time()
        for r in list(self.cfg.reminders):
            if now >= r["at"]:
                ev.append(("reminder", dict(r)))
                if r.get("repeat") == "daily":
                    while r["at"] <= now:
                        r["at"] += 86400
                else:
                    self.cfg.reminders.remove(r)
                self.cfg.save()
        if idle > 300:                                   # you were away 5+ min: that counts as a break
            if self.active > 20 * 60:
                ev.append(("welcome_break", None))
            self.active = self.since_eyes = 0.0
            self.focus = 0.0
        elif idle < 120:
            self.active += dt
            self.since_water += dt
            self.since_eyes += dt
            self.since_tea += dt
        self.since_night += dt
        if self.cfg.health_reminders:
            if self.active > self.cfg.break_minutes * 60 and now > self.break_snooze:
                ev.append(("break", None))
                self.break_snooze = now + 10 * 60          # ask again in 10 min if you keep going
            if self.since_water > self.cfg.water_minutes * 60:
                self.since_water = 0.0
                ev.append(("water", None))
            if self.since_eyes > self.cfg.eye_minutes * 60:
                self.since_eyes = 0.0
                ev.append(("eyes", None))
            hour = datetime.now().hour
            if 1 <= hour < 5 and idle < 60 and self.since_night > 30 * 60:
                self.since_night = 0.0
                ev.append(("night", None))
        # watching you work (program names only, and only if you turned it on)
        if self.cfg.watch_work:
            kind = work_kind(foreground_program()) if idle < 20 else ""
            if kind:
                self.focus += dt
                if not self.work and self.focus > 45:
                    self.work = kind
                    ev.append(("watch", kind))
                if self.focus > 45 * 60 and self.since_tea > 45 * 60:
                    self.since_tea = 0.0
                    ev.append(("tea", None))
            elif self.work and idle > 60 or (self.work and not kind and idle < 5):
                self.work = ""
                self.focus = 0.0
                ev.append(("unwatch", None))
        elif self.work:
            self.work = ""
            ev.append(("unwatch", None))
        return ev

    def took_break(self) -> None:
        self.active = 0.0
        self.break_snooze = 0.0
