# Apex

**A self-hosted, voice-first, always-on personal AI agent — MIT licensed, runs
on your own hardware, model-agnostic, no subscription required beyond the model
you choose to use.**

Repo: https://github.com/Alexkhouryyy/ni
License: MIT
Author: Alex Khoury

_Written as a submission-ready overview — current as of 2026-08-29. Numbers
below are measured from the repository, not estimated: 29,102 lines of Python
across `agent/`, `tools/`, `app/`, `dashboard/`, `skills/`; 9,529 lines of
frontend (JS/HTML/CSS); 1,529 tests across 83 test files; 104 tools the agent
can call; 27 dashboard tabs._

---

## The pitch

Most AI agents are a chat window. Apex is a persistent presence: it remembers
everything across every conversation, watches for what needs attention while
you're away, reaches you on whatever channel you're on, and runs entirely on
hardware you own. There is no vendor cloud in the loop except whichever model
provider you choose — and even that is swappable, because Apex speaks
Anthropic, OpenAI, Gemini, and local Ollama models through the same interface.

It ships as one Python process with a web dashboard, not a hosted SaaS product.
Clone it, add your API key, run it.

---

## What makes it different

- **One brain, not a chat log.** A single SQLite database holds memory, goals,
  skills, telemetry and every device is a window into the same state — start a
  conversation on your phone, finish it at your desk.
- **Model-agnostic by design.** Claude, GPT, Gemini, or a local Ollama model,
  switchable at runtime (`/model gpt-4o`) with zero code changes. A 3-way
  **council** mode has the models debate a question to a synthesized answer.
- **Actually autonomous, with a governor.** A background OODA loop (`cortex.py`)
  reads goals and world-state on a timer and proposes the next action — safe
  actions run automatically, risky ones stage for a push-notification approval.
  Nothing acts on your computer without that gate.
- **Self-improving.** When Apex hits a capability gap it can write its own tool
  (`skill_forge.py`), and a nightly reflection pass consolidates what worked —
  with automatic rollback if a change measurably hurts approval rates.
- **Reaches you everywhere.** Telegram, Discord, Slack, WhatsApp, Signal, and
  phone/SMS via Twilio, plus a installable PWA dashboard with web push — all
  backed by the same brain, from a Tailscale-reachable HTTPS URL if you want it.
- **Runs on your subscription, not per-token billing.** Turns can route through
  the Claude Agent SDK against your existing Claude subscription instead of
  metered API credits, with an automatic, honest fallback if that's unavailable.
- **Sees your hands.** A native MediaPipe-based hand tracker (no browser
  dependency, no third-party service) drives a 3D "glass board" you can
  literally reach into and drag, scale, and rotate cards and 3D models with two
  hands — architected so tracking survives the browser tab being backgrounded
  or closed, because Python holds the camera, not the page.
- **Operable from itself.** A Control tab lets you edit settings, see MCP server
  health, switch themes, pull updates, and restart the agent — no terminal
  required after first setup.
- **Extensible via MCP.** Connects to Model Context Protocol servers (Slack,
  Notion, Gmail, Calendar, and anything else in the ecosystem) and surfaces
  which ones are actually connected, rather than failing silently.

---

## Feature map

**Core agent** — multi-model routing (cheap-vs-flagship), extended thinking,
live screen vision, fallback on provider failure, persona tone that shifts with
the foreground app.

**Memory & knowledge** — SQLite + semantic embeddings + full-text search;
knowledge graph over entities; RAG over your own files; an Obsidian-compatible
Markdown vault; a "world model" live-context snapshot; a serendipity engine that
surfaces non-obvious links between memories.

**Autonomy** — the cortex OODA loop; awareness watchers (active window,
clipboard, files, camera-based hand tracking); a decision-moment mini-council
for judgment calls; long-horizon nudges; persistent cron/interval scheduling;
a learned "Restraint" model that decides when *not* to interrupt you.

**Self-improvement** — agent-written tools on a capability gap; Python and
Markdown skill formats; 7-day background skill maintenance; nightly reflection;
automatic rollback of regressions; a full feedback loop (thumbs up/down,
blind-comparison preference logging, evaluation).

**Multi-model reasoning** — Claude/GPT/Gemini debate-to-consensus council; a
standing panel of domain-expert "planets"; blind side-by-side model comparison
with a preference leaderboard.

**Safety & governance** — dangerous-command gating; write-approval staging;
spend-budget caps; revocable per-device dashboard tokens (a device can use Apex
without being able to reconfigure or restart it); a Docker/local execution
sandbox; every inbound webhook signature-verified.

**Reach & I/O** — bash, browser, computer control, camera, screen vision, live
web research, file access, a REPL, image generation, email (IMAP/SMTP),
calendar (CalDAV), IoT (Home Assistant), and channels: Telegram, Discord, Slack,
WhatsApp, Signal, phone/SMS via Twilio. One notification fans out to WebSocket +
Web Push + Telegram simultaneously.

**Skills shipped** — PC control, email triage, live research, website
publishing, a focused "work mode."

**Hand tracking & the glass board** — a native webcam hand tracker (thumb/index
pinch, wave, swipe, hold gestures) feeding a shared recognizer; a 3D board
where a pinch drags a card, two hands scale and rotate it — including real 3D
models (glTF) through a path-jailed props folder.

**The Control tab** — live settings editor (secrets masked, never sent to the
browser), MCP server connection status, five visual themes, git-pull update
checking, and a supervised restart — all gated to the master device token.

---

## The dashboard (27 tabs)

Overview (command center) · Live feed · Chat · Council · Compare · Documents
(writing-first AI editor) · Constellation (3D expert panel) · Goals · Memory ·
Knowledge graph · Reflections · Evolution (self-improvement ledger) · Learning ·
Telemetry (cost/usage) · Replay · Briefing · Schedule · Sub-agents · Knowledge
base · Self-mod · Phone · Inbox (email triage) · Calendar · Vision (camera) ·
Approvals · Research · Board (hand-tracked 3D board) · **Control**.

Served by FastAPI (~150 routes) with a WebSocket live stream, installable as a
PWA with a service worker, per-device token auth, and a fail-closed public bind
— Apex refuses to listen on a public interface without a token configured.

---

## Tech stack

Python 3.11 · FastAPI + uvicorn · SQLite (WAL mode, FTS5, sentence-transformer
embeddings) · APScheduler · Anthropic SDK (+ OpenAI-compatible interface for
GPT/Gemini/Ollama) · MediaPipe (Apache 2.0) for hand tracking · Three.js for the
3D board and constellation view · pywebpush (VAPID) for browser push · vanilla
JS single-page dashboard, no framework, no build step.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # add your API key(s)
python main.py --text         # text mode, no mic/speaker needed
```

`python main.py` alone runs full voice mode; `--resident` runs it always-on in
the background; `--wake` adds a wake word. Only `ANTHROPIC_API_KEY` is
required — adding `OPENAI_API_KEY` and/or `GEMINI_API_KEY` unlocks GPT/Gemini
and the multi-model council. Remote access works over Tailscale with a
dashboard password, or a one-command free-tier Oracle Cloud VM deploy under
systemd.

---

## Honest state — what's proven vs. what isn't

- **1,529 tests, 83 test files.** Deterministic logic (recognizers, safety
  gates, memory, scheduling, the props jail, the auth boundary) is covered with
  adversarial tests, not just happy-path ones — every guard in the codebase is
  reverted individually during development to confirm its test actually fails
  without it.
- **Hand tracking and the board are architecturally complete and unit-tested,
  but the last mile — real fingers in front of a real camera — has only been
  exercised on the author's own machine, not on a wide range of hardware.**
- The self-modification and skill-forging subsystems are real and tested, but
  are the newest and least battle-tested parts of the system; they ship
  disabled by default and require explicit opt-in.
- Every "risky" capability — computer control, tool self-writing, spend, and
  now dashboard restart/update — is gated behind either a master token, an
  approval step, or both. None of it is on by default.

---

## License

MIT. Use it, fork it, run your own version, no attribution obligations beyond
keeping the license notice. This is a from-scratch build — where it draws on
external protocols (MCP) or techniques from other projects (hand-tracking
landmark geometry, itself an unprotectable technique), that provenance is
documented in the relevant source file's docstring rather than left implicit.
