# APEX — Complete Standalone Briefing for Cowork

> Drop this whole file into a new Cowork tab. It is fully self-contained — you do
> not need repo access to reason about Apex from it. It describes what Apex is, how
> every subsystem works, the data model, the current state, known gaps, the roadmap,
> and the rules for adding features. Snapshot date: 2026-07-03.

---

## 0. TL;DR

Apex is a **voice-first, always-on, self-hosted personal AI agent**. It is
model-agnostic (Claude / GPT / Gemini / Ollama, no lock-in), has deep persistent
memory, acts autonomously, improves itself, and reaches its user across six
messaging channels + web push + a 23-tab web dashboard. It is a single "brain"
(one SQLite database); every device is a window into it. ~25,000 lines of Python
+ ~6,600 lines of frontend, 31 test files.

Its differentiators vs. comparable projects (OpenClaw, Hermes): **voice-native**,
**a real visual dashboard**, **truly local + model-agnostic**, and a **closed
self-improvement loop**. Its frontier gap is a **learning loop** — feedback is
logged but doesn't yet reshape behavior.

---

## 1. Tech stack & how to run it

- **Language/runtime:** Python 3.11.
- **Web:** FastAPI + uvicorn; a vanilla-JS single-page dashboard (`dashboard/static/app.js`) with Three.js 3D views; PWA with a service worker + VAPID Web Push.
- **Data:** one SQLite DB (`~/.voice_agent_memory.db`) holding memory, goals, skills, telemetry, tokens, etc. Semantic search via `sentence-transformers` embeddings (normalized float32 blobs) + SQLite FTS5 for keyword/turn search.
- **Models:** Anthropic SDK for Claude; OpenAI-compatible client for GPT, Gemini, and Ollama. `provider.py` abstracts them; `router.py` sends cheap queries to a small model and hard ones to the flagship.
- **Scheduling:** APScheduler (cron/interval/date), with downtime catch-up.
- **Entry points:** `python main.py` (voice) · `--text` · `--tui` · `--resident` (always-on background: tray, wake word, hotkey) · `--wake` · `--think` (force extended thinking).
- **Deploy:** local machine, and/or a **free Oracle Cloud Ampere VM** provisioned by one command (`scripts/bootstrap-oracle.sh`) running under systemd (`Restart=always`, boot-start). Reachable from anywhere via Tailscale (private) or Cloudflare tunnel (public). This is the "always-on brain"; the laptop is the perception layer.

---

## 2. The mental model (read this before proposing features)

- **One brain, many windows.** All state is in one SQLite DB. Every device/channel
  is a view into the same memory and context. Do not fork the DB per feature.
- **Memory:** each memory row has content, a `kind` (fact/preference/project/
  decision/note), an importance score (1–10), and a semantic embedding. `recall()`
  does semantic search blended with importance; `search_turns()` does FTS5 keyword
  search over the raw conversation log.
- **Autonomy (the cortex):** on a timer, `cortex.py` reads active goals + a
  world-state snapshot + recent awareness events, asks a cheap model for ONE safe
  next action, **auto-executes read-only tools** (search, recall, read_file,
  run_python) and **stages risky ones** (bash, write_file, send_email) for user
  approval — a push notification fires and the user approves from the dashboard.
- **Self-improvement loop:** `skill_forge` writes new tools when it hits a
  capability gap → `curator` maintains them on a 7-day cycle → `reflection`
  consolidates learning nightly → `rollback` reverts skill rewrites that hurt the
  approval rate. Feedback (👍/👎) and blind model-comparison preferences are logged.
- **Reach when away:** `notify.py` fans a single alert to WebSocket (open
  dashboards), Web Push (closed devices), and Telegram (fallback), backed by the
  always-on VM.
- **Voice-first:** every feature must degrade gracefully to headless/voice — never
  assume a screen is present.

---

## 3. Subsystem map (every module, grouped)

### Core agent
- `core.py` — the main agent loop: tool dispatch (`_execute_tool`, a large
  if/elif registry), extended thinking, screen vision, system-prompt assembly with
  prompt caching. The seam everything passes through (~2,100 lines).
- `provider.py` — multi-provider LLM adapter (Claude/GPT/Gemini/Ollama).
- `router.py` — query-complexity routing (cheap vs flagship).
- `resilience.py` — API-failure classification + OpenRouter fallback.
- `memory.py` — conversation memory with tiered compression + longterm persistence.
- `persona.py` — JARVIS British-butler personality layer.
- `app_context.py` — shifts tone to the foreground app (VSCode→reviewer, Slack→triage…).

### Memory & knowledge
- `longterm.py` — SQLite + semantic embeddings + FTS5; `remember`/`recall`/
  `search_turns`; enforces write-approval; the heart of persistence.
- `entities.py` — knowledge graph (entities + relations).
- `knowledge.py` — RAG over the user's actual files.
- `vault.py` — Obsidian-compatible Markdown vault (frontmatter + wikilinks).
- `perception.py` — durable log of everything Apex observed (FTS5).
- `world_model.py` — synthesizes goals + events + entities into a live snapshot.
- `threads.py` — **serendipity engine**: surfaces non-obvious cross-domain links
  between memories using their embeddings (backend built + tested; UI not yet wired).

### Autonomy
- `cortex.py` — the OODA loop (observe→orient→decide→act) that advances goals unprompted.
- `awareness.py` — watchers (active window, clipboard, files) + the review loop that drives the cortex; also proactive calendar/weather nudges with cooldowns.
- `guardian.py` — decision-moment detection with a mini-council intervention.
- `timecapsule.py` — long-horizon capture/surface with a heartbeat.
- `scheduler.py` — persistent cron/interval/date tasks with downtime catch-up.
- `goals.py` — strategic goals across horizons + self-evaluation.
- `proactive.py` — the older screenshot-only proactive monitor (fallback path).

### Self-improvement
- `skill_forge.py` — Apex writes its own tools on a capability gap (generate →
  validate → install), with a model-declared "read-only" auto-approve path.
- `skills.py` (Python skills) + `skill_md.py` (Markdown procedural runbooks).
- `curator.py` — background skill maintenance every 7 idle days (stale/archive, backup, report).
- `reflection.py` — nightly closed-loop learning + `me.md` user-profile digest.
- `rollback.py` — auto-revert skill rewrites that hurt approval rate.
- `self_mod.py` — controlled self-modification of prompt/tools (in-process overlay).
- `feedback.py` / `outcomes.py` / `prefs.py` / `eval.py` — the feedback machinery.

### Multi-model reasoning
- `council.py` — Claude + GPT + Gemini debate, a chair synthesizes the best answer.
- `constellation.py` — 12 standing domain-expert "planets" you can convene or chat 1:1.
- `compare.py` — **blind** side-by-side model testing; logs your preferences into a leaderboard.

### Safety & governance
- `safety.py` — pattern-based gate that intercepts dangerous tool calls for confirmation.
- `approvals.py` — write-approval staging queue (memory/note/skill/email).
- `budget.py` — daily + per-session spend caps.
- `access_tokens.py` — revocable, per-device dashboard tokens (hashed at rest).
- `tools/sandbox.py` — execution backend: `LocalBackend` (host) or `DockerBackend` (isolated container).

### I/O, channels, integrations
- `tools/`: `bash`, `browser`, `computer` (mouse/keyboard), `camera`, `screen_vision`,
  `research` (web search/browse), `files`, `repl`, `image_gen`, `email_box`
  (IMAP/SMTP), `calendar_box` (CalDAV read-only), `iot` (Home Assistant), and the
  channels: `telegram`, `discord`, `slack`, `whatsapp`, `signal`, `phone` (Twilio).
- `notify.py` — the cross-device notification hub.
- `mcp_client.py` — connects MCP servers configured in Claude Code settings.
- **Skills shipped:** `control_pc`, `email_triage`, `live_research`, `publish_website`, `work_mode`.

---

## 4. The dashboard (23 tabs)

Served by `dashboard/server.py` (FastAPI, ~60 endpoints + a WebSocket live stream).
Tabs: **overview** (command center + 3D globe), **live** (event feed), **chat**,
**council**, **compare**, **documents** (writing-first AI editor with inline edits),
**constellation** (3D expert planets), **goals**, **memory**, **graph** (knowledge
graph), **reflections**, **evolution** (self-improvement ledger), **telemetry**
(cost/usage), **replay**, **briefing**, **schedule**, **subagents**, **knowledge**,
**selfmod**, **phone**, **inbox** (email triage), **calendar**, **camera** (vision).

Auth: per-device bearer tokens; the server fails closed (refuses a public bind with
no token). PWA installable; service-worker-cached shell (versioned `?v=omniNN`).

---

## 5. Current state (recently shipped this cycle)

Docker execution sandbox · Compare tab (blind model testing) · per-device access
tokens · Documents editor (+ agent can write into it + export to vault) · 3 autonomy
bug fixes (cortex now runs in resident mode; scheduler catches up missed fires;
approvals actually execute) · Threads serendipity backend · a large hardening batch
(skill-name path-traversal guards, tar-slip fix, timing-safe token compares,
WhatsApp XML escaping, email-header XSS → token-theft fixed, pair-token leak closed,
atomic approvals, memory-injection filter, SQLite WAL, bounded `recall`, FTS sync
triggers, `remember` content cap, IMAP timeout).

---

## 6. Known gaps (from a fresh 6-auditor security+quality review)

**Security (needs product decisions):**
- Execution sandbox is **off by default** (`EXECUTION_BACKEND=local`); the cortex's
  auto-run `run_python` executes on the host with no `safety.check`.
- `skill_forge` can **auto-install a forged tool** based on a model-declared
  `is_read_only` flag that's never verified against the code.
- Telegram/Twilio/Signal inbound webhooks **lack signature verification**; empty
  channel allowlists mean **allow-all**.

**Correctness / cost:**
- Council/compare/documents model spend **bypasses the budget caps** (invisible to `/api/budget`).
- **No data retention** — turn/usage/skill logs grow unbounded.
- Several silent `except: pass` sites hide failures (memory/schedule/recall).

**Product gaps:**
- **27 backend endpoints have no UI** — most importantly, the cortex
  `pending-actions` and skill-forge `forged-tools` **approval flows can't be
  actioned from the dashboard** (only `staged-writes` has a UI). Apex asks for
  approval and there's no button to grant it.
- A few dead config keys; `rollback.py` documents thresholds that aren't in config.

---

## 7. Roadmap (sequenced by dependency)

1. **Close the RCE surface** — sandbox-by-default decision, stop trusting
   `is_read_only`, add `run_python`/`run_skill` to the safety gate, webhook
   signatures, async-offload the blocking channel handlers. *(needs 2 decisions)*
2. **Correctness & cost** *(pure wins, mostly done)* — bounded recall ✓, FTS
   triggers ✓, content caps ✓; still to do: route all providers through telemetry +
   enforce budget before fan-out; add retention.
3. **Surface what's built** — the pending-actions + forged-tools approval UIs
   (highest value-per-effort product gap), curator/world-state/perception panels.
4. **Restraint** — a receptiveness model that learns *when not to interrupt*
   (per-context win-rate over the user's reactions) + a "Held Back" panel + a
   calibration score. The governor on autonomy.
5. **Learning loop** — turn Compare/feedback into a reward signal that reranks
   responses; optionally a local fine-tune later. The one thing no competitor has.
6. **Distribution** *(only if going public)* — onboarding wizard, iMessage adapter
   (BlueBubbles), public skill hub.

**Open decisions the user still owes:** (a) sandbox default — require Docker or
host-default+sandbox-when-exposed? (b) forged-tool approval — require approval for
all, or gated auto-approve? (c) which channels are actually live? (d) public vs
private? (e) learning-loop depth — reranker only, or fine-tune?

---

## 8. Rules for adding features (so proposals fit Apex)

- **Additive modules.** A new capability = a new `agent/*.py` or `tools/*.py`
  module + (usually) a dashboard tab, wired via an `init_db()` call at boot and an
  endpoint in `dashboard/server.py`. Reuse the one SQLite DB via `longterm._conn()`.
- **Deterministic cores get tests.** The suite (31 files under `tests/`) is the
  safety net; prefer logic that doesn't require live API calls so it's testable.
- **Safety posture.** Risky/irreversible actions **stage for approval**; code
  execution should route through `tools/sandbox`; secrets live only in git-ignored
  `.env`; the dashboard is token-gated and fails closed.
- **Frontend discipline.** Vanilla JS in `app.js`; **escape all user/agent content**
  before `innerHTML`; bump the `?v=omniNN` asset version + the service-worker cache
  name on any frontend change (or clients serve stale assets).
- **Voice-first & proactive-aware.** Degrade to headless/voice; and remember Apex
  is genuinely proactive, so "when/whether to speak" matters as much as "what."

---

## 9. Good opening questions for a feature discussion

- "What's the single feature that would make Apex indispensable rather than
  impressive?" (restraint/receptiveness is the current lead answer)
- "Where does Apex do the wrong thing silently?" (the `except: pass` + no-retention
  classes)
- "What capability exists in the backend but has no way to use it?" (the approval UIs)
- "What would turn Apex from an orchestrator of other models into a system that
  learns *me*?" (the reranker/learning loop)

---

_This file is a point-in-time snapshot. For live code, the repo is `alexkhouryyy/ni`,
branch `claude/brainstorm-project-ideas-asUsT`; deeper docs live in `docs/ROADMAP.md`,
`docs/APEX_OVERVIEW.md`, and `docs/audit/apex-full-audit-v2-2026-07.md`._
