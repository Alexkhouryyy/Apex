# Apex — Full Engineering Audit

_Date: 2026-07-03 · Method: 4 parallel subagent auditors, each scoped to a slice, all findings cited to `file:line` and verified against current code (not docstrings). ~31K lines reviewed (24.6K Python + 6.6K frontend)._

---

## Executive summary

Apex is architecturally strong and feature-rich, but it is an **always-on agent with full host tool access** (bash, `run_python`, `run_skill`, computer control) plus a public-facing dashboard and six inbound messaging channels. That means **the blast radius of nearly any gap is host RCE (remote code execution) as your user account.**

The audit found **one dominant theme repeated across three independent slices**: untrusted input (LLM decisions, inbound messages, forged skills) reaches host code execution with no sandbox and no effective safety gate in the default configuration. The Docker sandbox built earlier this session is the right fix — but it is opt-in and **off by default**, and several execution paths never route through it at all.

**Severity tally:** 6 Critical · 8 High · ~13 Medium · ~10 Low · 4 significant test gaps.

Nothing here means Apex is "broken" — it runs. It means that **before Apex is exposed to anything you don't fully trust (a public tunnel, a group chat, a web page it reads), these paths are exploitable.**

---

## CRITICAL — fix before any untrusted exposure

### C1 — Default config runs LLM-chosen and message-driven code directly on the host
The autonomous cortex marks `run_python` as auto-execute (`agent/cortex.py:27` `"run_python":"always"`), executes it at `cortex.py:131-135`, and the default backend is `local` (`config.py:119` `EXECUTION_BACKEND=local`, `SANDBOX_REQUIRE=false`) → a plain host subprocess (`tools/sandbox.py:91-102`). The code is chosen by an LLM from goals + world state + **recent awareness events** (web pages, clipboard, screen OCR, `POST /api/awareness/ingest`). `safety.check()` is **never called** on this path (it only runs in the interactive loop at `core.py:1200`), and there is no `run_python` safety rule anyway. **Prompt-injection → unattended host RCE.**

### C2 — Installed skills execute on the host in-process; the "sandbox" only covers a one-time test
`agent/skills.py:48-50` (`exec_module`) and `:86` (`run()`) run skill code in the main agent process — never through the sandbox. `create_skill` runs `exec(compile(...))` at **install time** (`skills.py:165`), so a skill whose top-level body is `import os; os.system(...)` executes the moment it's created. The only sandbox call in the whole lifecycle is the one-time validation probe in `skill_forge._validate_in_sandbox` (`skill_forge.py:161`).

### C3 — Auto-approve is model-controlled and bypasses the safety gate
`skill_forge.py:205` trusts the model's own JSON: `is_read_only = proposal.get("is_read_only", False)`. If true, `skill_forge.py:229-231` → `self_mod.register_new_tool` **directly**, skipping `core._execute_tool`, so `safety.check("register_new_tool")` never fires. A prompt injection that sets `is_read_only:true` ships a host-executing tool with zero human interaction.

### C4 — `run_skill` path traversal → load & execute any `.py` on disk
`skills._skill_path` = `SKILLS_DIR / f"{name}.py"` (`skills.py:24`); `run_skill` does **not** validate `name` (`skills.py:75-78`). `run_skill("../../../tmp/evil", …)` loads and `exec_module`s an arbitrary file. `run_skill` is a first-class agent + expert tool (`core.py:1542`, `constellation.py:584`).

### C5 — Unauthenticated, spoofable inbound webhooks (Telegram, Signal, Twilio SMS/Voice/WhatsApp)
`server.py:136-140` lists these in `_WEBHOOK_PATHS`, so the auth middleware **skips them** (`server.py:166-167`). But unlike Discord (Ed25519, `server.py:628`), Slack (HMAC, `:647`), and IoT (HMAC, `:705`), these five verify **nothing**:
- Telegram (`server.py:612`, `tools/telegram.py:147`) — no `X-Telegram-Bot-Api-Secret-Token` check.
- Signal (`server.py:687`, `tools/signal.py:84`) — no signature.
- Twilio SMS/Voice/WhatsApp (`server.py:594,603,671`) — no `X-Twilio-Signature`.

The only gate is a per-channel allowlist that (a) **defaults to allow-all when unset** (`_is_allowed` returns `True` for empty lists — `telegram.py:54`, `signal.py:57`, `phone.py:44`, `whatsapp.py:59`) and (b) is bypassed anyway since the attacker controls the `chat_id`/`source`/`From` field. Every inbound message runs the full agent → RCE.

### C6 — Master `DASHBOARD_TOKEN` leaks to device tokens via `/api/pair/info` and `/api/pair/qr`
`_pair_url()` embeds the master secret (`server.py:891`), and `/api/pair/info` (`:895`) and `/api/pair/qr` (`:1217`) have **no `_require_master` gate**. Any revocable device token can `GET /api/pair/info`, read the master token, and become master — defeating the entire per-device revocation design. The master token never rotates, so a logged QR/URL exposes it permanently.

---

## HIGH

- **H1 — Safety gate has no rule for the two highest-blast-radius tools.** `safety._RULES` (`safety.py:5-26`) covers `bash`/`write_file`/`browser_*` but **not `run_python` or `run_skill`** → both fall through to `(True,"")` and execute unguarded (`core.py:1200`). Cortex bypasses `safety.check` entirely for its allowlist tier.
- **H2 — `curator.rollback` tar-slip arbitrary write.** `curator.py:217-219` `tarfile.extractall()` with no `filter=`/member validation; backups dir is writable by any host-running skill → a planted tar with `../../.ssh/authorized_keys` escapes the tree.
- **H3 — `skill_md.manage` path traversal + approval drift.** `name` never validated (`skill_md.py:20`); only `create` consults `SKILL_WRITE_APPROVAL` — `edit`/`patch`/`delete` are never staged, so the agent can rewrite/delete any skill unapproved.
- **H4 — Slack events processed twice + freeze the event loop.** `server.py:655` runs `dispatch_event` synchronously in the async handler (blocks the whole loop for a full agent turn), returns `None`, then `:659` schedules it **again** → double reply, double cost, and Slack retries (missed 3s ack) → 3rd/4th run.
- **H5 — Twilio/WhatsApp inbound run the agent synchronously in `async def`** (`server.py:594,603,671`) → event-loop freeze for every SMS/call/WhatsApp turn + Twilio retries → duplicate charged replies. (Telegram/Signal correctly use `run_in_executor`; these don't.)
- **H6 — TwiML injection in WhatsApp replies.** `tools/whatsapp.py:116` interpolates the reply **unescaped** into `<Response><Message>…`; `phone.py` escapes the identical output. A reply containing `&`/`<` corrupts the message or injects Twilio verbs (redirect flow to attacker TwiML).
- **H7 — Persistent self-prompt modification from any token.** `POST /api/selfmod/prompt` (`server.py:385`) permanently alters the agent's own system prompt with no master gate and no confirmation — a durable backdoor primitive.
- **H8 — WebSocket auth bypasses the throttle + non-constant-time compare.** `/ws/live` (`server.py:1622`) runs outside the HTTP middleware, so `AuthThrottle` never applies → unthrottled token-guessing oracle; both WS (`:1629`) and master (`:177`) checks use `==` not `hmac.compare_digest`.

---

## MEDIUM

- **M1 — `approvals.approve` TOCTOU double-apply** (`approvals.py:133-143`): read-then-update in separate connections, no atomic compare-and-set → concurrent `approve(id)` + `approve("all")` both apply → duplicate emails/writes.
- **M2 — `recall` loads the entire memories table** with no `LIMIT` (`longterm.py:291-311`), scoring cosine in Python; `remember` caps nothing (`:276`) → unbounded CPU/mem, injectable DoS.
- **M3 — Injection filter guards `add` but not `replace`** (`longterm.py:457` vs `:479-488`) → clean channel to inject text into the system prompt (files are read into the prompt at session start).
- **M4 — SQLite has no `busy_timeout`/WAL** (`longterm.py:261-268`); background threads contend → `database is locked` errors swallowed (`skills.py:108`, `curator.py:45`) → lost usage/audit/telemetry rows that rollback & curator depend on.
- **M5 — Docker backend hardening gaps** (`sandbox.py`): single persistent writable `/work` shared across runs (one skill plants files another executes), no `--read-only`/`--tmpfs`, no in-container timeout.
- **M6 — SSRF via `/api/push/subscribe`** (`server.py:840`, `notify.py:164`): attacker-controlled `endpoint` URL is POSTed to on every notify → `http://169.254.169.254/…` internal reach.
- **M7 — Throttle keyed on `request.client.host`** (`server.py:168`) is useless behind the documented Cloudflare/Tailscale tunnel (all requests share the tunnel IP) → global lockout DoS + no real brute-force protection; no `X-Forwarded-For`.
- **M8 — Cost/budget blindness for non-Anthropic providers.** `provider.complete()` (`provider.py:419`) calls `client.messages.create` directly, bypassing `telemetry`; GPT/Gemini/Ollama spend records as `$0` → council/compare/constellation fan-out cost is invisible to `/api/budget`.
- **M9 — Missing network timeouts** (OpenAI `provider.py:397`, IMAP `email_box.py:95,134`) → a hung provider/IMAP pins a threadpool thread ~600s; fan-out can exhaust the pool and wedge `/api/chat`.
- **M10 — Telegram replies hard-coded `parse_mode=Markdown`** (`telegram.py:63-79`) → unbalanced markdown → `400 can't parse entities` → reply silently dropped.
- **M11 — Required-field `payload["key"]` → `KeyError` → 500** across many mutating endpoints (`server.py:287,307,344,387,438,448,591`) instead of 400.
- **M12 — IoT webhook open when `IOT_WEBHOOK_SECRET` empty** (`agent/iot.py verify_signature` returns True). Off by default, but a footgun on enable.
- **M13 — Device tokens are not privilege-reduced** (`server.py:902-936`): a leaked device token can hit `/api/chat` (→ RCE), `/api/selfmod/prompt`, approve staged actions, IoT toggles — everything except token management.

---

## LOW / hardening

- `skill_forge` `INSERT OR REPLACE` on UNIQUE name resets an approved skill to pending while the live overlay keeps running (`skill_forge.py:219`).
- `search_turns` passes raw user `query` into `MATCH` unescaped and leaks `str(e)` (`longterm.py:374,389`).
- Access tokens never expire and `verify` has no lockout (`access_tokens.py:47-73`) — low impact given 256-bit entropy + hashing.
- `ratelimit.py:27,31` off-by-one → 11 failures allowed, not 10.
- `/api/models` discloses which provider keys are configured (`server.py:244`).
- `/api/push/test` + IoT/camera/guardian toggles have no master gate (`server.py:735-826,862`) — nuisance/spam.
- IMAP `uid` path segment unvalidated into `M.fetch` (`server.py:991`, `email_box.py:137`) — constrain to `^\d+$`.
- `curator._llm_dedup` calls the model directly, bypassing telemetry (`curator.py:98`).

---

## Test coverage gaps (ranked by risk)

| Module | Status |
|---|---|
| `agent/core.py` (main loop / `_execute_tool`) | **No real test** — only a monkeypatched stub |
| `agent/approvals.py` (write-approval gate) | **No test** |
| `dashboard/server.py` `_auth` middleware | **No test** (only ratelimit + token primitive covered separately) |
| `agent/router.py` (model routing) | **No test** |
| `agent/provider.py`, `agent/council.py`, `agent/longterm.py` | Thin/indirect only, no dedicated file |

Well-covered: `safety`, `resilience`, `access_tokens`, `cortex` (approval regression), `constellation`, `compare`, `sandbox`, `scheduler` (missed-fire).

---

## Prioritized fix list (execute top-to-bottom)

**Tier 1 — close the RCE surface (do before any untrusted exposure):**
1. Validate `name` (`.isidentifier()`, reject `.`/`/`/`..`) in `run_skill`, `_load`, `skill_md.manage` — kills C4, H3 path traversal. *(low risk, testable)*
2. Add `safety._RULES` entries for `run_python` + `run_skill`, and make cortex honor `safety.check` — closes H1. *(low risk)*
3. Stop trusting `is_read_only` from the model; require human approval for every forged tool; register only via `core._execute_tool` — closes C3.
4. Route skill `run()`/install execution through the sandbox, or default `SANDBOX_REQUIRE=true` — closes C1/C2. *(needs your decision: Docker dependency)*
5. Add per-service webhook auth (Twilio `RequestValidator`, Telegram secret-token header, Signal HMAC); remove the empty-allowlist "allow-all" fallback — closes C5. *(needs secrets config)*
6. Make `/api/pair/info` + `/api/pair/qr` mint a **device** token, never `DASHBOARD_TOKEN`; add `_require_master` — closes C6.

**Tier 2 — correctness & data integrity:**
7. `hmac.compare_digest` for token checks (`server.py:177,1629`); throttle the WS auth path — H8. *(trivial)*
8. Escape WhatsApp TwiML via `phone._escape_xml` — H6. *(trivial)*
9. `tarfile.extractall(..., filter="data")` + prefix check in curator rollback — H2. *(trivial)*
10. Offload Slack/Twilio/WhatsApp to `run_in_executor`, ack immediately, dedupe on provider event id — H4/H5.
11. Atomic `UPDATE … WHERE id=? AND status='pending'` in `approvals.approve` — M1.
12. `busy_timeout` + WAL in `longterm._conn`; run injection filter on `replace` too — M3/M4.
13. `LIMIT` + content-length caps in `recall`/`remember` — M2.

**Tier 3 — cost, reliability, robustness:**
14. Route all providers through `telemetry`; add GPT/Gemini/Ollama pricing — M8.
15. Explicit timeouts on OpenAI/Gemini/Ollama/IMAP — M9.
16. `X-Forwarded-For` (trusted) for throttle key — M7.
17. Pydantic request models → 400 not 500; constrain `uid`/`endpoint` — M11/L.
18. Retry Telegram send without `parse_mode` on parse error — M10.

**Tier 4 — test the untested high-risk modules:** `core._execute_tool` dispatch, `approvals` staging/apply, `server._auth` middleware (path allowlist, webhook bypass, master-vs-device), `router.route_model`.
