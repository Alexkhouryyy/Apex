#!/usr/bin/env python3
"""
Voice AI Agent — talks, sees your screen, controls your computer, researches, pushes back.

Usage:
    python main.py                  # voice mode (default)
    python main.py --text           # text mode (no mic/speaker needed)
    python main.py --text --think   # text mode + extended thinking for all queries
"""
import argparse
import os
import signal
import sys
import threading
import time

# Ensure ANTHROPIC_API_KEY is set before anything else
from dotenv import load_dotenv
load_dotenv()

import config

if not config.ANTHROPIC_API_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set. Create a .env file or set the environment variable.")
    sys.exit(1)


# Returned by the text-mode reader when stdin is closed. A distinct object,
# not "", because "" already means "you pressed enter" and the main loop's
# response to that is to prompt again — which is exactly the wrong answer to
# an EOF.
_EOF = "\x00stdin-closed"


def read_text_input() -> str:
    """Read one line in text mode. Module-level so it is testable — the bug it
    exists to prevent lived in a closure inside main(), where nothing could
    reach it."""
    try:
        return input("YOU: ").strip()
    except EOFError:
        return _EOF
    except KeyboardInterrupt:
        return ""


def build_parser():
    p = argparse.ArgumentParser(description="Voice AI Agent")
    p.add_argument("--text", action="store_true", help="Text I/O instead of voice")
    p.add_argument("--tui", action="store_true", help="Rich terminal UI: streaming output + interrupt-and-redirect")
    p.add_argument("--think", action="store_true", help="Enable extended thinking for all queries")
    p.add_argument("--no-proactive", action="store_true", help="Disable proactive screen monitoring")
    p.add_argument("--no-screenshot", action="store_true", help="Don't auto-attach screenshot to each message")
    p.add_argument("--wake", action="store_true", help="Hands-free wake word mode (say 'Apex')")
    p.add_argument("--resident", action="store_true", help="Always-on background companion with tray icon and global hotkey")
    p.add_argument("--model", type=str, default=None, help="Override starting model (e.g. claude-sonnet-5, gpt-5.1, ollama/llama3)")
    return p


def main():
    args = build_parser().parse_args()

    # Resident mode: always-on background companion with tray + global hotkey.
    # Hands off to a dedicated entry point that owns the state machine.
    if args.resident:
        from app.resident import run_resident
        run_resident(model_override=args.model)
        return

    if args.no_proactive:
        config.PROACTIVE_ENABLED = False

    print("\n" + "="*60)
    print("  Voice AI Agent")
    print("  Model:     ", config.AGENT_MODEL)
    print("  Mode:      ", "tui" if args.tui else ("text" if args.text else "voice"))
    print("  Thinking:  ", "on" if args.think else "auto")
    print("  Proactive: ", "on" if config.PROACTIVE_ENABLED else "off")
    print("  Wake word: ", "on" if args.wake else "off")
    print("="*60 + "\n")

    # Initialize agent + long-term memory
    from agent.core import AgentCore
    from agent import longterm, telemetry
    # Every table, from the one list app/resident.py also uses. Kept in
    # agent/schema.py rather than here because these two boot sequences
    # were hand-maintained copies and had drifted by twelve modules.
    from agent import schema as _schema
    _schema.init_all()
    # Pick up notes edited in Obsidian since last run. Off the critical
    # path on purpose: freshness costs a read of every note, and doing
    # that at query time would put the whole vault back on the retrieval
    # path, which is the one thing vault search must not do.
    from agent import vault_index as _vault_index
    _vault_index.start_background_reindex()
    session_id = longterm.start_session()
    telemetry.set_session(session_id)
    print(f"[Memory] Session #{session_id} started. DB: {longterm.DB_PATH}")

    # Load top memories into the agent's working context as a system reminder
    memories = longterm.top_memories(limit=15)
    if memories:
        print(f"[Memory] Loaded {len(memories)} long-term memories.")
    agent = AgentCore()
    if args.model:
        result = agent.set_model(args.model)
        print(f"[Model] {result}")
    if memories:
        agent.memory.summary = longterm.format_for_context(memories)

    # Jarvis: me.md profile digest background loop
    try:
        from agent import reflection as _reflection
        _reflection.start_profile_digest_loop(agent.anthropic)
        print("[Reflection] Profile digest loop started.")
    except Exception as _e:
        print(f"[Reflection] Profile digest loop skipped: {_e}")

    # MCP tool discovery (non-blocking — happens in background)
    import threading
    def _load_mcp():
        n = agent.load_mcp_tools()
        if n:
            speak(f"Connected {n} MCP tools.")
    threading.Thread(target=_load_mcp, daemon=True, name="MCPDiscover").start()

    # Initialize I/O
    if args.text or args.tui:
        def speak(text: str):
            print(f"\nAGENT: {text}\n")

        listen = read_text_input
    else:
        from voice.tts import speak
        from voice.stt import listen, listen_streaming, warm_up as _stt_warm

        # Pre-warm Whisper kernel so the first real turn is fast
        threading.Thread(target=_stt_warm, daemon=True, name="STTWarmup").start()

        # Warm up TTS
        speak("Agent online. Ready.")

    # Wire safety confirmation to speak+listen
    from agent import safety as _safety
    def _voice_confirm(reason: str) -> bool:
        speak(f"Safety check. {reason}. Say yes to proceed.")
        answer = listen() if not (args.text or args.tui) else input("Proceed? (y/N): ").strip()
        return answer.lower() in {"yes", "y", "yeah", "yep", "do it", "confirm"}
    # Only THIS thread owns the console. A scheduled task, the cortex or an
    # inbound channel hitting a safety rule must not be able to seize the
    # prompt — see safety.interactive_only for what that looked like live.
    _safety.set_confirm_fn(_safety.interactive_only(_voice_confirm))

    # Start scheduler
    from agent import scheduler as sched
    sched.init(agent_run_fn=agent.run, speak_fn=speak)

    # Wire orchestrator with a fresh-agent factory (no shared memory)
    from agent import orchestrator
    def _sub_factory():
        from agent.core import AgentCore
        return AgentCore()
    orchestrator.set_agent_factory(_sub_factory)

    # Load self-mod overlay (system prompt addition + dynamic tools)
    from agent import self_mod
    n_dyn = self_mod.load_dynamic_handlers()
    if n_dyn:
        print(f"[SelfMod] Loaded {n_dyn} user-defined tools.")

    # Load skills registry
    from agent import skills as skills_mod
    # Procedural (markdown) skills that ship with Apex — self-install on first boot.
    from agent import skill_md as _skill_md_mod
    _n_bundled = _skill_md_mod.install_bundled()
    if _n_bundled:
        print(f"[Skills] Installed {_n_bundled} bundled procedural skill(s).")

    n_skills = skills_mod.load_all()
    if n_skills:
        print(f"[Skills] Loaded {n_skills} skill(s).")

    # Obsidian vault — create structure + migrate memory files on first run
    from agent import vault as _vault_mod
    print(f"[Vault] {_vault_mod.init_vault()}")

    # The Constellation — 12 persistent domain-expert planets + their vault journals
    from agent import constellation as _constellation_mod
    print(f"[Constellation] {_constellation_mod.init()}")

    # Knowledge base — optionally trigger background indexing.
    # Its tables come from schema.init_all() above, like everything else.
    from agent import knowledge

    # Goals — auto-schedule the weekly self-eval (once per fresh DB)
    from agent import goals

    from agent import feedback  # noqa: F401  (turn_feedback, for 👍/👎 capture)
    weekly_eval_exists = any(
        "Weekly self-evaluation" in t.get("description", "") for t in sched.list_tasks()
    )
    if not weekly_eval_exists:
        sched.schedule(
            description=(
                "Weekly self-evaluation. Call evaluate_recent_work(days=7) and speak the result. "
                "Then suggest one concrete focus for the coming week."
            ),
            trigger_type="cron",
            trigger_params={"day_of_week": "mon", "hour": 8, "minute": 0},
        )

    # Nightly reflection — once per fresh DB
    from agent import reflection
    nightly_refl_exists = any(
        "Nightly reflection" in t.get("description", "") for t in sched.list_tasks()
    )
    if not nightly_refl_exists:
        sched.schedule(
            description=(
                "Nightly reflection. Call reflect_now(hours=24). Then call list_reflections(status='pending') "
                "and briefly mention how many insights are awaiting review on the dashboard."
            ),
            trigger_type="cron",
            trigger_params={"hour": 3, "minute": 0},
        )

    # Morning briefing — daily spoken digest (weather, news, follow-ups)
    from agent import briefing as _briefing
    _briefing_result = _briefing.install_briefing_task()
    if "scheduled" in _briefing_result:
        print(f"[Briefing] {_briefing_result}")

    # Wire every messaging channel's inbound dispatch to this agent.
    from tools.channels import wire_channels
    wire_channels(agent)

    # Skill Curator — background maintenance thread
    if getattr(config, "CURATOR_ENABLED", True):
        _last_activity_ref = {"ts": time.time()}
        def _curator_tick():
            import time as _t
            while True:
                _t.sleep(3600)
                try:
                    from agent import curator as _cur
                    if _cur.should_run(last_active_ts=_last_activity_ref["ts"]):
                        print("[Curator] Running scheduled curation...")
                        report = _cur.run(dry_run=False, client=agent.anthropic)
                        lines = report.splitlines()
                        print(f"[Curator] Done. {len(lines)} report lines.")
                except Exception as e:
                    print(f"[Curator] Error: {e}")
        threading.Thread(target=_curator_tick, daemon=True, name="SkillCurator").start()

    # Telegram long-polling — pulls messages when there's no public webhook URL.
    if config.TELEGRAM_POLLING:
        from tools import telegram as _tg
        print(_tg.start_polling())

    # Awareness monitor (replaces old screenshot-only proactive)
    if config.AWARENESS_ENABLED:
        # Built via the shared helper so the interactive and always-on (resident)
        # paths wire the autonomous cortex identically and cannot drift apart.
        from agent import awareness as _awareness_mod
        from agent import notify as _notify_ref
        monitor = _awareness_mod.build_monitor(
            agent, speak_fn=speak, notify_fn=_notify_ref.notify
        )
        print("[Cortex] Autonomous cortex active (trusted allowlist mode).")
        print("[SkillForge] Skill forge active.")
    else:
        # No monitor at all. The old ProactiveMonitor lived here — a 30-second
        # screenshot poller that awareness replaced ("Screen (mss): periodic
        # screenshot (kept from old proactive monitor)"). With AWARENESS_ENABLED
        # defaulting True it had been unreachable for a long time, and
        # app/resident.py never had it, so the always-on path already ran
        # without it. Turning awareness off now means no watching, which is what
        # turning it off should mean.
        monitor = None

    # Let reflection pull awareness events at consolidation time
    if hasattr(monitor, "log"):
        reflection.set_awareness_drain(lambda: monitor.log.recent(since_seconds=86400))

    # Start the web dashboard
    if getattr(config, "DASHBOARD_ENABLED", True):
        try:
            from dashboard import server as dash
            dash.set_agent(agent, awareness_log=getattr(monitor, "log", None))
            # Hook awareness events into the WebSocket broadcaster
            from agent import awareness as _aw_mod
            _aw_mod.attach_live_feed(monitor, dash)
            if hasattr(monitor, "guardian") and monitor.guardian is not None:
                dash.set_guardian(monitor.guardian)
            if getattr(monitor, "timecapsule", None) is not None:
                dash.set_timecapsule(monitor.timecapsule)
            port = getattr(config, "DASHBOARD_PORT", 7860)
            _dash_t = dash.start_in_background(port=port)
            # Print where it actually bound. Printing config's host was wrong
            # twice over: the tokenless-bind guard can move it to loopback, and
            # a failed bind printed a working URL for a dead server.
            print(f"[Dashboard] {getattr(_dash_t, 'dashboard_url', f'http://127.0.0.1:{port}')}")
        except Exception as e:
            print(f"[Dashboard] NOT running — {e}")

    shutdown_event = threading.Event()

    def shutdown(sig=None, frame=None):
        print("\n[Agent] Shutting down...")
        shutdown_event.set()
        if monitor is not None:
            monitor.stop()
        try:
            longterm.end_session(session_id, summary=agent.memory.summary)
        except Exception:
            pass
        try:
            from tools import browser as _b
            _b.close()
        except Exception:
            pass
        try:
            from tools import repl as _r
            _r.shutdown()
        except Exception:
            pass
        if not args.text:
            speak("Signing off.")
        sys.exit(0)

    from agent import awareness as _aw_report
    _aw_report.report_hand_tracking(monitor)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if monitor is not None:
        monitor.start()

    # TUI mode: hand off to the terminal UI, which owns the input loop.
    if args.tui:
        from tui.app import run_tui
        try:
            run_tui(agent)
        finally:
            shutdown()
        return

    # Greeting
    greeting = (
        "I'm online. I can see your screen, run commands, search the web, and control your computer. "
        "What are we building?"
    )
    speak(greeting)

    # Wake word mode setup
    wake_event = threading.Event()
    wake_listener = None
    if args.wake and not args.text and not args.tui:
        from voice.wake import WakeWordListener
        wake_listener = WakeWordListener(wake_phrases=config.WAKE_PHRASES)
        wake_listener.start(on_wake=wake_event.set)
        speak("Wake mode on. Say 'hey agent' to wake me.")

    # Stream STT partials to the dashboard if it's running
    def _on_partial(text: str):
        try:
            if config.DASHBOARD_ENABLED:
                from dashboard import server as _dash
                _dash.ws_manager.broadcast_threadsafe({
                    "type": "event", "ts": __import__("time").time(),
                    "source": "stt", "content": f"… {text}",
                })
        except Exception:
            pass

    def _voice_listen():
        if args.text:
            return listen()
        try:
            return listen_streaming(on_partial=_on_partial)
        except Exception as e:
            print(f"[STT] Streaming failed ({e}), falling back to listen().")
            return listen()

    # Main loop
    print("Press Ctrl+C to quit.\n")
    while not shutdown_event.is_set():
        # Get input
        if args.text:
            user_input = listen()
            if user_input is _EOF:
                # stdin is closed, not empty — there is no terminal on the other
                # end. Treating it as an empty turn sends us straight back to
                # input(), which raises again immediately: a hot spin that pins a
                # core and grows stdout without bound. The first real boot of
                # `main.py --text` with no tty produced 129MB of "YOU: " in 90s.
                # Anything that starts Apex detached — systemd, nohup, docker,
                # `&` — lands here on the very first read.
                #
                # Shutting down would be the other wrong answer: the dashboard,
                # scheduler and awareness monitor are already up and are the
                # whole point of an always-on agent. Only the prompt is
                # meaningless. So park until a signal arrives.
                print("[Agent] stdin closed — no terminal attached. Running "
                      "headless: dashboard, scheduler and awareness stay up. "
                      "Send SIGTERM (or Ctrl-C) to stop.", flush=True)
                shutdown_event.wait()
                break
        elif args.wake:
            # Wait for wake trigger, then do full listen
            wake_event.wait()
            wake_event.clear()
            user_input = _voice_listen()
        else:
            print("[Listening...]")
            user_input = _voice_listen()

        if not user_input:
            continue

        if user_input.lower() in {"quit", "exit", "bye", "goodbye", "stop"}:
            shutdown()

        # /model command — switch providers at runtime
        if user_input.startswith("/model"):
            parts = user_input.split(None, 1)
            if len(parts) < 2:
                from agent import provider as _prov
                lines = [f"Current model: {agent._model}", ""]
                # Ask each provider what it actually serves. The built-in list
                # is a convenience, not the authority — it is what went stale.
                for name, live in _prov.discover_all().items():
                    if live:
                        lines.append(f"{name} (live, {len(live)}): "
                                     + ", ".join(sorted(live)))
                    else:
                        builtin = sorted(m for m in _prov.KNOWN_MODELS
                                         if _prov.provider_for(m) == name)
                        lines.append(f"{name} (could not check — no key or "
                                     f"unreachable): {', '.join(builtin)}")
                speak("\n".join(lines))
            else:
                result = agent.set_model(parts[1].strip())
                speak(result)
            continue

        # /feedback +1 [comment] | /feedback -1 [comment] — rate the last completed turn
        if user_input.startswith("/feedback"):
            from agent import feedback, telemetry as _tel
            parts = user_input.split(None, 2)
            if len(parts) < 2 or parts[1] not in {"+1", "1", "-1", "up", "down"}:
                speak("Usage: /feedback +1 [comment]  |  /feedback -1 [comment]")
                continue
            rating = -1 if parts[1] in {"-1", "down"} else 1
            comment = parts[2].strip() if len(parts) > 2 else ""
            last_turn = _tel.current_turn()
            if last_turn < 1:
                speak("No turn to rate yet.")
                continue
            try:
                feedback.record(
                    rating, session_id=session_id, turn_index=last_turn,
                    comment=comment, source="cli",
                )
                speak(f"Recorded {'👍' if rating == 1 else '👎'} for turn #{last_turn}.")
            except Exception as e:
                speak(f"Feedback failed: {e}")
            continue

        # Voice/text feedback phrases ("thumbs up", "that was wrong") — short messages only
        if not user_input.startswith("/"):
            from agent import feedback, telemetry as _tel
            phrase_rating = feedback.detect_feedback_phrase(user_input)
            if phrase_rating is not None and _tel.current_turn() >= 1:
                try:
                    feedback.record(
                        phrase_rating, session_id=session_id,
                        turn_index=_tel.current_turn(),
                        comment=user_input, source="voice" if not args.text else "cli",
                    )
                    speak(f"Got it — recorded {'👍' if phrase_rating == 1 else '👎'}.")
                except Exception:
                    pass
                continue

        # /iot command — toggle IoT integration on/off or check status
        if user_input.startswith("/iot"):
            if not config.IOT_ENABLED:
                speak("IoT is not enabled. Set IOT_ENABLED=true in .env and restart.")
                continue
            from agent import iot as _iot_state
            parts = user_input.split(None, 1)
            sub = parts[1].strip().lower() if len(parts) > 1 else "status"
            if sub in {"on", "enable", "1", "true"}:
                _iot_state.set_enabled(True, source="cli")
                speak("IoT enabled.")
            elif sub in {"off", "disable", "0", "false"}:
                _iot_state.set_enabled(False, source="cli")
                speak("IoT disabled.")
            else:
                state = "enabled" if _iot_state.is_enabled() else "disabled"
                ha_url = config.IOT_HA_URL or "(not configured)"
                speak(f"IoT is currently {state}. HA URL: {ha_url}")
            continue

        # /council command — Claude, GPT, and Gemini debate to the best answer
        if user_input.startswith("/council"):
            parts = user_input.split(None, 1)
            if len(parts) < 2:
                speak("Usage: /council <question>")
            else:
                from agent import council
                print("\n[Council convening...]\n")
                result = council.convene(
                    parts[1].strip(),
                    rounds=1,
                    on_progress=lambda m: print(f"  [council] {m}"),
                )
                for entry in result.transcript:
                    tag = "Opening" if entry["round"] == 0 else f"Debate {entry['round']}"
                    print(f"\n--- {entry['label']} ({tag}) ---\n{entry['text']}\n")
                speak(f"COUNCIL VERDICT (members: {', '.join(result.members)}):\n\n{result.final_answer}")
            continue

        # /experts command — convene the Constellation of 12 domain-expert planets
        if user_input.startswith("/experts"):
            parts = user_input.split(None, 1)
            if len(parts) < 2:
                speak("Usage: /experts <question>")
            else:
                from agent import constellation
                print("\n[Constellation convening...]\n")
                result = constellation.convene(
                    parts[1].strip(),
                    on_progress=lambda m: print(f"  [constellation] {m}"),
                    on_answer=lambda key, text: print(
                        f"\n--- {constellation.PLANETS[key].codename} "
                        f"({constellation.PLANETS[key].display}) ---\n{text}\n"),
                )
                experts = ", ".join(p["display"] for p in result.planets)
                verdict = f"CONSTELLATION VERDICT (experts: {experts}):\n\n{result.final_answer}"
                if result.disagreement and result.disagreement.lower() != "the council agreed":
                    verdict += f"\n\nWhere they split: {result.disagreement}"
                speak(verdict)
            continue

        # Determine if this warrants deep thinking
        think = args.think or _needs_thinking(user_input)
        if think:
            print("[Thinking deeply...]")

        # Run agent — streaming when we have voice output for low-latency speak
        try:
            if args.text:
                response = agent.run(
                    user_input,
                    include_screenshot=not args.no_screenshot,
                    use_thinking=think,
                )
                speak(response)
            else:
                from voice.streamer import StreamingSpeaker
                streamer = StreamingSpeaker()
                streamer.start()
                response = agent.run(
                    user_input,
                    include_screenshot=not args.no_screenshot,
                    use_thinking=think,
                    streamer=streamer,
                )
                streamer.finish()
                print(f"\nAGENT: {response}\n")
        except Exception as e:
            response = f"Something went wrong: {e}"
            print(f"[Error] {e}")
            speak(response)


def _needs_thinking(text: str) -> bool:
    """Heuristic: trigger extended thinking for complex analytical or planning tasks."""
    keywords = [
        "plan", "design", "architect", "strategy", "analyze", "analyse",
        "think through", "compare", "evaluate", "pros and cons", "should i",
        "best way", "how would you", "explain why", "deep dive",
    ]
    low = text.lower()
    return any(k in low for k in keywords)


if __name__ == "__main__":
    main()
