"""Time Capsule — long-horizon memory with a heartbeat.

Where Guardian Angel reacts in the 5-second window before you commit an action,
Time Capsule looks *forward across weeks*. When you say something goal-shaped or
emotionally loaded to Apex — "I want to quit by March", "I'm so done with this
job", "I'm going to start running" — it quietly bookmarks the statement with a
callback date. Days or weeks later, unprompted, it surfaces the callback:

    "Three weeks ago you said you wanted to start running — how's that going?"

Pipeline (mirrors Guardian's cheap-filter → one-model-call → spoken delivery):

  CAPTURE  (every ~60 s)
    read new user turns from turn_log → cheap regex pre-filter → on a hit, one
    Haiku call extracts {capture, kind, statement, callback_days} → dedup against
    pending capsules → INSERT into time_capsules (persistent SQLite).

  SURFACE  (every ~30 min + on boot)
    query capsules whose callback_date has passed → take the oldest, throttled to
    TIME_CAPSULE_MAX_PER_DAY → build a warm callback line → speak + tray notify →
    mark 'reminded'.

The model is only invoked when the regex pre-filter hits (rare), so cost stays
near zero. Capture latency of ~60 s is irrelevant — callbacks fire days out.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

import config
from agent import longterm


# ── Pre-filter ───────────────────────────────────────────────────────────────
# A cheap gate (no API call) that decides whether a user turn is even worth an
# extraction call. Intentionally generous — false positives are filtered out by
# the model; false negatives are silently lost, so we lean inclusive.

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december"
)

_PREFILTER = re.compile(
    r"\b("
    r"i\s+want\s+to|i\s+wanna|i'?m\s+gonna|i'?m\s+going\s+to|i\s+need\s+to|"
    r"i\s+will|i'?ll\s+(start|stop|begin|never|finally)|i\s+promise|i\s+should|"
    r"i\s+wish|i\s+hope\s+to|i\s+plan\s+to|someday|one\s+day|"
    r"my\s+goal|my\s+plan|i\s+resolve|i\s+swear|i'?m\s+determined|"
    r"i'?m\s+(so\s+)?(done|sick|tired|fed\s+up)\b|i\s+can'?t\s+(take|do|stand)|"
    r"i\s+hate\s+(my|this|that)|i'?m\s+quitting|i\s+quit\b|i'?m\s+leaving|"
    r"by\s+(next\s+)?(week|month|year|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|" + _MONTHS + r")"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class Capsule:
    id: int
    ts: float
    statement: str
    kind: str
    callback_date: float
    status: str  # pending | reminded


_VALID_KINDS = {"goal", "aspiration", "commitment", "concern", "reflection"}


# ── Storage (shares the longterm SQLite DB) ──────────────────────────────────

def _init_table() -> None:
    """Create the time_capsules table if it does not exist. Idempotent."""
    with longterm._conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS time_capsules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                statement TEXT NOT NULL,
                kind TEXT NOT NULL,
                callback_date REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                reminded_at REAL,
                embedding BLOB
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_caps_due "
            "ON time_capsules(status, callback_date)"
        )


# ── Extraction prompt ────────────────────────────────────────────────────────

_SYSTEM = (
    "You are Time Capsule, a quiet observer that bookmarks a person's intentions "
    "and feelings so they can be revisited later. You are given one thing the user "
    "just said to their assistant. Decide whether it expresses a genuine goal, "
    "commitment, aspiration, or strong feeling worth checking back on in the "
    "future — as opposed to a passing comment, a task for right now, or small talk.\n\n"
    "Respond with ONLY a JSON object, no prose:\n"
    '{"capture": true|false, '
    '"kind": "goal|aspiration|commitment|concern|reflection", '
    '"statement": "<a short clean first-person restatement, e.g. you want to start running>", '
    '"callback_days": <integer number of days until it is worth checking back>}\n\n'
    "Guidance: if the user names a date, set callback_days to land a few days "
    "after it. Otherwise: commitment≈7, goal≈14, aspiration≈30, concern≈3, "
    "reflection≈21. If it is not worth revisiting, return capture=false."
)


def _coerce_extraction(raw: str) -> Optional[dict]:
    """Parse the model's JSON reply defensively. Returns a clean dict or None."""
    if not raw:
        return None
    text = raw.strip()
    # Tolerate ```json fences or leading prose.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or not obj.get("capture"):
        return None
    statement = str(obj.get("statement", "")).strip()
    if not statement:
        return None
    kind = str(obj.get("kind", "")).strip().lower()
    if kind not in _VALID_KINDS:
        kind = "reflection"
    try:
        days = int(obj.get("callback_days", config.TIME_CAPSULE_DEFAULT_CALLBACK_DAYS))
    except (ValueError, TypeError):
        days = config.TIME_CAPSULE_DEFAULT_CALLBACK_DAYS
    # Clamp to something sane: at least a day out, at most a year.
    days = max(1, min(days, 365))
    return {"statement": statement, "kind": kind, "callback_days": days}


def _turn_text(content_json: str) -> str:
    """Pull the user's text out of a turn_log content_json blob."""
    try:
        obj = json.loads(content_json)
    except (ValueError, TypeError):
        return content_json if isinstance(content_json, str) else ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return str(obj.get("text") or obj.get("content") or "")
    if isinstance(obj, list):
        # Anthropic-style content blocks: [{"type":"text","text":"..."}]
        parts = []
        for b in obj:
            if isinstance(b, dict) and b.get("text"):
                parts.append(str(b["text"]))
            elif isinstance(b, str):
                parts.append(b)
        return " ".join(parts)
    return ""


def _humanize_age(seconds: float) -> str:
    """'three weeks ago' style phrasing for a callback line."""
    days = seconds / 86400.0
    if days < 1.5:
        return "yesterday" if days >= 0.75 else "earlier today"
    if days < 13:
        n = round(days)
        return f"{n} days ago"
    if days < 25:
        weeks = round(days / 7.0)
        return "a week ago" if weeks == 1 else f"{weeks} weeks ago"
    months = round(days / 30.0)
    return "a month ago" if months == 1 else f"{months} months ago"


# ── Time Capsule ─────────────────────────────────────────────────────────────

class TimeCapsule:
    def __init__(
        self,
        speak_fn: Callable[[str], None],
        tray_notify_fn: Callable[[str, str], None],
    ):
        self.speak = speak_fn
        self.tray_notify = tray_notify_fn

        self._last_scan = 0.0
        self._last_surface = 0.0
        # Skip everything already in the log so a restart doesn't replay history.
        self._last_turn_id = self._max_turn_id()
        self._surfaced_today: list[float] = []  # ts of recent surfacings (for daily cap)

        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="capsule")
        self._lock = threading.Lock()

        # Dashboard log (in memory; the server reads it).
        self._log: list[dict] = []
        self._log_lock = threading.Lock()

    # -- public ---------------------------------------------------------------

    def tick(self) -> None:
        """Called every ~15 s from the awareness loop. Self-rate-limits; non-blocking."""
        if not getattr(config, "TIME_CAPSULE_ENABLED", True):
            return
        now = time.time()
        if now - self._last_scan >= config.TIME_CAPSULE_SCAN_INTERVAL_SECONDS:
            self._last_scan = now
            self._executor.submit(self._safe, self._capture_scan)
        if now - self._last_surface >= config.TIME_CAPSULE_SURFACE_INTERVAL_SECONDS:
            self._last_surface = now
            self._executor.submit(self._safe, self._surface_due)

    def recent_capsules(self, limit: int = 10) -> list[dict]:
        """Recent capture/surface activity for the dashboard, newest first."""
        with self._log_lock:
            return list(reversed(self._log[-limit:]))

    def set_enabled(self, value: bool) -> None:
        config.TIME_CAPSULE_ENABLED = value

    # -- capture --------------------------------------------------------------

    def _capture_scan(self) -> None:
        rows = self._new_user_turns()
        if not rows:
            return
        for turn_id, _ts, content_json in rows:
            self._last_turn_id = max(self._last_turn_id, turn_id)
            text = _turn_text(content_json).strip()
            if len(text) < 12 or not _PREFILTER.search(text):
                continue
            extracted = self._extract(text)
            if extracted is None:
                continue
            if self._is_duplicate(extracted["statement"]):
                continue
            self._store(extracted["statement"], extracted["kind"], extracted["callback_days"])

    def _extract(self, text: str) -> Optional[dict]:
        model = getattr(config, "TIME_CAPSULE_MODEL", "claude-haiku-4-5")
        try:
            from agent import provider as _prov
            raw = _prov.complete(model, _SYSTEM, text[:1000], max_tokens=120)
        except Exception:
            return None  # fail-safe: never store on API error
        return _coerce_extraction(raw)

    def _is_duplicate(self, statement: str) -> bool:
        """True if a near-identical pending capsule already exists (cosine > 0.9)."""
        vec_blob = longterm._embed(statement)
        if vec_blob is None:
            # No embeddings available — fall back to exact-text match.
            with longterm._conn() as c:
                row = c.execute(
                    "SELECT 1 FROM time_capsules "
                    "WHERE status='pending' AND lower(statement)=lower(?) LIMIT 1",
                    (statement,),
                ).fetchone()
            return row is not None
        import numpy as _np

        query_vec = _np.frombuffer(vec_blob, dtype=_np.float32)
        with longterm._conn() as c:
            blobs = [
                r[0]
                for r in c.execute(
                    "SELECT embedding FROM time_capsules "
                    "WHERE status='pending' AND embedding IS NOT NULL"
                ).fetchall()
            ]
        if not blobs:
            return False
        return max(longterm._cosine_scores(query_vec, blobs), default=0.0) > 0.9

    def _store(self, statement: str, kind: str, callback_days: int) -> None:
        now = time.time()
        callback_date = now + callback_days * 86400
        embedding = longterm._embed(statement)
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO time_capsules (ts, statement, kind, callback_date, status, embedding) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (now, statement, kind, callback_date, embedding),
            )
        print(f"[TimeCapsule] captured ({kind}, +{callback_days}d): {statement}")
        self._record("captured", kind, statement, callback_date)

    # -- surface --------------------------------------------------------------

    def _surface_due(self) -> None:
        if not self._under_daily_cap():
            return
        now = time.time()
        with longterm._conn() as c:
            row = c.execute(
                "SELECT id, ts, statement, kind, callback_date, status FROM time_capsules "
                "WHERE status='pending' AND callback_date <= ? "
                "ORDER BY callback_date ASC LIMIT 1",
                (now,),
            ).fetchone()
        if row is None:
            return
        cap = Capsule(*row)
        line = self._callback_line(cap)
        self._deliver(cap, line)
        with longterm._conn() as c:
            c.execute(
                "UPDATE time_capsules SET status='reminded', reminded_at=? WHERE id=?",
                (now, cap.id),
            )
        with self._lock:
            self._surfaced_today.append(now)

    def _under_daily_cap(self) -> bool:
        cap = getattr(config, "TIME_CAPSULE_MAX_PER_DAY", 2)
        cutoff = time.time() - 86400
        with self._lock:
            self._surfaced_today = [t for t in self._surfaced_today if t >= cutoff]
            return len(self._surfaced_today) < cap

    def _callback_line(self, cap: Capsule) -> str:
        age = _humanize_age(time.time() - cap.ts)
        return f"{age.capitalize()} you said you wanted to {cap.statement}. How's that going?"

    def _deliver(self, cap: Capsule, line: str) -> None:
        print(f"[TimeCapsule] surfacing #{cap.id}: {line}")
        try:
            self.tray_notify("Time Capsule", line)
        except Exception:
            pass
        try:
            self.speak(line)
        except Exception:
            pass
        # Cross-device reach (phone/PWA/other tabs) via the notification hub.
        try:
            from agent import notify as _notify
            _notify.notify("Time Capsule", line, kind="timecapsule",
                           priority="normal", url="/?tab=telemetry",
                           dedup_key=f"capsule:{cap.id}")
        except Exception:
            pass
        self._record("surfaced", cap.kind, cap.statement, cap.callback_date, verdict=line)

    # -- helpers --------------------------------------------------------------

    def _max_turn_id(self) -> int:
        try:
            with longterm._conn() as c:
                row = c.execute("SELECT MAX(id) FROM turn_log").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            return 0

    def _new_user_turns(self) -> list[tuple]:
        try:
            with longterm._conn() as c:
                return c.execute(
                    "SELECT id, ts, content_json FROM turn_log "
                    "WHERE role='user' AND id > ? ORDER BY id ASC LIMIT 50",
                    (self._last_turn_id,),
                ).fetchall()
        except Exception:
            return []

    def _record(self, action: str, kind: str, statement: str, callback_date: float,
                verdict: str = "") -> None:
        entry = {
            "ts": time.time(),
            "action": action,          # captured | surfaced
            "kind": kind,
            "statement": statement,
            "callback_date": callback_date,
            "verdict": verdict,
        }
        with self._log_lock:
            self._log.append(entry)
            if len(self._log) > 100:
                self._log = self._log[-100:]

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:
            print(f"[TimeCapsule] {getattr(fn, '__name__', 'task')} error: {e}")
