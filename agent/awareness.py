"""Continuous awareness — event-driven watchers replace periodic screen polling.

Watchers (each in its own thread):
  - Active window (xdotool getactivewindow getwindowname): app switch events
  - Clipboard (pyperclip): text the user copies
  - File watcher (watchdog): user-configured directories
  - Screen (mss): periodic screenshot (kept from old proactive monitor)

All events flow into an AwarenessLog ring buffer. Every N seconds, a lightweight
Haiku call reviews recent events and decides whether to interrupt the user with
a proactive observation.
"""
import os
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Optional

import config

# Try imports — degrade gracefully
try:
    import pyperclip
except Exception:
    pyperclip = None

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG = True
except Exception:
    _WATCHDOG = False


class AwarenessLog:
    """Ring buffer of recent events."""
    def __init__(self, maxlen: int = 60):
        self._events: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, source: str, content: str) -> None:
        ts = time.time()
        with self._lock:
            self._events.append({"ts": ts, "source": source, "content": content})
        # Persist to durable perception log (lazy import avoids circular deps at module load)
        try:
            from agent import perception as _perc
            _perc.log_event(source, content, ts)
        except Exception:
            pass

    def recent(self, since_seconds: float = 120.0) -> list[dict]:
        cutoff = time.time() - since_seconds
        with self._lock:
            return [e for e in self._events if e["ts"] >= cutoff]

    def drain(self) -> list[dict]:
        with self._lock:
            items = list(self._events)
            self._events.clear()
            return items


class ActiveWindowWatcher(threading.Thread):
    def __init__(self, log: AwarenessLog, interval: float = 2.0):
        super().__init__(daemon=True, name="WindowWatcher")
        self.log = log
        self.interval = interval
        self._stop = threading.Event()
        self._last_title: str = ""

    def stop(self): self._stop.set()

    def run(self):
        while not self._stop.wait(timeout=self.interval):
            try:
                wid = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True, text=True, timeout=2,
                ).stdout.strip()
                if not wid:
                    continue
                title = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, text=True, timeout=2,
                ).stdout.strip()
                if title and title != self._last_title:
                    self.log.add("window", f"Switched to: {title}")
                    self._last_title = title
            except Exception:
                continue


class ClipboardWatcher(threading.Thread):
    def __init__(self, log: AwarenessLog, interval: float = 1.5):
        super().__init__(daemon=True, name="ClipboardWatcher")
        self.log = log
        self.interval = interval
        self._stop = threading.Event()
        self._last: str = ""

    def stop(self): self._stop.set()

    def run(self):
        if pyperclip is None:
            return
        while not self._stop.wait(timeout=self.interval):
            try:
                current = pyperclip.paste()
                if current and current != self._last and len(current) < 4000:
                    self._last = current
                    snippet = current[:200] + ("..." if len(current) > 200 else "")
                    self.log.add("clipboard", f"Copied: {snippet}")
            except Exception:
                continue


class FileWatcher(threading.Thread):
    def __init__(self, log: AwarenessLog, paths: list[str]):
        super().__init__(daemon=True, name="FileWatcher")
        self.log = log
        self.paths = paths
        self._observer = None

    def stop(self):
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass

    def run(self):
        if not _WATCHDOG or not self.paths:
            return

        log = self.log

        class Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.is_directory:
                    return
                log.add("file", f"Modified: {event.src_path}")
            def on_created(self, event):
                if event.is_directory:
                    return
                log.add("file", f"Created: {event.src_path}")

        self._observer = Observer()
        handler = Handler()
        for p in self.paths:
            expanded = os.path.expanduser(p)
            if os.path.exists(expanded):
                self._observer.schedule(handler, expanded, recursive=True)
        self._observer.start()
        # Keep thread alive until observer stopped
        while self._observer.is_alive():
            time.sleep(1)


_PROACTIVE_COOLDOWNS: dict[str, float] = {}  # trigger_key → last_fired timestamp

_MEETING_LOOKAHEAD_SECONDS = 600   # warn when a goal/meeting is ≤10 min away
_MEETING_COOLDOWN = 1800           # 30 min between meeting warnings
_WEATHER_COOLDOWN = 10800          # 3 hr between weather alerts


def _check_meetings(log: "AwarenessLog") -> None:
    """Emit a proactive event when a meeting or goal deadline is ≤10 min away.

    Pulls from two sources: real CalDAV calendar events (if configured) and active
    goal deadlines as a fallback. Cooldown-gated so it nudges at most once per window.
    """
    try:
        import time as _t
        now = _t.time()
        key = "meeting_check"
        if now - _PROACTIVE_COOLDOWNS.get(key, 0) < _MEETING_COOLDOWN:
            return

        # 1. Real calendar events (CalDAV) — the high-value source.
        try:
            from tools import calendar_box
            if calendar_box.is_configured():
                for ev in calendar_box.imminent_events(within_minutes=int(_MEETING_LOOKAHEAD_SECONDS / 60)):
                    mins = ev.get("starts_in_min", 0)
                    where = f" ({ev['location']})" if ev.get("location") else ""
                    log.add("calendar", f"Meeting in {mins}m: {ev['summary']}{where}")
                    _PROACTIVE_COOLDOWNS[key] = now
                    return
        except Exception:
            pass

        # 2. Goal deadlines — fallback when no calendar is wired.
        # NOTE: the goals table stores `deadline` as a REAL unix timestamp.
        from agent import longterm
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT title, deadline FROM goals WHERE status='active' AND deadline IS NOT NULL"
            ).fetchall()
        for title, deadline_ts in rows:
            if not deadline_ts:
                continue
            try:
                secs_away = float(deadline_ts) - now
                if 0 < secs_away <= _MEETING_LOOKAHEAD_SECONDS:
                    log.add("calendar", f"Upcoming deadline in {int(secs_away/60)}m: {title}")
                    _PROACTIVE_COOLDOWNS[key] = now
                    break
            except Exception:
                continue
    except Exception:
        pass


def _check_weather(log: "AwarenessLog") -> None:
    """Emit a weather alert if WEATHER_API_KEY is set and conditions changed."""
    try:
        import os, time as _t
        key = "weather_check"
        now = _t.time()
        if now - _PROACTIVE_COOLDOWNS.get(key, 0) < _WEATHER_COOLDOWN:
            return
        api_key = os.environ.get("WEATHER_API_KEY", "")
        city = os.environ.get("WEATHER_CITY", "")
        if not api_key or not city:
            return
        import urllib.request, json as _json, ssl
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
            data = _json.loads(r.read())
        desc = data.get("weather", [{}])[0].get("description", "")
        temp = data.get("main", {}).get("temp", "")
        if desc:
            log.add("weather", f"{city}: {desc}, {temp}°C")
            _PROACTIVE_COOLDOWNS[key] = now
    except Exception:
        pass


class AwarenessMonitor:
    """Coordinates watchers and triggers proactive speech-up when warranted."""

    def __init__(
        self,
        agent_proactive_check: Callable[[str], Optional[str]],
        speak_fn: Callable[[str], None],
        watch_paths: Optional[list[str]] = None,
        review_interval: float = 60.0,
    ):
        self.log = AwarenessLog()
        self.proactive_check = agent_proactive_check  # signature: (events_summary) -> str|None
        self.speak = speak_fn
        self.review_interval = review_interval
        self._stop = threading.Event()
        self._reviewer: Optional[threading.Thread] = None

        # Guardian Angel — injected after construction via monitor.guardian = ...
        self.guardian = None
        # Time Capsule — injected after construction via monitor.timecapsule = ...
        self.timecapsule = None
        # World Model + Autonomous Cortex — injected after construction
        self.world_model_client = None  # Anthropic client for world_model.build()
        self.cortex = None              # agent.cortex module reference

        # Watchers
        self.window = ActiveWindowWatcher(self.log)
        self.clipboard = ClipboardWatcher(self.log)
        self.files = FileWatcher(self.log, watch_paths or [])

        # IoT watcher — only starts if env flag is set and entities are configured
        self.iot: Optional[threading.Thread] = None
        if config.IOT_ENABLED and config.IOT_AWARENESS_ENTITIES:
            from agent.iot_watcher import IoTWatcher
            self.iot = IoTWatcher(self.log)

        # Hand tracking via barehands. Gated on the flag ALONE, deliberately:
        # the IoT watcher above also requires a non-empty allowlist, so an empty
        # list silently disables the whole subsystem while looking configured.
        # Here an empty BAREHANDS_GESTURE_ACTIONS means look-but-don't-touch —
        # gestures are still recognized and logged, they just cannot act.
        self.barehands: Optional[threading.Thread] = None
        if getattr(config, "BAREHANDS_ENABLED", False):
            from agent.barehands_watcher import BarehandsWatcher
            self.barehands = BarehandsWatcher(self.log)

        # Apex's own MediaPipe tracker — no browser, so it keeps seeing hands
        # when nothing is open. Independent of the barehands watcher above:
        # running both would put two gesture streams into one log and double
        # every wave, so the native tracker wins when both are enabled.
        self.handtrack: Optional[threading.Thread] = None
        if getattr(config, "HANDTRACK_ENABLED", False):
            from agent import handtrack as _ht
            self.handtrack = _ht.HandTracker(self.log)
            _ht.set_active_tracker(self.handtrack)
            if self.barehands is not None:
                print("[HandTrack] Both HANDTRACK_ENABLED and BAREHANDS_ENABLED "
                      "are set. Using the native tracker for gestures; barehands "
                      "stays available for the board and the ring.")
                self.barehands.gestures_enabled = False

    def start(self) -> None:
        self.window.start()
        self.clipboard.start()
        self.files.start()
        if self.iot is not None:
            self.iot.start()
        if self.handtrack is not None:
            self.handtrack.start()
            # The tracker holds the webcam, so Apex's own camera_capture tool
            # has to take frames from it rather than open the device again.
            try:
                from tools import camera as _cam
                _cam.set_tracker_frame_source(self.handtrack.latest_frame)
            except Exception as e:
                print(f"[HandTrack] could not share frames with camera_capture: {e}")
        if self.barehands is not None:
            self.barehands.start()
            # Only the watcher polls often enough to tell a live stage from a
            # frozen one, so it answers that question for tools/barehands.py.
            try:
                from tools import barehands as _bh_tools
                _bh_tools.set_tracker_probe(lambda: self.barehands.recognizer.tracker)
            except Exception as e:
                print(f"[Barehands] could not register the tracker probe: {e}")
        self._reviewer = threading.Thread(target=self._review_loop, daemon=True, name="AwarenessReviewer")
        self._reviewer.start()
        print(f"[Awareness] Monitor started (review every {self.review_interval}s).")

    def stop(self) -> None:
        self._stop.set()
        self.window.stop()
        self.clipboard.stop()
        self.files.stop()
        if self.iot is not None:
            self.iot.stop()
        if self.barehands is not None:
            self.barehands.stop()
        if self.handtrack is not None:
            self.handtrack.stop()

    def _review_loop(self) -> None:
        last_summary = ""
        # Guardian Angel checks every 15 s; general Haiku review every review_interval.
        guardian_interval = 15.0
        world_model_interval = 300.0  # 5 min — rebuild world state
        _last_guardian = 0.0
        _last_review = 0.0
        _last_world_model = 0.0
        _world_state = ""

        while not self._stop.wait(timeout=guardian_interval):
            now = time.time()
            events = self.log.recent(since_seconds=self.review_interval * 1.5)

            # Jarvis: calendar deadline warnings + optional weather alerts
            _check_meetings(self.log)
            _check_weather(self.log)

            # Guardian Angel — high-priority decision-moment detection
            if self.guardian is not None and now - _last_guardian >= guardian_interval:
                _last_guardian = now
                try:
                    self.guardian.check(events)
                except Exception as e:
                    print(f"[Guardian] Check error: {e}")

            # Time Capsule — long-horizon capture/surface (self-rate-limited)
            if self.timecapsule is not None:
                try:
                    self.timecapsule.tick()
                except Exception as e:
                    print(f"[TimeCapsule] tick error: {e}")

            # World Model + Autonomous Cortex — every 5 min
            if self.world_model_client is not None and now - _last_world_model >= world_model_interval:
                _last_world_model = now
                try:
                    from agent import world_model as _wm
                    _world_state = _wm.build(self.world_model_client, self.log, force=True)
                except Exception as e:
                    print(f"[WorldModel] Build error: {e}")

                if self.cortex is not None:
                    try:
                        self.cortex.tick(self.world_model_client, _world_state, events, force=True)
                    except Exception as e:
                        print(f"[Cortex] Tick error: {e}")

            # Restraint — score whether past pings landed, and release anything
            # held now that the moment may have turned. Runs on the 15s tick so
            # a held message surfaces promptly once you are back, rather than
            # waiting out its ceiling.
            try:
                from agent import restraint as _restraint, notify as _notify_mod
                _restraint.score_pending()
                _notify_mod.release_held(user_active=bool(events))
            except Exception as e:
                print(f"[Restraint] release tick failed: {e}")

            # Memory consolidation — distils recent activity into reflections and
            # refreshes the preference digest injected into every system prompt.
            # Runs off-thread: a consolidation pass is a full model call, and
            # blocking here would stall the 15s Guardian checks behind it.
            # Placed before the review `continue` so its cadence is its own.
            if self.world_model_client is not None:
                try:
                    from agent import reflection as _refl
                    if _refl.is_due():
                        threading.Thread(
                            target=_refl.consolidate_if_due,
                            args=(self.world_model_client,),
                            daemon=True, name="MemoryConsolidation").start()
                except Exception as e:
                    print(f"[Reflection] could not start consolidation: {e}")

            # General Haiku review — fires every review_interval
            if now - _last_review < self.review_interval:
                continue
            _last_review = now

            if not events:
                continue
            summary = "\n".join(
                f"[{e['source']}] {e['content']}" for e in events[-20:]
            )
            # --no-proactive means "do not speak up unprompted". Honoured here
            # rather than by skipping the monitor, so the cortex, Guardian and
            # Time Capsule keep working — those are awareness, not interruption.
            #
            # This flag had no effect at all until now: the only code that read
            # it was ProactiveMonitor.start(), and ProactiveMonitor only ran when
            # AWARENESS_ENABLED was False, which is not the default. So
            # --no-proactive printed "Proactive: off" and changed nothing. That
            # predates the deletion of proactive.py; removing the module is what
            # made it visible.
            if not getattr(config, "PROACTIVE_ENABLED", True):
                continue
            try:
                observation = self.proactive_check(summary)
                if observation and observation != last_summary:
                    last_summary = observation
                    print(f"[Awareness] Speaking up: {observation}")
                    self.speak(f"Heads up. {observation}")
            except Exception as e:
                print(f"[Awareness] Review error: {e}")


def report_hand_tracking(monitor) -> str:
    """Say, at boot, whether hand tracking is running — and if not, why not.

    Silence is a terrible status report. A user with BAREHANDS_ENABLED=true and
    no `[Barehands]` line in the console cannot tell apart: the flag did not
    take, the code is not on this machine, or AWARENESS_ENABLED is off and took
    hand tracking down with it as a side effect. All three look identical, and
    working looks identical too until the watcher thread gets a slice.

    That last case is the nasty one: two independent flags where the wrong one
    silently wins. Returns the line it printed, for tests.
    """
    native = getattr(config, "HANDTRACK_ENABLED", False)
    board = getattr(config, "BAREHANDS_ENABLED", False)
    if not (native or board):
        return ""
    if monitor is None:
        line = ("[HandTrack] Hand tracking is enabled but the awareness monitor "
                "is not running, so it is OFF. Set AWARENESS_ENABLED=true.")
    elif native and getattr(monitor, "handtrack", None) is not None:
        line = ("[HandTrack] Hand tracking on — Apex's own camera tracker, "
                "no browser needed.")
    elif board and getattr(monitor, "barehands", None) is not None:
        line = f"[Barehands] Hand tracking on, polling {config.BAREHANDS_URL}."
    else:
        line = ("[HandTrack] Hand tracking is enabled but no watcher was built — "
                "this Apex is older than the hand-tracking code. Pull and restart.")
    print(line)
    return line


def attach_live_feed(monitor, dash) -> bool:
    """Make everything the monitor observes appear in the dashboard live feed.

    Shared by both entry points on purpose. This used to be nine lines pasted
    inline in `main.py` and simply absent from `app/resident.py`, so the
    always-on mode — the one that actually runs unattended — showed an empty
    live feed while every watcher was working perfectly. Two copies of wiring
    is how one of them goes missing; there is now one.

    Returns True if the feed was attached. Idempotent: attaching twice would
    double every event, so a second call is a no-op.
    """
    log = getattr(monitor, "log", None)
    if log is None:
        return False
    if getattr(log.add, "_apex_live_feed", False):
        return True

    original = log.add

    def _add_and_broadcast(source, content):
        original(source, content)
        try:
            dash.ws_manager.broadcast_threadsafe({
                "type": "event", "ts": time.time(),
                "source": source, "content": content,
            })
        except Exception:
            # The live feed is a view, never a reason to lose an observation.
            pass

    _add_and_broadcast._apex_live_feed = True
    log.add = _add_and_broadcast
    return True


def build_monitor(agent, speak_fn, notify_fn=None):
    """Construct a fully-wired AwarenessMonitor (Guardian + Time Capsule + World
    Model + autonomous Cortex), ready to .start().

    Shared by the interactive (`main.py`) and always-on (`app/resident.py`) entry
    points so the autonomous cortex runs in BOTH — previously resident mode built
    no monitor at all, so the cortex/world-model/Guardian/Time-Capsule never ticked.

    Caller is responsible for having initialised the relevant DBs (cortex, world
    model, perception, skill_forge, approvals). Returns the monitor, NOT started.
    """
    from agent import telemetry
    from agent import cortex as _cortex_mod, skill_forge as _forge_mod

    def _proactive_check(events_summary: str):
        try:
            resp = telemetry.create(
                agent.anthropic,
                call_site="agent.awareness/review",
                model=config.PROACTIVE_MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": (
                    "You watch the user's desktop in the background. "
                    "Recent events (last ~90s):\n\n" + events_summary + "\n\n"
                    "Is there anything URGENT or genuinely useful to proactively bring up RIGHT NOW? "
                    "Examples that warrant interrupting: an error in their work, a security concern, "
                    "they look stuck on something obvious you could help with, an opportunity they'll miss. "
                    "Examples that DON'T: normal app switching, routine copy-paste, mundane file edits. "
                    "If YES, respond with a single short observation (1 sentence). "
                    "If NO, respond with exactly: NO"
                )}],
            )
            text = resp.content[0].text.strip()
            return None if text.upper().startswith("NO") else text
        except Exception:
            return None

    watch_paths = [os.path.expanduser(p) for p in config.AWARENESS_WATCH_PATHS]
    monitor = AwarenessMonitor(
        agent_proactive_check=_proactive_check,
        speak_fn=speak_fn,
        watch_paths=watch_paths,
        review_interval=config.AWARENESS_REVIEW_INTERVAL,
    )

    # Guardian Angel + Time Capsule (best-effort; never fatal)
    if getattr(config, "GUARDIAN_ANGEL_ENABLED", True):
        try:
            from agent.guardian import GuardianAngel
            from agent import longterm as _lt

            def _recall_for_guardian(query: str, limit: int) -> str:
                try:
                    results = _lt.recall(query, limit=limit, semantic=True)
                    return "\n".join(r.get("content", "") for r in results if r.get("content"))
                except Exception:
                    return ""

            monitor.guardian = GuardianAngel(
                speak_fn=speak_fn,
                tray_notify_fn=lambda _t, _m: None,
                recall_fn=_recall_for_guardian,
            )
            if getattr(config, "TIME_CAPSULE_ENABLED", True):
                from agent.timecapsule import TimeCapsule, _init_table
                _init_table()
                monitor.timecapsule = TimeCapsule(
                    speak_fn=speak_fn,
                    tray_notify_fn=lambda _t, _m: None,
                )
        except Exception as e:
            print(f"[Awareness] Guardian/TimeCapsule wiring skipped: {e}")

    # World Model + autonomous Cortex
    monitor.world_model_client = agent.anthropic
    monitor.cortex = _cortex_mod
    if notify_fn is not None:
        try:
            _cortex_mod.set_notify_fn(notify_fn)
            _forge_mod.set_notify_fn(notify_fn)
            from agent import approvals as _appr_mod
            _appr_mod.set_notify_fn(notify_fn)
        except Exception:
            pass
    print("[Cortex] Autonomous cortex wired into monitor.")
    return monitor
