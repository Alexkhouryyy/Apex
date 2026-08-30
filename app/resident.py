"""Apex Resident — the always-on background companion entry point.

Run with: python main.py --resident

Boots: agent + wake listener + scheduler + dashboard + (optional) tray + hotkey.
Logs everything to ~/.apex/resident.log so closing a terminal can't kill it.
Silent by default — no greeting on boot, just a tray icon and a wake listener.

State machine:
    idle ──(wake / hotkey)──> listening ──(stt done)──> thinking
    thinking ──(agent done)──> speaking ──(tts done)──> idle
    any ──(mute toggle)──> muted ──(unmute)──> idle
    any ──(quit)──> shutdown
"""
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
import webbrowser
from typing import Optional

import config


def _setup_logging() -> None:
    """Redirect stdout/stderr to a rotating log file (10 MB, 5 backups)."""
    log_path = config.RESIDENT_LOG_FILE
    parent = os.path.dirname(log_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Redirect raw print() output too — Apex uses a lot of print() calls
    class _LogStream:
        def __init__(self, level: int):
            self.level = level
            self._buf = ""

        def write(self, s: str) -> None:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    logging.log(self.level, line)

        def flush(self) -> None:
            if self._buf.strip():
                logging.log(self.level, self._buf)
                self._buf = ""

    sys.stdout = _LogStream(logging.INFO)
    sys.stderr = _LogStream(logging.ERROR)


class ResidentState:
    """Thread-safe state holder. Tray + hotkey + wake all publish here."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MUTED = "muted"

    def __init__(self) -> None:
        self._state = self.IDLE
        self._previous = self.IDLE
        self._lock = threading.Lock()
        self._muted_until: Optional[float] = None   # epoch; None = not muted
        self._listeners: list = []
        self._shutdown = threading.Event()

    @property
    def shutdown(self) -> threading.Event:
        return self._shutdown

    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    def get(self) -> str:
        with self._lock:
            if self._muted_until is not None:
                if time.time() >= self._muted_until:
                    self._muted_until = None
                else:
                    return self.MUTED
            return self._state

    def set(self, new_state: str) -> None:
        with self._lock:
            if new_state == self._state:
                return
            self._previous = self._state
            self._state = new_state
        for fn in list(self._listeners):
            try:
                fn(new_state)
            except Exception as e:
                logging.warning(f"State listener error: {e}")

    def is_muted(self) -> bool:
        return self.get() == self.MUTED

    def mute(self, minutes: int) -> None:
        """minutes > 0 = mute for that long; minutes == -1 = until quit;
        minutes == 0 = unmute."""
        with self._lock:
            if minutes == 0:
                self._muted_until = None
            elif minutes < 0:
                self._muted_until = float("inf")
            else:
                self._muted_until = time.time() + minutes * 60
        # Surface a state change
        for fn in list(self._listeners):
            try:
                fn(self.get())
            except Exception:
                pass


def resident_confirm(reason: str) -> bool:
    """Answer safety.check() when there is nobody to ask.

    Module-level so it can be tested — the same reason `iot_watcher.decide_event`
    is. It used to not exist at all: `main.py` injects a voice prompt via
    `safety.set_confirm_fn`, resident mode injected nothing, and `safety.check`
    falls back to `input("Proceed? (y/N): ")`. On a daemon whose stdout is a log
    file and whose stdin nobody is attached to, that blocks the turn for ever.

    The answer has to be NO. An action risky enough to require confirmation must
    not happen merely because the user was not at the keyboard — that is the
    whole point of the gate. It is announced rather than silently refused, so a
    blocked action is something you find out about.
    """
    logging.warning(f"Safety refused — nobody to ask in resident mode: {reason}")
    try:
        from agent import notify as _n
        _n.notify(title="Apex blocked a risky action",
                  body=str(reason)[:300], kind="safety", priority="high")
    except Exception:
        pass
    return False


def run_resident(model_override: Optional[str] = None) -> None:
    """Main entry point. Blocks until quit."""
    _setup_logging()
    logging.info("=" * 50)
    logging.info(f"Apex Resident starting (pid={os.getpid()})")
    logging.info("=" * 50)

    # --- Boot the agent (mostly the same as main.py, but quieter) ---
    from agent.core import AgentCore
    from agent import longterm, telemetry, scheduler as sched, self_mod
    from agent import knowledge, goals, feedback as fb_mod
    from agent import reflection
    from voice.tts import speak as _voice_speak

    longterm.init_db()
    session_id = longterm.start_session()
    telemetry.set_session(session_id)
    logging.info(f"Memory session #{session_id} started.")

    agent = AgentCore()
    if model_override:
        logging.info(f"Model override: {agent.set_model(model_override)}")

    memories = longterm.top_memories(limit=15)
    if memories:
        agent.memory.summary = longterm.format_for_context(memories)
        logging.info(f"Loaded {len(memories)} long-term memories.")

    # MCP discovery in background
    threading.Thread(
        target=lambda: agent.load_mcp_tools(),
        daemon=True, name="MCPDiscover"
    ).start()

    # Self-mod tools
    self_mod.load_dynamic_handlers()
    # Procedural (markdown) skills that ship with Apex — self-install on first boot.
    try:
        from agent import skill_md as _skill_md_mod
        _n_bundled = _skill_md_mod.install_bundled()
        if _n_bundled:
            logging.info(f"Installed {_n_bundled} bundled procedural skill(s).")
    except Exception as e:
        logging.warning(f"Bundled skill install skipped: {e}")
    knowledge.init_db()
    goals.init_db()
    fb_mod.init_db()
    from agent import budget as _budget_mod; _budget_mod.init_db()
    # Autonomy DBs — required before the cortex/world-model/forge/approvals run.
    from agent import world_model as _wm_mod; _wm_mod.init_db()
    from agent import perception as _perc_mod; _perc_mod.init_db()
    from agent import cortex as _cortex_mod; _cortex_mod.init_db()
    from agent import skill_forge as _forge_mod; _forge_mod.init_db()
    from agent import approvals as _appr_mod; _appr_mod.init_db()
    from agent import compare as _compare_mod; _compare_mod.init_db()
    from agent import access_tokens as _at_mod; _at_mod.init_db()
    from agent import trajectory as _traj_mod; _traj_mod.init_db()
    from agent import verification as _verif_mod; _verif_mod.init_db()
    from agent import reranker as _rr_mod; _rr_mod.init_db()
    from agent import documents as _docs_mod; _docs_mod.init_db()

    # Scheduler — pass a thin speak that goes through the state machine
    state = ResidentState()
    sched.init(agent_run_fn=agent.run, speak_fn=lambda t: _speak_with_state(state, t))

    # Autonomous cortex — BUG FIX: resident mode previously built no AwarenessMonitor,
    # so the cortex/world-model/Guardian/Time-Capsule never ticked in the always-on
    # path. Build and start the same wired monitor the interactive path uses.
    awareness_monitor = None
    if config.AWARENESS_ENABLED:
        try:
            from agent import awareness as _awareness_mod
            from agent import notify as _notify_ref
            awareness_monitor = _awareness_mod.build_monitor(
                agent,
                speak_fn=lambda t: _speak_with_state(state, t),
                notify_fn=_notify_ref.notify,
            )
            try:
                reflection.set_awareness_drain(
                    lambda: awareness_monitor.log.recent(since_seconds=86400)
                )
            except Exception:
                pass
            awareness_monitor.start()
            logging.info("Autonomous cortex + awareness monitor started (resident).")
        except Exception as e:
            logging.error(f"Awareness monitor failed to start in resident mode: {e}")

    from agent import safety as _safety_ref
    _safety_ref.set_confirm_fn(resident_confirm)

    try:
        from agent import awareness as _aw_report
        _line = _aw_report.report_hand_tracking(awareness_monitor)
        if _line:
            logging.info(_line)
    except Exception as e:
        logging.warning(f"Hand-tracking status report failed: {e}")

    # Resident-mode greeting policy: never speak on boot
    logging.info(f"Silent boot: {config.RESIDENT_SILENT_BOOT}")

    # Jarvis: me.md profile digest background loop
    try:
        reflection.start_profile_digest_loop(agent.anthropic)
        logging.info("Profile digest loop started.")
    except Exception as e:
        logging.warning(f"Profile digest loop skipped: {e}")

    # Dashboard (background thread, optional)
    _dashboard_url = f"http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}"
    if getattr(config, "DASHBOARD_ENABLED", True):
        try:
            from dashboard import server as dash
            # Was `awareness_log=None`, which meant the live feed got no
            # snapshot backlog and — with the rebind below missing too — no
            # events at all in resident mode. Everything the watchers observed
            # was invisible in exactly the mode that runs unattended.
            dash.set_agent(agent, awareness_log=getattr(awareness_monitor, "log", None))
            if awareness_monitor is not None:
                from agent import awareness as _aw_mod
                _aw_mod.attach_live_feed(awareness_monitor, dash)
            _dash_t = dash.start_in_background(port=getattr(config, "DASHBOARD_PORT", 7860))
            # Where it actually bound, not where we asked — see server.start_in_background.
            _dashboard_url = getattr(_dash_t, "dashboard_url", _dashboard_url)
            logging.info(f"Dashboard: {_dashboard_url}")
        except Exception as e:
            logging.error(f"Dashboard NOT running — {e}")

    # Jarvis: desktop orb (opt-in via ORB_ENABLED=true in .env)
    if getattr(config, "ORB_ENABLED", False):
        try:
            from app.orb import run_orb
            hotkey = getattr(config, "DESKTOP_SHELL_HOTKEY", "<ctrl>+<shift>+\\")
            threading.Thread(
                target=run_orb,
                kwargs={"dashboard_url": _dashboard_url, "hotkey": hotkey},
                daemon=True,
                name="ApexOrb",
            ).start()
            logging.info(f"Desktop orb launched (hotkey: {hotkey})")
        except Exception as e:
            logging.warning(f"Orb failed to start: {e}")

    # Wire messaging channels to the agent so inbound webhooks (WhatsApp, Telegram,
    # SMS, etc.) actually reach Apex in always-on mode. Without this, the dashboard
    # webhooks are live but every inbound message gets "Agent not ready yet."
    try:
        from tools.channels import wire_channels
        wire_channels(agent)
        logging.info("Messaging channels wired to agent.")
    except Exception as e:
        logging.error(f"Channel wiring failed: {e}")

    # Telegram long-polling — pulls messages when there's no public webhook URL.
    if config.TELEGRAM_POLLING:
        try:
            from tools import telegram as _tg
            logging.info(_tg.start_polling())
        except Exception as e:
            logging.error(f"Telegram polling failed to start: {e}")

    # --- Wake listener ---
    from voice.wake import WakeWordListener
    from voice.stt import listen, warm_up as _stt_warm
    from app import audit, hotkey as hotkey_mod, tray as tray_mod

    threading.Thread(target=_stt_warm, daemon=True, name="STTWarmup").start()

    # Single-flight lock — only one turn at a time
    turn_lock = threading.Lock()
    # Turn index of the last SPOKEN answer, captured at the moment it completes.
    # Required because telemetry._turn_index is a single process-wide global that
    # the scheduler, cortex, channels and dashboard all advance concurrently — so
    # reading current_turn() when the user says "that was perfect" could rate a
    # background turn instead of the one they just heard.
    _last_voice_turn = {"index": 0}

    def _do_turn(text_from_wake: str = "", *, from_hotkey: bool = False) -> None:
        """Run one user turn. text_from_wake may already contain the request."""
        if not turn_lock.acquire(blocking=False):
            logging.info("Turn already in progress, ignoring trigger.")
            return
        try:
            # Decide what user input to use
            user_text = _extract_request(text_from_wake, config.WAKE_PHRASES)

            # If the user only said "apex" (no continuation), open the mic.
            if not user_text:
                if config.RESIDENT_WAKE_REQUIRE_CONTINUATION and not from_hotkey:
                    audit.record(text_from_wake, "no_continuation",
                                 note="single wake word, no follow-up — opening mic")
                state.set(ResidentState.LISTENING)
                try:
                    user_text = listen() or ""
                except Exception as e:
                    logging.error(f"Listen failed: {e}")
                    state.set(ResidentState.IDLE)
                    return

            if not user_text.strip():
                state.set(ResidentState.IDLE)
                return

            # Spoken feedback ("that was perfect", "no, that's wrong") rates the
            # PREVIOUS answer instead of starting a new turn. This is the only
            # way ratings are ever captured in resident mode, and the reward
            # model behind best-of-n reranking is trained on them.
            from agent import feedback as _fb
            phrase_rating = _fb.capture_phrase(
                user_text, session_id=session_id,
                last_turn=_last_voice_turn["index"],   # captured, not a live read
                source="voice",
            )
            if phrase_rating is not None:
                audit.record(user_text, "feedback",
                             note=f"rated turn #{_last_voice_turn['index']} "
                                  f"{'up' if phrase_rating == 1 else 'down'}")
                _speak_with_state(state, "Got it — noted.")
                state.set(ResidentState.IDLE)
                return

            # Run the turn (screenshot included by default)
            state.set(ResidentState.THINKING)
            try:
                reply = agent.run(user_text, include_screenshot=True, use_thinking=False)
                # Pin the turn this answer landed on, so spoken feedback on the
                # NEXT turn rates this one and not whatever background turn the
                # scheduler/cortex has advanced the global counter to since.
                try:
                    _last_voice_turn["index"] = telemetry.current_turn()
                except Exception:
                    pass
            except Exception as e:
                logging.exception("Agent.run failed")
                reply = f"Something went wrong: {e}"

            # Speak
            state.set(ResidentState.SPEAKING)
            try:
                _voice_speak(reply)
            except Exception as e:
                logging.error(f"TTS failed: {e}")

            audit.record(text_from_wake or "[hotkey]", "responded",
                         note=f"reply_chars={len(reply)}")
        finally:
            state.set(ResidentState.IDLE)
            turn_lock.release()

    def _on_wake(transcript: str = "") -> None:
        if state.is_muted():
            audit.record(transcript, "muted_ignored")
            return
        threading.Thread(target=_do_turn, args=(transcript,),
                         daemon=True, name="WakeTurn").start()

    def _on_hotkey_wake() -> None:
        if state.is_muted():
            audit.record("[hotkey]", "muted_ignored")
            return
        threading.Thread(target=_do_turn, kwargs={"from_hotkey": True},
                         daemon=True, name="HotkeyTurn").start()

    def _on_mute_hotkey() -> None:
        if state.is_muted():
            state.mute(0)
            logging.info("Unmuted via hotkey.")
            tray_obj.notify("Apex", "Unmuted") if tray_obj else None
        else:
            state.mute(-1)
            logging.info("Muted via hotkey.")
            tray_obj.notify("Apex", "Muted (until you unmute)") if tray_obj else None

    wake_listener = None
    if config.WAKE_WORD_ENABLED:
        wake_listener = WakeWordListener(wake_phrases=config.WAKE_PHRASES)
        wake_listener.start(on_wake=_on_wake)

    # Gestures wake Apex from the native tracker. Gated on the same flag that
    # decides whether it recognizes them at all — turning gestures on with no
    # tracker running would recognize things that could never do anything.
    if getattr(config, "HANDTRACK_ENABLED", False):
        try:

            def _on_gesture(gesture: str, action: str) -> None:
                """A gesture never calls a tool. It reaches one of the same
                attention verbs the hotkey uses, and inherits the mute check
                with them — a camera that can wake a muted Apex is worse than
                no camera."""
                if action in ("wake", "listen"):
                    # The same thing in resident mode, and worth saying rather
                    # than implying two paths: a turn already begins by opening
                    # the mic, so "wake" and push-to-talk "listen" land on one
                    # handler. The audit will label the turn "[hotkey]"; the
                    # gesture itself is durably recorded in perception_log by
                    # the watcher, which is the trail that matters.
                    _on_hotkey_wake()
                elif action == "stop":
                    if state.get() == ResidentState.SPEAKING:
                        state.set(ResidentState.IDLE)
                else:
                    logging.warning(
                        f"Gesture '{gesture}' is mapped to unknown action "
                        f"'{action}' — check HANDTRACK_GESTURE_ACTIONS.")

            src = getattr(awareness_monitor, "handtrack", None) if awareness_monitor else None
            if src is not None:
                src.on_gesture = _on_gesture
            else:
                logging.warning(
                    "Hand tracking is enabled but no watcher is running — "
                    "gestures will not be seen (is AWARENESS_ENABLED off?).")
        except Exception as e:
            logging.error(f"gesture wiring failed: {e}")

    # --- Tray icon ---
    def _do_mute(minutes: int) -> None:
        state.mute(minutes)
        if minutes == 0:
            tray_obj.notify("Apex", "Unmuted") if tray_obj else None
        elif minutes < 0:
            tray_obj.notify("Apex", "Muted until you unmute") if tray_obj else None
        else:
            tray_obj.notify("Apex", f"Muted for {minutes} min") if tray_obj else None

    def _open_dashboard() -> None:
        url = f"http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}"
        try:
            webbrowser.open(url)
        except Exception as e:
            logging.warning(f"Could not open dashboard: {e}")

    def _show_recent() -> None:
        entries = audit.recent(20)
        if not entries:
            if tray_obj:
                tray_obj.notify("Apex", "No recent wake events.")
            return
        snippet = "\n".join(f"{e.get('ts', '?')} {e.get('heard', '')}" for e in entries[:5])
        if tray_obj:
            tray_obj.notify("Apex — recent", snippet)

    def _quit() -> None:
        logging.info("Quit requested via tray.")
        state.shutdown.set()
        if tray_obj:
            tray_obj.stop()

    tray_obj = tray_mod.Tray(
        on_wake_now=_on_hotkey_wake,
        on_mute=_do_mute,
        on_open_dashboard=_open_dashboard,
        on_show_recent=_show_recent,
        on_quit=_quit,
    )
    tray_started = tray_obj.start()

    # State → tray icon
    def _state_to_tray(new_state: str) -> None:
        if tray_started:
            tray_obj.set_state(new_state)
    state.add_listener(_state_to_tray)

    # --- Global hotkeys ---
    hotkeys = hotkey_mod.GlobalHotkeys()
    hotkeys.bind(config.RESIDENT_GLOBAL_HOTKEY, _on_hotkey_wake)
    hotkeys.bind(config.RESIDENT_MUTE_HOTKEY, _on_mute_hotkey)
    hotkeys.start()

    # --- Signals ---
    def _shutdown_signal(sig, frame):
        logging.info(f"Signal {sig} received, shutting down.")
        state.shutdown.set()

    signal.signal(signal.SIGINT, _shutdown_signal)
    signal.signal(signal.SIGTERM, _shutdown_signal)

    logging.info("Apex Resident is live. Say 'Apex' or press the wake hotkey.")
    if tray_started:
        tray_obj.notify("Apex", "Resident mode active. Say 'Apex' or press Ctrl+Space.")

    # --- Main loop: just block until shutdown ---
    try:
        while not state.shutdown.is_set():
            state.shutdown.wait(timeout=1.0)
    finally:
        logging.info("Shutting down resident…")
        if awareness_monitor is not None:
            try:
                awareness_monitor.stop()
            except Exception:
                pass
        if wake_listener:
            wake_listener.stop()
        hotkeys.stop()
        if tray_started:
            tray_obj.stop()
        try:
            longterm.end_session(session_id, summary=agent.memory.summary)
        except Exception:
            pass
        logging.info("Resident exited cleanly.")


def _extract_request(transcript: str, wake_phrases: list[str]) -> str:
    """Strip the wake phrase off the front, return whatever the user said after.
    Returns empty string if there's no continuation."""
    if not transcript:
        return ""
    text = transcript.lower().strip()
    # Try each wake phrase, longest first (so "hey apex" beats "apex")
    for phrase in sorted(wake_phrases, key=len, reverse=True):
        idx = text.find(phrase.lower())
        if idx >= 0:
            after = text[idx + len(phrase):].strip(" ,.?!:")
            return after
    return text


def _speak_with_state(state: "ResidentState", text: str) -> None:
    """Speak via voice/tts, marking state SPEAKING ↔ IDLE."""
    from voice.tts import speak
    state.set(ResidentState.SPEAKING)
    try:
        speak(text)
    finally:
        state.set(ResidentState.IDLE)
