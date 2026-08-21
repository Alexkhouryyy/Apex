# Apex — Gap Analysis

Feature-by-feature status for all 54 sections of the proposed `CLAUDE.md`,
checked against the code rather than recalled.

**Generated:** 2026-08-20 · **Basis:** 1008 tests, 13 smoke checks, 3 static audits

---

## Why the labels are not the ones that were asked for

`CLAUDE.md` §52 Phase B asks for `IMPLEMENTED / PARTIAL / MISSING /
NEEDS_REFACTOR / EXPERIMENTAL`. That set cannot express the state that actually
causes trouble in this repository.

On 2026-08-18, five features were found broken in one afternoon:
`generate_profile_digest` had never once run (it selected a column that does not
exist), local Ollama models were wired to the paid API, Opus was priced at 3× its
real rate, text mode wrote 129 MB in 90 seconds, and Apex did not know the date.

Every one of them would have been labelled `IMPLEMENTED`. The code existed, was
imported, and was called. **977 unit tests caught none of them.**

Apex fails open by design — subsystems catch their own exception, print a line,
and continue. That is good for uptime and fatal for an audit, because *"never
wired up"* and *"working perfectly"* look identical from outside. So
`IMPLEMENTED` is split:

| Label | Meaning |
|---|---|
| **WORKS** | Exists **and** something demonstrates it executing |
| **UNPROVEN** | Exists and is wired, but nothing proves it runs |
| **OFF** | Exists but disabled by default |
| **PARTIAL** | Some of the section is built |
| **MISSING** | No implementation |
| **NEEDS_REFACTOR** | Runs, but structurally wrong |

**No label without a citation.** A row that cannot cite evidence for WORKS is
UNPROVEN by definition, not by judgement. `tests/test_gap_analysis.py` enforces
this: every citation here must resolve to a file or test that actually exists.

---

## Summary

| Label | Count |
|---|---|
| WORKS | 27 |
| UNPROVEN | 10 |
| PARTIAL | 10 |
| OFF | 1 |
| MISSING | 13 |
| NEEDS_REFACTOR | 1 |

62 rows across 54 sections — some sections carry more than one row where the
document describes separable capabilities (e.g. §6 tracking, limits, prices and
cost-aware routing have four different answers).

**The headline is the UNPROVEN column, not the MISSING one.** The missing
features are mostly aspirational and known to be absent (3D printing, gesture
control, car integration). The unproven ones are features believed to work,
sitting in production, with nothing demonstrating they ever execute — the exact
category all seventeen bugs found so far have lived in.

Three are headline sections: **§11 self-improvement engine**,
**§17 personality**, and **§40 proactive intelligence**. No test references
`agent/skill_forge.py`, `agent/persona.py`, or `agent/proactive.py`.

---

## Core & Reasoning

| § | Feature | Label | Evidence |
|---|---|---|---|
| 3.1, 4 | Council: independent answers → critique → debate → judge | WORKS | `agent/council.py` — `_OPENING_SYS`, `_DEBATE_SYS`, bounded `rounds`, `_parse_verdict`; `tests/test_compare.py` |
| 4.5 | Council metadata (confidence, disagreement, contributors) | PARTIAL | `_parse_verdict` returns a verdict; no confidence or agreement-level field |
| 5 | Tiered routing (fast / smart / council / deep) | OFF | `config.py` — `SMART_ROUTING_ENABLED = False`. Implemented in `agent/router.py`, disabled by default |
| 5 | Automatic escalation decision | MISSING | Routing only ever *downgrades*; nothing escalates on uncertainty |
| 41 | Distinguishes model knowledge vs. retrieved vs. verified | PARTIAL | `agent/answers.py` cites only fetched sources; `agent/verification.py` exists. No explicit provenance class on an answer |
| 41 | Council must not create false confidence from correlated knowledge | UNPROVEN | Nothing measures correlation between council members |
| 49 | Orchestrator / model router / provider abstraction | WORKS | `agent/orchestrator.py`, `agent/router.py`, `agent/provider.py`; `tests/test_provider_routing.py`, `tests/test_model_discovery.py` |

## Memory

| § | Feature | Label | Evidence |
|---|---|---|---|
| 7.1 | Working memory | WORKS | `agent/memory.py`; `tests/test_memory.py` |
| 7.2 | Episodic memory | WORKS | `agent/longterm.py` `turn_log`, `sessions`; `tests/test_memory.py` |
| 7.3 | Semantic user memory | WORKS | `agent/longterm.py` `memories`; smoke check `tool_calls_take_effect` |
| 7.4 | **Project memory** (per-project context) | **MISSING** | No project scoping anywhere in the schema |
| 7.5 | Procedural memory | WORKS | `agent/lessons.py`; `tests/test_lessons.py` |
| 7.6 | Failure / correction memory | PARTIAL | `agent/lessons.py` proposes from failure rates; no structured `{error, cause, correction}` record |
| 8 | Memory write policy (classify before writing) | PARTIAL | `kind` + `importance` exist; no confidence, expiry, or contradiction handling |
| 8 | Editing / deletion / forgetting | WORKS | `agent/curator.py`; `tests/test_memory.py` |
| 9 | Retrieval + ranking rather than whole-DB injection | WORKS | `agent/longterm.py` FTS5 + `agent/reranker.py`; `tests/test_reranker.py` |
| 10 | Adaptive personalization | PARTIAL | `agent/prefs.py`, `me.md` digest. Digest had never run before 2026-08-18 (`tests/test_reflection.py`) |

## Learning & Self-Improvement

| § | Feature | Label | Evidence |
|---|---|---|---|
| 11 | **Self-improvement engine** (detect gap → propose skill) | **UNPROVEN** | `agent/skill_forge.py` — no test references this module |
| 12 | Skill system + registry + metadata | WORKS | `agent/skills.py`, `agent/skill_md.py`; `tests/test_skills.py`, `tests/test_skill_md_usage.py` |
| 12 | Skill metadata: success_rate, last_evaluated | PARTIAL | Name/description/version present; no reliability tracking |
| 13 | Skill creation safety (sandbox → approval → install) | WORKS | `agent/skills.py` AST parse not exec, `tools/sandbox.py`, `agent/approvals.py`; `tests/test_skill_autonomy.py`, `tests/test_sandbox.py` |
| 14 | Self-improvement logs (what changed, why, rollback) | PARTIAL | `agent/reflection.py` + `agent/rollback.py`; no numbered improvement record with evaluation result |
| 15 | Outcome learning | UNPROVEN | `agent/outcomes.py`, `tests/test_outcomes.py` exist, but nothing feeds real-world results back in |
| 42 | Reliability metrics | PARTIAL | `agent/telemetry.py` tracks cost/latency; `agent/eval.py` exists. No task-success or council-vs-single comparison |
| 43 | **Model reputation system** | **MISSING** | No per-task model scoring anywhere |
| 44 | Model performance learning | MISSING | No `{task_type, model, quality}` record |
| 47 | Reversibility (git for behaviour) | WORKS | `agent/rollback.py`; `tests/test_rollback.py` |

## Cost

| § | Feature | Label | Evidence |
|---|---|---|---|
| 6 | Cost tracking (provider, tokens, cost, latency) | WORKS | `agent/telemetry.py` → `usage_log`; smoke check `spend_is_recorded` |
| 6 | Budget limits + warning thresholds | WORKS | `agent/budget.py`, `budget_config`; `tests/test_model_discovery.py` |
| 6 | Correct prices | NEEDS_REFACTOR | Fixed 2026-08-19 — Opus was listed at $15/$75 against a real $5/$25. Prices are a hand-maintained table that will go stale again; `MODEL_PRICING_JSON` is the escape hatch, not a fix |
| 6 | Cost-aware routing (cheap first, escalate) | MISSING | Routing does not consider cost or confidence |
| 6 | Caching | PARTIAL | Anthropic prompt caching via `cache_control` (`agent/core.py`); no response cache for repeated requests |
| 6 | Bring Your Own API Key | MISSING | — |

## Interfaces & Reach

| § | Feature | Label | Evidence |
|---|---|---|---|
| 18, 19 | Local runtime | WORKS | `main.py`; smoke check `boot_completes` |
| 20 | Cloud runtime, shared intelligence layer | WORKS | `scripts/bootstrap-oracle.sh`, `scripts/apex.service`, `Dockerfile` |
| 20 | Authentication, secrets, rate limits | WORKS | `dashboard/server.py` `_auth`, `agent/access_tokens.py`; `tests/test_access_tokens.py`, `tests/test_ratelimit.py` |
| 21 | Mobile / PWA | WORKS | `dashboard/static/sw.js`, `dashboard/static/manifest.webmanifest`, QR pairing |
| 33 | Many channels (Telegram, Discord, Slack, WhatsApp, Signal) | WORKS | `tools/*.py` per channel; `tests/test_telegram.py`, `tests/test_discord.py`, `dashboard/webhook_auth.py` |
| 39 | Notifications with priority + quiet hours | WORKS | `agent/notify.py` VAPID push, `agent/restraint.py` holds; `tests/test_notify.py`, `tests/test_restraint.py` |
| 40 | **Proactive intelligence** | **UNPROVEN** | `agent/proactive.py` — no test references this module |
| 22 | Car / Drive Mode | MISSING | — |
| 50 | One brain, many interfaces | WORKS | Single SQLite brain; `docs/OMNIPRESENCE.md` |

## Perception

| § | Feature | Label | Evidence |
|---|---|---|---|
| 23 | Voice: STT → agent → TTS | WORKS | `voice/stt.py`, `voice/tts.py`; `tests/test_tui.py` covers the text path |
| 23 | Wake word | UNPROVEN | `voice/wake.py`, `--wake` flag; no test |
| 23 | Streaming speech, interruption, speaker recognition | PARTIAL | Streaming STT present; no speaker recognition |
| 24 | Vision / camera | UNPROVEN | `tools/vision.py`, `tools/screen_vision.py`, `agent/perception.py`; `tests/test_camera.py` covers the device API only, not understanding |
| 25, 26 | Hand tracking + gesture safety | MISSING | No mediapipe/gesture code |

## Hardware

| § | Feature | Label | Evidence |
|---|---|---|---|
| 30 | Raspberry Pi / IoT bridge | UNPROVEN | `agent/iot.py`, `agent/iot_watcher.py`; `tests/test_iot.py` covers `agent/iot.py`, nothing covers the watcher |
| 27, 28 | 3D printing + gesture-driven printer UI | MISSING | — |
| 29 | Physical object copying | MISSING | — |

## Safety & Governance

| § | Feature | Label | Evidence |
|---|---|---|---|
| 13, 31 | Confirmation for dangerous actions | WORKS | `agent/safety.py`; `tests/test_safety.py`, `tests/test_command_review.py` |
| 31 | **Explicit permission tiers 0–4** | **MISSING** | Safety is per-action, not tiered |
| 45 | Secrets kept external, never in frontend | WORKS | `.env` git-ignored, `scripts/set_env_key.py`; `tests/test_set_env_key.py` |
| 45 | Per-skill declared permissions | MISSING | Skill metadata has no permissions field |
| 46 | **Audit log of important actions** | **MISSING** | `tool_events` records tool calls, but there is no human-readable action log as described |
| 2 | Self-improvement observable/reversible/permission-aware | WORKS | `agent/approvals.py`, `agent/rollback.py`, `tools/autonomy_audit.py`; `tests/test_autonomy.py` |
| 16 | Mission Mode (goals, milestones, progress) | WORKS | `agent/goals.py`; `tests/test_goals.py` |
| 16 | Blocker identification + strategy adjustment | UNPROVEN | `agent/initiative.py` proposes from stalled goals; nothing measures whether adjustments help |

## UI

| § | Feature | Label | Evidence |
|---|---|---|---|
| 36, 38 | Dashboard with the described modules | WORKS | 26 tabs / 61 endpoints; smoke check `every_dashboard_tab_answers` — 60 ok, 1 unconfigured, 0 broken |
| 37 | Council visualization (live per-model state) | UNPROVEN | `/api/council/roster` exists; no test or smoke check for the live debate view |
| 17 | **Personality separated from reasoning** | **UNPROVEN** | `agent/persona.py` — no test references this module |
| 51 | Cinematic must not substitute for useful | WORKS | Stated and followed; the audits exist precisely to enforce it |

## Sections that are process, not features

| § | Item | Status |
|---|---|---|
| 32 | OpenClaw / Hermes integration audit | **DONE** — `docs/decisions/` (4 weighted-matrix reports). One port each; all shipped |
| 34, 35 | Conceptual roles / distinctive combination | Positioning, not code |
| 48 | Development priorities | Superseded — see below |
| 52 | Phase A architecture doc | Partly exists as `docs/audit/`, `docs/APEX_OVERVIEW.md` |
| 53 | Coding rules | Followed |

---

## What this changes about §48's priorities

§48 orders the work as a build ladder: stabilize core → cost → self-improvement
→ cloud → voice → vision → gesture → hardware → 3D printing.

Priorities 1 through 4 are largely **already built**. The measured constraint is
not missing features — it is nine UNPROVEN ones, three of which are headline
capabilities nothing has ever exercised.

The highest-value work available is not on that ladder:

1. **Prove or delete the nine UNPROVEN rows.** Start with `skill_forge`,
   `persona`, `proactive` — no test touches them, and two of the three run
   unattended.
2. **§7.4 project memory** — the largest genuinely missing piece of the memory
   architecture, and the one most likely to be felt daily.
3. **§43/§44 model reputation** — completely absent, and the thing that would
   make §5 routing decisions empirical instead of hardcoded.

Gesture control and 3D printing are correctly last, and §52 Phase D already says
not to start there.
