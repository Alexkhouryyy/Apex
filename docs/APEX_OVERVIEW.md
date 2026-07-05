# APEX — Complete Overview (feature-discussion briefing)

_A self-contained snapshot of the Apex agent, written to be pasted into a fresh
chat so you can brainstorm and add features with full context. Current as of
2026-07-03. ~25K lines Python + ~6.6K frontend, 30 test files._

---

## 1. What Apex is

Apex is a **voice-first, always-on personal AI agent** that runs on the user's own
hardware (laptop and/or a free Oracle Cloud VM), model-agnostic (Claude / GPT /
Gemini / Ollama with zero lock-in). It has deep persistent memory, autonomous
behavior, self-improvement, a web dashboard, and reaches the user across six
messaging channels + web push. It is a single "brain" (one SQLite DB) that every
device is a window into.

**Stack:** Python 3.11 · FastAPI + uvicorn · SQLite (+ sentence-transformers
embeddings, FTS5) · APScheduler · Anthropic SDK (+ OpenAI-compatible for GPT/
Gemini/Ollama) · pywebpush (VAPID) · vanilla-JS SPA dashboard with Three.js.

**Entry points:** `python main.py` (voice) · `--text` · `--tui` · `--resident`
(always-on background) · `--wake` (wake word) · `--think`.

**Deploy:** local, or one-command Oracle Cloud free-tier VM (`scripts/bootstrap-oracle.sh`)
under systemd, reachable from anywhere via Tailscale or Cloudflare tunnel.

---

## 2. Subsystems (what's actually built)

**Core agent** — `core.py` (the main loop, tool dispatch, extended thinking,
screen vision), `provider.py` (multi-model), `router.py` (cheap-vs-flagship
routing), `resilience.py` (fallback), `memory.py` (conversation memory with
compression), `persona.py` (JARVIS British-butler tone), `app_context.py` (shifts
tone to the foreground app).

**Memory & knowledge** — `longterm.py` (SQLite + semantic embeddings + FTS5 turn
search), `entities.py` (knowledge graph), `knowledge.py` (RAG over your files),
`vault.py` (Obsidian-compatible Markdown vault), `perception.py` (everything Apex
observed), `world_model.py` (live context snapshot), `threads.py` (serendipity
engine over embeddings — newest, uncommitted).

**Autonomy** — `cortex.py` (OODA loop that advances goals unprompted), `awareness.py`
(watchers: active window, clipboard, files + a review loop), `guardian.py`
(decision-moment mini-council), `timecapsule.py` (long-horizon nudges),
`scheduler.py` (persistent cron/interval/date tasks), `proactive.py`, `goals.py`.

**Self-improvement** — `skill_forge.py` (Apex writes its own tools on a capability
gap), `skills.py` + `skill_md.py` (Python + Markdown skills), `curator.py`
(7-day background skill maintenance), `reflection.py` (nightly learning),
`rollback.py` (auto-revert skill rewrites that hurt approval), `self_mod.py`
(controlled prompt/tool self-modification), `feedback.py` / `outcomes.py` /
`prefs.py` / `eval.py` (the feedback loop).

**Multi-model reasoning** — `council.py` (Claude+GPT+Gemini debate → chair
synthesis), `constellation.py` (12 standing domain-expert "planets"),
`compare.py` (blind side-by-side model testing + preference leaderboard).

**Safety & governance** — `safety.py` (dangerous-command gate), `approvals.py`
(write-approval staging), `budget.py` (spend caps), `access_tokens.py`
(revocable per-device dashboard tokens), `tools/sandbox.py` (Docker/local
execution backend).

**I/O & reach** — `tools/`: bash, browser, computer control, camera, screen
vision, research, files, repl, image_gen, email (IMAP/SMTP), calendar (CalDAV),
IoT (Home Assistant), and channels: telegram, discord, slack, whatsapp, signal,
phone (Twilio). `notify.py` fans one alert to WebSocket + Web Push + Telegram.
`mcp_client.py` connects MCP servers.

**Skills shipped** — control_pc, email_triage, live_research, publish_website, work_mode.

---

## 3. The dashboard (23 tabs)

`overview` (command center + 3D globe) · `live` (event feed) · `chat` · `council` ·
`compare` · `documents` (writing-first AI editor) · `constellation` (3D expert
planets) · `goals` · `memory` · `graph` (knowledge graph) · `reflections` ·
`evolution` (self-improvement ledger) · `telemetry` (cost/usage) · `replay` ·
`briefing` · `schedule` · `subagents` · `knowledge` · `selfmod` · `phone` ·
`inbox` (email triage) · `calendar` · `camera` (vision).

Served by `dashboard/server.py` (FastAPI, ~60 endpoints, WebSocket live stream).
PWA with service worker; per-device token auth; fail-closed public bind.

---

## 4. How memory / autonomy works (the important mental model)

- **One SQLite brain.** All memory, goals, skills, tokens, telemetry live in one
  DB. Memories carry an importance score (1–10) and a semantic embedding.
- **The cortex** ticks on a timer (in `main.py`/resident via the awareness review
  loop): reads goals + world-state + recent events, asks a cheap model for ONE
  safe next action, auto-executes read-only tools, and **stages** risky ones for
  your approval (push notification → approve from dashboard).
- **Self-improvement loop:** skill_forge writes tools → curator maintains them →
  reflection consolidates nightly → rollback reverts regressions. Feedback
  (👍/👎) and Compare preferences are logged.
- **Reach when away:** Telegram polling + dashboard over Tailscale/Cloudflare +
  Web Push, all backed by the always-on Oracle VM.

---

## 5. Current state & known gaps (from a fresh security+quality audit)

**Recently shipped this cycle:** Docker execution sandbox, Compare tab,
per-device tokens, Documents editor (+ agent write + vault export), 3 autonomy
bug fixes, and a large batch of hardening (path-traversal, XSS→token-theft,
pair-token leak, atomic approvals, injection filters, WAL, bounded recall, FTS
sync triggers). Full detail: `docs/audit/apex-full-audit-v2-2026-07.md`.

**Still open (roadmap `docs/ROADMAP.md`):**
- **Security (needs decisions):** sandbox is OFF by default; forged tools can
  auto-install on a model-declared "read-only" flag; Telegram/Twilio/Signal
  webhooks lack signature verification; empty channel allowlists = allow-all.
- **Correctness/cost:** council/compare/documents spend bypasses the budget caps;
  no data retention (logs grow unbounded); several silent `except: pass` sites.
- **Product gaps:** 27 backend endpoints have no UI — notably the cortex
  `pending-actions` and skill-forge `forged-tools` **approval flows can't be
  actioned from the dashboard** (only staged-writes has UI).

---

## 6. Where it's going (candidate features on the table)

- **Restraint** — a receptiveness model that learns *when not to interrupt*
  (per-context win-rate over your reactions), with a "Held Back" panel + a
  calibration score. The governor on autonomy.
- **Threads** (in progress) — surfaces non-obvious cross-domain links between
  memories using the embeddings already stored.
- **Learning loop** — turn Compare/feedback into a reward signal that reranks
  responses (the one thing no competitor has); optionally a local fine-tune later.
- **Distribution** — onboarding wizard, iMessage adapter, public skill hub.

---

## 7. Guardrails for adding features (context for the discussion)

- **One brain, additive modules.** New capability = a new `agent/*.py` or
  `tools/*.py` module + a dashboard tab, wired via `init_db()` at boot and an
  endpoint in `server.py`. Don't fork the DB.
- **Everything testable.** Deterministic cores get a `tests/test_*.py`; the suite
  is the safety net (30 files). Prefer pure logic that doesn't need live API calls.
- **Safety posture:** risky actions stage for approval; execution should route
  through the sandbox; secrets stay in `.env` (git-ignored); the dashboard is
  token-gated and fails closed.
- **Frontend:** vanilla JS in `app.js`; escape all user/agent content; bump the
  `?v=omniNN` + service-worker cache version on any frontend change.
- **Voice-first:** features should degrade gracefully to headless/voice, not
  assume a screen.

---

_To go deeper on any subsystem, ask for the file — e.g. "show me how the cortex
decides" (`agent/cortex.py`) or "how does skill_forge validate a tool"
(`agent/skill_forge.py`)._
