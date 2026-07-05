# Apex — Plan of Action

_Living roadmap. Sequenced by value-per-effort and hard dependencies (safety before
autonomy-expansion; data-collection before learning; correctness before scale).
Grounded in the v2 audit (`docs/audit/apex-full-audit-v2-2026-07.md`) and this
session's work. Last updated: 2026-07-03, HEAD `14143f1`._

Legend: **[do]** = no decision needed, safe to execute · **[decide]** = needs your call first.

---

## Phase 0 — DONE
- Full engineering audits (v1 + v2, 6 auditors).
- P0/tier-1 hardening: path-traversal guards, tar-slip, TOCTOU approve, timing-safe
  tokens, WhatsApp XML escape, memory-injection filter, WAL+busy_timeout.
- v2 P0: email-header XSS → token theft (closure-bound), pair-token leak (device
  token minted, master-gated), approval regression fixed, fail-closed write gate,
  broken budget/guardian/timecapsule toggles fixed, iot DB lock.

---

## Phase 1 — Close the remaining RCE surface (safety-critical)
Goal: Apex is safe to expose to anything not fully trusted (a tunnel, a group chat,
a web page it reads). These are the still-open Criticals from v2.

- **[decide] Sandbox on by default.** `config.py:119-120` → default `SANDBOX_REQUIRE=true`
  and/or `EXECUTION_BACKEND=docker`. Trade-off: hard Docker dependency. *Decision:
  require Docker, or keep host-default and only sandbox in "exposed" mode?*
- **[decide] Stop trusting model-declared `is_read_only`** (`skill_forge.py:205,229`).
  Route every forged tool through the approval queue instead of auto-installing.
  *Decision: OK to require approval for ALL forged tools (safer), or keep an
  auto-approve path gated by a static AST check for network/writes?*
- **[do] Add `run_python`/`run_skill`/`read_file` to `safety._RULES`** and make the
  cortex path honor `safety.check` (`cortex.py:117-145`, `safety.py:5-26`).
- **[do] Make `_validate_in_sandbox`/`_compile_check` never exec on host** — require
  the Docker backend for forged-skill validation (`skill_forge.py:161,292`).
- **[decide] Webhook signature verification** — Telegram secret-token, Twilio
  `RequestValidator`, Signal HMAC (`server.py:595-693`); drop allow-all-when-empty
  in the channel `_is_allowed` helpers. *Decision: you set the secrets; which
  channels are actually in use?*
- **[do] Offload Twilio/WhatsApp/Slack to `run_in_executor`**, ack immediately, dedupe
  on provider event id (`server.py:600,609,655,675`) — stops loop-freeze + double runs.

Risk: medium (behavior changes to autonomy/channels). Effort: ~3-4 days.

---

## Phase 2 — Correctness & cost (pure wins, no decisions) ← EXECUTE FIRST
Goal: make Apex faster, cheaper, and honest as it accumulates data. All clearly-correct,
no behavior/UX change, testable.

- **[do] `recall()` gets a `LIMIT` + candidate cap** (`longterm.py:296-320`) so it stops
  loading the entire memories table + all embeddings every call. Biggest latency win.
- **[do] `remember` content-length cap** (`longterm.py:279`) so one huge tool result
  can't bloat every future recall.
- **[do] FTS5 delete/update triggers** (`longterm.py:207-216`) so `search_turns` can't
  corrupt the day anything prunes `turn_log`.
- **[do] Route all providers through `telemetry`** (`provider.complete`, council,
  compare, documents, curator, resilience fallback) and enforce `budget` before
  multi-model fan-out — so spend caps actually see council/compare cost.
- **[do] IMAP socket timeout** (`email_box.py:95,134`); explicit provider client timeouts.
- **[do] Replace silent `except: pass`** on memory/schedule/recall paths with logged
  failures (the "looks fine while wrong" class).

Risk: low. Effort: ~2 days. **Start here.**

---

## Phase 3 — Surface what's already built
Goal: expose capability that exists but has no UI (27 unwired endpoints from the audit).

- **[do] Approval UIs for `pending-actions` (cortex) and `forged-tools` (skill_forge)**
  (`server.py:940-970,1132-1142`). Right now Apex stages autonomous actions + forged
  tools and notifies you, but there's no button to approve them. Highest value-per-effort
  product gap — mirrors the existing `staged-writes` inbox UI.
- **[do] Curator / world-state / perception glance panels** (endpoints already exist).
- **[do] Clean dead config** (`KB_INDEX_PATHS`, `MAX_SUBAGENTS`, `OCR_CONFIDENCE_THRESHOLD`,
  `SCREEN_HOTKEY`) and add the 6 `getattr` config names `rollback.py` claims are configurable.

Risk: low. Effort: ~2-3 days.

---

## Phase 4 — "Restraint" (the receptiveness model) — signature feature
Goal: Apex learns *when not to interrupt*. The governor on the autonomy we hardened.
See the pitch in chat. Built from parts that exist (`awareness._PROACTIVE_COOLDOWNS`,
`feedback.py`, `outcomes.py`, perception log, cortex notify path).

- **[do] `receptiveness` table** mirroring the Compare win-rate schema:
  `(interruption_type × context_bucket) → engaged/dismissed counts`.
- **[do] Gate the cortex notify path** on predicted value; replace static cooldowns
  with learned per-type/per-context suppression.
- **[do] ε-greedy curiosity budget** to avoid over-suppression collapse (the detail
  everyone gets wrong).
- **[do] "Held Back" dashboard panel** + calibration score.

Depends on: Phase 2 (clean feedback/outcome data). Risk: medium (UX). Effort: ~4-5 days.

---

## Phase 5 — The learning loop (reranker) — deepest
Goal: feedback actually reshapes behavior. The one thing no competitor has.

- **[do] Reward signal from Compare leaderboard + thumbs feedback** → rerank candidate
  responses (pick the model/answer your history prefers before you see it).
- **[decide] Later: fine-tune a small local model** on trajectory data (`turn_log` +
  outcomes). *Decision: worth the ML-infra pivot, or stay reranker-only?*

Depends on: Phase 2 (telemetry) + Phase 4 (richer signal). Risk: medium. Effort: reranker ~3 days.

---

## Phase 6 — Distribution (only if going public)
Goal: make Apex usable by someone other than you.
- **[do] Onboarding wizard** (`ni onboard` writing `.env` + Oracle/Tailscale path).
- **[decide] iMessage adapter** (BlueBubbles) — needs a Mac running the bridge.
- **[decide] Public skill hub** (agentskills.io-style) — needs a skill corpus first.

Risk: low-medium. Effort: onboarding ~1 day; the rest larger.

---

## Decision points needing your input (in order)
1. **Sandbox default** — require Docker, or host-default + sandbox-when-exposed?
2. **Forged-tool approval** — require approval for all, or keep gated auto-approve?
3. **Which channels are live** — so webhook auth targets the right ones.
4. **Public or private** — determines whether Phase 6 happens at all.
5. **Learning loop depth** — reranker only, or fine-tune later?

## Recommended immediate next action
Execute **Phase 2** (correctness & cost) now — it's pure upside, needs no decision, and
every later phase is better on clean data. Then bring you the Phase-1 decisions.
