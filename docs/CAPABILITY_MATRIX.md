# Apex capability matrix

**Dated 2026-10-04**, against `main` at `a3db77c`. This is Phase 0 of
*Apex Environment Vision and Build Roadmap*: one table that separates what
works from what is only built.

**Baseline:**
- 3426 Python tests pass and 1 is skipped;
- 47 browser and script checks run in CI on every push;
- the static audits `tools/wiring_audit.py`, `sql_audit.py` and `autonomy_audit.py` run too.

Re-date this file whenever a row changes status. A row moves up only on
evidence, never because code was added.

## The statuses

| Status | Means | Evidence it needs |
| --- | --- | --- |
| **LIVE** | Seen working on your own PC, phone or car | You ran it and reported the result. The date is given |
| **PARTIAL** | Part of it is LIVE, part is only tested | Says which part is which |
| **TESTED** | Passes automated tests in this repo, but nobody has seen it on your machine | The named tests or checks |
| **NEEDS YOU** | Built and tested, but waiting on an account, key or deploy that only you can do | The exact step left |
| **PLANNED** | Written in a plan doc; there is no code | The doc |
| **UNAVAILABLE** | Not possible, or ruled out on purpose | The reason |

"Tested" means fixtures and stubs. For example, no test talks to the real
Twilio, Railway, DeepSeek or a real MCP client on Windows unless the row says
so. Test counts are per file, as collected on 2026-10-04.

## Summary

| Status | Rows |
| --- | --- |
| LIVE | 3 |
| PARTIAL | 4 |
| TESTED | 34 |
| NEEDS YOU | 9 |
| PLANNED | 3 |
| UNAVAILABLE | 3 |

**The honest reading:** breadth is not the gap. Most of Apex is TESTED, and
only a handful of things have been seen working where they matter. The
fastest way to raise the LIVE count is not new code. It is the
**NEEDS YOU** column and the measurements listed at the end.

Related: the acceptance test is in [`DEMO.md`](DEMO.md), and outside projects reviewed for three of these gaps are in [`OPEN_SOURCE_REGISTER.md`](OPEN_SOURCE_REGISTER.md).

---

## 1. Core: brain, memory, learning

| Capability | Status | Evidence | Left to do |
| --- | --- | --- | --- |
| Chat with tools (one agent, ~100 tools, provider routing between Anthropic, DeepSeek and local) | **LIVE** | You run it daily with `Start-Apex-*.cmd`. Tests: `test_provider_routing` 52, `test_provider_deepseek` 55, `test_model_capabilities` 46, `test_smoke` 23 | — |
| Long-term memory that survives restarts (SQLite) | **LIVE** | Restart-verified (Blueprint Phase 1). Tests: `test_memory` 22, `test_memory_lazy_runtime` 3 | — |
| Semantic recall and reranking | **TESTED** | `test_reranker` 12, `test_rerank_wiring` 11, `test_reranker_integrity` 6. The sandbox has no `sentence-transformers`, so only the keyword fallback runs here | Confirm `matched: semantic` appears on your PC |
| Obsidian vault and vault search | **TESTED** | `test_vault_index` 28 | — |
| Word, PowerPoint, Excel and PDF as text (read_file, knowledge base, demo); scans reported as needing OCR | **TESTED** | `test_doc_convert` 15 (every guard broken once on purpose and caught); `scripts/doc_acceptance.py` sample set: 5/5 pass, each file under 0.1 s after a one-time 0.8 s load | Run `doc_acceptance.py` on about 10 of your own files (`docs/DOCUMENTS.md`) |
| Lessons from measured tool failures | **TESTED** | `test_lessons` 26, `test_observed` 44, `test_outcomes` 14, `test_outcome_measurement` 16 | — |
| Reflection, feedback and trajectory | **TESTED** | `test_reflection` 26, `test_reflection_heartbeat` 14, `test_feedback` 28, `test_trajectory` 8 | — |
| Skills: procedural, SKILL.md imports and the skill forge | **TESTED** | `test_skills` 13, `test_skill_md_usage` 8, `test_skill_forge` 28, `test_skill_autonomy` 8 | — |
| Goals, initiative, scheduler and time awareness | **TESTED** | `test_goals` 32, `test_initiative` 22, `test_scheduler` 8, `test_time_awareness` 12 | — |
| Approvals (nothing outward-facing without your yes) | **TESTED** | `test_cortex_approval` 3, `test_safety` 25, `test_restraint` 26, `test_autonomy` 8 | — |
| Self-modification with rollback | **TESTED** | `test_self_mod` 28, `test_rollback` 14, `test_recovery` 12 | — |
| World state: what's true right now, fresh for 30 min | **TESTED** | `test_world_state_fresh` 3 | — |
| Minimum complete demonstration (projects, restart, isolation, app write with receipt, recovery) | **TESTED** | `scripts/apex_demo.py`: 15 pass and 1 unknown in fixture mode; `test_apex_demo` 5, including two injected faults that each fail their check | Run `scripts\apex_demo.py --live` and send the summary (`docs/DEMO.md`) |
| Task suite: two representative tasks through the task runner, judged by rules, cost from Apex's ledger | **TESTED** | `scripts/apex_tasks.py`: 2/2 pass in fixture mode; `test_apex_tasks` 7, including two injected faults that each fail only their own check | Run `apex_tasks.py --live` (and once per model you want to compare), then send `--history` (`docs/TASKS.md`) |

## 2. Voice and companion

| Capability | Status | Evidence | Left to do |
| --- | --- | --- | --- |
| Celine's voice (local Qwen, streamed) | **PARTIAL** | LIVE: you run `Start-Apex-Celine-Fast.cmd` and hear her. Not measured: Pillar 1 latency (median ≤ 1.5 s, p90 ≤ 2.5 s over 20 turns). Tests: `test_qwen_fast_server` 8, `test_speak_stream` 9, `test_voice_timing` 25, `test_celine` 16 | 20 spoken turns, then `python -m agent.voice_timing`, and send the output (`docs/PROVE_IT.md` §1) |
| Companion (`/companion`): chat, Voice mode, Look now | **PARTIAL** | LIVE: you use it. Not measured: barge-in stop time on the laptop (simulated 0.25 s), and wake-word accuracy. Tests: `test_companion` 23, `test_look_now` 35, `test_wake` 25 | PROVE_IT §1b |
| Apex's character: the Mk I armoured suit (reflective metal, selective glow, suit-up sequence), or your own rigged model, in place of the orb; voice drives the grille or mouth | **TESTED** | `check_avatar_pose.cjs` (incl. bone names across Mixamo/VRoid/Blender), `check_avatar_ui.cjs`, `check_voice_stream_ui.cjs`; `check_avatar_browser.cjs` in real Chromium; every state, the suit-up, car and phone layouts, and a Mixamo model through the model slot checked by eye | Turn on **Apex appears as: Character** and judge it on the laptop and in the car; for a film-quality look, add your own model (`docs/AVATAR.md`) |
| Photoreal video avatar: real footage of the character, mouth redrawn per sentence by MuseTalk on your GPU | **NEEDS YOU** | `test_video_avatar` 8 (encoding, server rules, Apex routes against a real server, installer), `check_video_avatar_ui.cjs` (every guard broken once and caught), `check_video_avatar_browser.cjs` (real clips in Chromium). The MuseTalk engine itself has not run: it needs your GPU | `Setup-Apex-Video-Avatar.cmd`, make the idle video, `Start-Apex-Video-Avatar.cmd`, then choose **Video (photoreal)** and send the render times (`docs/VIDEO_AVATAR.md`) |
| Several voices, including an Alex voice | **NEEDS YOU** | `test_voices` 18. The library, both voice servers and the Voices tab are built | Record about 15 s of your voice in **Voices**, then pick it (`docs/VOICES.md`) |
| Speech model for hands-free (tells voice from noise; sends 0.7 s after you stop instead of 1.2 s) | **NEEDS YOU** | `test_speech_model` 8, `check_speech_detector_ui.cjs` (every guard broken once on purpose and caught). The real model loads and runs in Chromium: speech scored 0.6–0.98; steady noise and hum about 0.01, with one 0.2 s spike to 0.72 at a sound change | Run `Setup-Apex-Speech-Model.cmd`, do about 10 hands-free turns each way, then send `agent.voice_timing` |
| Voicebox desktop voice | **TESTED** | `test_voicebox` 14, `test_voicebox_live` 23 (live against a stub) | Only if you use Voicebox |
| Speech-to-text (faster-whisper; browser speech as fallback) | **TESTED** | `test_browser_stt` 4, plus the companion tests | — |

## 3. Hands, board and 3D

| Capability | Status | Evidence | Left to do |
| --- | --- | --- | --- |
| One-hand pinch-grab on `/board` | **LIVE** | First observed 2026-09-22. G1 (≥ 18/20 per hand) reported passed 2026-09-23, without counts. Tests: `test_pinch_hysteresis` 44, `test_handtrack` 91, `test_calibrate_pinch` 38 | Record the counts next time |
| Two-hand pull, rotate and scale | **PARTIAL** | "Two-hand scale works" is part of G1 (reported). Your retest of the two-hand pull since the last fixes is pending. Tests: `test_board` 92, `test_gesture_misfires` 21 | Retest the two-hand pull; send the gesture recorder output |
| Gestures: point "this", flick, swipes | **TESTED** | `test_main_gestures` 10, `test_gesture_recorder` 11, `test_study_pointing` 3 | Gesture recording on your camera |
| Build and edit 3D by voice (`board_build`, Blender) | **TESTED** | `test_build3d` 33, `test_build_create` 15, `test_blender_bridge` 45, `test_board_create` 5 | — |
| Named workspaces and parts mode | **TESTED** | `test_board_workspaces` 8, `test_board_parts` 20 | — |
| Study library (jet engine, heart, car engine) with motion | **TESTED** | `test_study_library` 22, `test_study_motion` 10, `test_study_import` 16, `scripts/check_study_motion.mjs` | Check the car engine cycle looks right to you |
| Forge: printable STL or 3MF only after the mesh passes | **TESTED** | `test_forge` 106, `test_forge_tool` 27. No real print yet | Print one part |

## 4. Agents that keep working

| Capability | Status | Evidence | Left to do |
| --- | --- | --- | --- |
| Missions: work until done, stuck, out of budget or needs you | **TESTED** | `test_missions` 24. Command checks need Docker | Run one real mission with Docker Desktop on |
| Team workspace (specialists) and council | **TESTED** | `test_team` 18, `test_orchestrator` 15, `test_council_stats` 31, `test_consensus` 11 | — |
| Genesis (testable hypotheses, with a gate) | **TESTED** | `test_genesis` 103, `test_genesis_tool` 26 | — |
| Deep research | **TESTED** | `test_deepresearch` 19 | — |
| Sandbox and code execution | **TESTED** | `test_sandbox` 12 | — |

## 5. Apex inside your other AI tools, and their tools inside Apex

| Capability | Status | Evidence | Left to do |
| --- | --- | --- | --- |
| Apex as an MCP server (Claude Code, Codex, Desktop, Cursor) | **PARTIAL** | LIVE: the raw stdio exchange passed on your Windows PC. Not confirmed: the real-client test, which timed out there before it was fixed and is required again. Tests: `test_mcp_server` 8 | Run `scripts\apex_mcp.py --self-test` and send the output, then `Setup-Apex-MCP.cmd` |
| Apex using other MCP servers, behind a read/write gate | **TESTED** | `test_mcp_client` 20, `test_mcp_policy` 71, `test_mcp_http` 39, `test_mcp_catalog` 30. Real server annotations have never been parsed in use (Blueprint Phase 5) | Connect one real server |
| Apps via Composio (Gmail, Calendar and so on) | **NEEDS YOU** | `test_apps` 32 | A Composio key and sign-ins in **Apps** (`docs/mcp-connections.md`) |
| Plugins from GitHub | **TESTED** | `test_plugins` 29 | — |

## 6. Phone, cloud and car

| Capability | Status | Evidence | Left to do |
| --- | --- | --- | --- |
| Telegram | **TESTED** | `test_telegram` 4. The token is in your `.env`, but no test message has been recorded | Send one message, and say if it answered |
| Discord, WhatsApp, Signal and Slack channels | **TESTED** | `test_discord` 14 and `test_webhook_auth` 38. WhatsApp, Signal and Slack only have auth and audit tests | Only the channels you want |
| PWA and the same brain on every device | **TESTED** | `test_devices` 9, `test_continuity` 14 | — |
| Cloud relay: phone answers while the laptop is shut (G2) | **NEEDS YOU** | Proven on localhost: `test_relay` 30, `test_relay_server` 29, `test_relay_phone` 31, `test_relay_answer` 19, `test_relay_drain` 32, `test_relay_check` 19, `test_relay_railway` 4 | Railway steps 1–6 (`docs/RELAY_RAILWAY.md`), then shut the lid and ask from the phone |
| Car page and orb screen (`/drive#orb`) | **NEEDS YOU** | `test_car_origin` 6, `test_car_setup` 4, `test_drive_spatial` 11, `check_car_link.cjs`, `check_orb_screen_ui.cjs` | `Setup-Apex-Car.cmd`. For the cloud orb, set `DASHBOARD_TOKEN`, an AI key and `OPENAI_API_KEY` on the Railway Apex |
| Call Apex through Uconnect (Twilio) | **NEEDS YOU** | `test_call_apex` 9, `test_relay_calls` 6. The calls are signed and checked against a caller list | The relay first, then Twilio (a number, SID and token) and the webhook (`docs/CALL_APEX.md`) |
| Local node: the cloud hands tasks to your PC | **NEEDS YOU** | `test_node_tasks` 31, `test_node_worker` 29 | Needs the relay deployed |
| Smart home (IoT) | **NEEDS YOU** | `test_iot` 22, `test_iot_watcher` 14 | A Home Assistant address and token, and an entity allowlist |

## 7. World View and Apocalypse

| Capability | Status | Evidence | Left to do |
| --- | --- | --- | --- |
| World View: globe, layers, flights, satellites | **TESTED** | `test_world_view` 5, `test_world_layers` 8, `test_world_flights` 23, `test_world_satellites` 6 | Open `/world` on your PC and say what loads |
| Live panel with Ask Celine about what's on screen | **TESTED** | `test_world_live` 8, `check_world_live_ui.cjs` | Same |
| God's Eye View engine (`/world/engine/`, upstream pinned) | **TESTED** | `test_world_engine` 22, `test_world_setup` 7, `test_world_windows_launcher` 5 | Any provider keys its layers need |
| Apocalypse: offline session with Project NOMAD | **TESTED** | `test_apocalypse` 19 | Download the offline packs while online |

## 8. Planned, and not there

| Capability | Status | Where |
| --- | --- | --- |
| Pillar 2: a full session with no keyboard on the board | **PLANNED** | `docs/APEX_V2_PLAN.md` |
| Documents per project | **PLANNED** | Found by the demo: documents are one shared list, linked to a project only by its handoff | — |
| Pillar 3: "receipts" for every claim Apex makes | **PLANNED** | `docs/APEX_V2_PLAN.md` |
| CarPlay or Android Auto app | **UNAVAILABLE** | A 2011 Uconnect has neither. Apple and Google gate both. Call Apex and the car page cover the car |
| Celine's own voice on a phone call | **UNAVAILABLE** | She runs on your PC's GPU, which a call can't reach. Calls use Twilio's voice |
| ChatGPT as an Apex MCP client | **UNAVAILABLE** | It needs a public OAuth server, and Apex's server is local on purpose |

## What would raise the LIVE count fastest

The full ordered list, with commands, is [CHECKLIST.md](CHECKLIST.md).

Every item is on your side. None needs new code.

1. **Voice timing:** 20 turns, then `agent.voice_timing`. This makes Pillar 1
   measured instead of felt.
2. **MCP self-test:** one command. It closes the MCP row.
3. **The Railway relay, steps 1–6.** This unlocks four rows: G2, the phone
   while the PC is off, Call Apex and the local node.
4. **Alex voice:** 15 seconds of recording.
5. **Telegram:** one message.
6. **The two-hand pull retest with the gesture recorder,** with counts.

Send back each result, and the row changes status here with its date.
