"""Long-term memory in SQLite (%APPDATA%\\DesktopWaifu\\waifu_memory.db).

Tables:
  messages  every chat line (role: user / model), so conversations survive restarts
  facts     durable things she learned about you ("likes lo-fi", "has an exam on Friday")
  stats     relationship counters and mood (key/value)
  events    milestones and notable moments (first meeting, first headpat, songs shared...)
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime

from .config import APP_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, ts REAL, role TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, ts REAL, category TEXT, fact TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, detail TEXT);
"""


class Memory:
    def __init__(self, path=None):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if path is None:
            path = APP_DIR / "waifu_memory.db"
            old = APP_DIR / "memory.db"
            if old.exists() and not path.exists():
                old.rename(path)          # migrate from the earlier file name
        self.path = path
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()
        if self.get("first_met") is None:
            self.set("first_met", time.time())
            self.event("milestone", "We met for the first time.")

    # ------------------------------------------------------------------ stats
    def get(self, key: str, default=None):
        with self._lock:
            row = self.db.execute("SELECT value FROM stats WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        v = row[0]
        return float(v) if _is_num(v) else v

    def set(self, key: str, value) -> None:
        with self._lock:
            self.db.execute("INSERT INTO stats(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, str(value)))
            self.db.commit()

    def bump(self, key: str, by: float = 1) -> float:
        v = float(self.get(key, 0.0)) + by
        self.set(key, v)
        return v

    # ------------------------------------------------------------------ messages
    def add_message(self, role: str, text: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO messages(ts, role, text) VALUES(?, ?, ?)", (time.time(), role, text))
            self.db.commit()

    def recent_messages(self, n: int) -> list[tuple[str, str]]:
        with self._lock:
            rows = self.db.execute("SELECT role, text FROM messages ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return list(reversed(rows))

    def message_count(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    # ------------------------------------------------------------------ facts
    def add_fact(self, fact: str, category: str = "general") -> bool:
        fact = " ".join(fact.split()).strip(" .")
        if len(fact) < 3:
            return False
        with self._lock:
            cur = self.db.execute("INSERT OR IGNORE INTO facts(ts, category, fact) VALUES(?, ?, ?)",
                                  (time.time(), category, fact))
            self.db.commit()
            return cur.rowcount > 0

    def facts(self, limit: int = 60) -> list[tuple[int, str, str]]:
        with self._lock:
            return self.db.execute("SELECT id, category, fact FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def delete_fact(self, fact_id: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM facts WHERE id=?", (fact_id,))
            self.db.commit()

    # ------------------------------------------------------------------ events
    def event(self, kind: str, detail: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO events(ts, kind, detail) VALUES(?, ?, ?)", (time.time(), kind, detail))
            self.db.commit()

    def recent_events(self, n: int = 8) -> list[tuple[float, str, str]]:
        with self._lock:
            rows = self.db.execute("SELECT ts, kind, detail FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return list(reversed(rows))

    # ------------------------------------------------------------------ prompt
    def summary(self) -> str:
        """Text block for the system prompt."""
        first = float(self.get("first_met", time.time()))
        days = int((time.time() - first) // 86400)
        lines = [f"You first met {days} day(s) ago ({datetime.fromtimestamp(first):%d %b %Y}). "
                 f"Headpats so far: {int(self.get('pats', 0.0))}, held hands: {int(self.get('hand_holds', 0.0))}, "
                 f"times hurt: {int(self.get('hits', 0.0))}, messages exchanged: {self.message_count()}."]
        facts = self.facts(50)
        if facts:
            lines.append("Things you remember about them:")
            lines += [f"- {f}" for _, _, f in reversed(facts)]
        habits = self.habits()
        if habits:
            lines.append("What you've learned about their habits:")
            lines += [f"- {h}" for h in habits]
        ev = self.recent_events(6)
        if ev:
            lines.append("Recent moments together:")
            lines += [f"- {datetime.fromtimestamp(ts):%d %b %H:%M}: {d}" for ts, _, d in ev]
        return "\n".join(lines)

    def habits(self) -> list[str]:
        """Patterns learned from how they use the PC and treat her."""
        out = []
        with self._lock:
            rows = self.db.execute("SELECT key, value FROM stats").fetchall()
        st = {k: float(v) for k, v in rows if _is_num(v)}
        hours = sorted(((st[k], int(k[8:])) for k in st if k.startswith("active_h")), reverse=True)[:3]
        if hours and hours[0][0] >= 3:
            out.append("They're usually at the PC around " + ", ".join(f"{h:02d}:00" for _, h in sorted(hours, key=lambda x: x[1])) + ".")
        names = {"touch_pet": "headpats and stroking", "touch_squeeze": "squishing you", "touch_gentle": "little pokes",
                 "touch_move": "moving you around", "touch_tug": "playing with your hair", "touch_rough": "being rough"}
        touches = sorted(((st.get(k, 0), v) for k, v in names.items()), reverse=True)
        fav = [f"{v} ({int(n)}x)" for n, v in touches if n >= 3][:3]
        if fav:
            out.append("Their favourite ways to interact with you: " + ", ".join(fav) + ".")
        tactics = {"approach_wave": "walking over and waving", "hop_call": "hopping and calling them",
                   "dance": "dancing cutely", "lonely_pout": "pouting that you're lonely",
                   "ask_question": "asking them questions"}
        scored = [((st.get(f"tactic_{t}_ok", 0) + 1) / (st.get(f"tactic_{t}_try", 0) + 2), d)
                  for t, d in tactics.items() if st.get(f"tactic_{t}_try", 0) >= 2]
        if scored:
            out.append("What best gets their attention: " + max(scored)[1] + ".")
        msgs = st.get("chat_msgs", 0)
        if msgs >= 10:
            out.append(f"They have chatted with you {int(msgs)} times.")
        return out

    def wipe(self) -> None:
        with self._lock:
            for t in ("messages", "facts", "events"):
                self.db.execute(f"DELETE FROM {t}")
            self.db.execute("DELETE FROM stats WHERE key NOT IN ('first_met')")
            self.db.commit()


def _is_num(v: str) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
