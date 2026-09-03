# Phases 6 and 7 — Cloud Core and the Local Node

A plan for the last two structural phases of `APEX_FINAL_Master_Blueprint_V2.docx`.
Written 2026-09-03 against the code, not against the document.

| Phase | Blueprint's success check |
|---|---|
| 6 | Laptop can go offline while phone/web still accesses Apex memory |
| 7 | Cloud Core can delegate a local task when laptop is online |

---

## 1. The decision that has to be made before any code

The blueprint is unambiguous. §3.1 puts "persistent user/project memory",
"authentication, device registration", and "conversation/task state" **in the
cloud**. Non-negotiable rule 6 says: *"Local nodes expose capabilities; they do
not own Apex identity."* Under that reading, the laptop stops being Apex and
becomes a limb.

Apex's own README says, in the first sentence:

> **A self-hosted, voice-first, always-on personal AI agent.** Runs on your own
> hardware.

Those two statements cannot both survive Phase 6 as written. Moving identity and
memory to a hosted service makes Apex a product that keeps your second brain on
someone else's computer, which is the specific thing the project has been
positioned against — and it is not a detail that can be settled later, because
every line of Phase 6 code follows from it.

The blueprint itself leaves the door open. §3.3's mental model calls the cloud
core "brain stem and long-term continuity", rule 5 insists it "stays thin enough
to be affordable and reliable", and §1 explicitly lists **"Raspberry Pi or other
hardware"** among the things that connect to the Core. Nothing in the document
requires the always-on machine to be rented rather than owned.

So the question is not *cloud or not*. It is: **what is the smallest always-on
thing that satisfies "laptop closed, phone still works", and who owns it?**

---

## 2. What is already true, measured rather than assumed

Four facts change the shape of this work, and three of them make it much smaller
than the phase titles suggest.

**Most of Phase 6's success check is already met.** The laptop is on Tailscale
with a permanent HTTPS name (`alexslaptop.tail1d8b3d.ts.net`), so the phone
already reaches Apex's memory from anywhere in the world, authenticated by
per-device revocable tokens (`agent/access_tokens.py`). The unmet part of that
sentence is exactly three words long: **while the laptop is offline**. Phase 6 is
not "build a cloud platform". It is "decide what happens when the lid is shut".

**The brain is 565 KB and 1,216 rows across 59 tables.** This is not a data
migration problem and any plan that treats it as one is solving the wrong
problem. The entire memory of Apex fits comfortably in a single HTTP response.

**There is one storage choke point — and one hole in it.** 42 modules go through
`longterm._conn()`, which makes changing where data lives tractable. But
`agent/iot.py:36` opens its own `sqlite3.connect(_DB_PATH)` and bypasses it
entirely. Any storage change would silently miss IoT state — the subsystem would
keep working locally and quietly diverge. That is a prerequisite fix, not a
Phase 6 task.

**Phase 7's parts half-exist.** `agent/devices.py` is an 88-line heartbeat
registry — it knows *what* is connected and nothing about *what each can do*.
The dashboard has a WebSocket, but it broadcasts server→client only. There is no
task queue, no acknowledgement, no lease, no retry.

---

## 3. Three ways to satisfy Phase 6

### Option A — Hosted Cloud Core (the blueprint read literally)

A managed service (Fly.io / Railway / a VPS) owns Postgres, identity, routing
and orchestration. The laptop registers as a node.

- Contradicts the self-hosted claim outright.
- 59 tables of SQLite-specific SQL to port, and `tools/sql_audit.py` currently
  validates queries against SQLite's schema introspection.
- A second deployment to keep alive, with the API keys and the memory in it.
- Real recurring cost.
- **Weeks of work, and Apex becomes a different product.**

### Option B — Thin relay, laptop stays authoritative *(recommended)*

A small always-on process that does exactly two things:

1. **Holds an outbox** — messages and task intents that arrive while the laptop
   is off, drained in order when it returns.
2. **Serves the last snapshot** — an encrypted digest of memory the phone can
   read when the laptop cannot answer.

The laptop remains the single writer and the identity. The relay never reasons,
never calls a model, and cannot read what it stores.

- Satisfies the success check: phone reads memory and leaves work while the
  laptop sleeps.
- Preserves "one brain, one writer" — the hardest property to get back once lost.
- Preserves self-hosted: the relay holds ciphertext and a queue, not authority.
- Small enough to run on a $5 VPS **or** a Raspberry Pi, which makes the
  own-versus-rent choice a deployment flag rather than an architecture.

### Option C — Always-on box you own

Option B's process, running on a Pi or an old machine on your Tailnet. Same
code. No third party, no monthly cost, no ciphertext leaving the house — at the
cost of being dark when your home internet is.

**Recommendation: build B, deploy as C where possible.** They are the same
program; the difference is one hostname. Designing for that from the start costs
nothing and keeps the ownership question reversible.

---

## 4. Phase 6 design

### What the relay stores

Two tables, and deliberately no more:

| Table | Contents |
|---|---|
| `outbox` | `id, device_id, created_at, kind, payload_ciphertext, claimed_at, claimed_by, done_at` |
| `snapshot` | `updated_at, ciphertext, byte_len` — one row, overwritten |

The relay never holds plaintext. `payload_ciphertext` and `snapshot.ciphertext`
are sealed with a key that exists only on the laptop and the phone. The relay
operator — including you, on a bad day — cannot read a message by looking in the
database.

That constraint is what keeps this honest, and it has a cost worth stating
plainly: **the relay cannot search your memory**, because it cannot read it. The
phone downloads the snapshot and searches locally. At 565 KB that is a
non-problem today; if the brain ever reaches tens of megabytes, this design has
to be revisited rather than quietly strained.

### The write rule

**Writes never originate in the cloud.** A memory added from your phone while
the laptop is off becomes an *intent* in the outbox, applied by the laptop when
it wakes. One writer, no conflict resolution, no divergence.

The trade, stated rather than discovered: **a note you add from your phone is
not searchable until the laptop is next awake.** The phone shows it as pending
so it never looks lost. The alternative — letting the cloud write — buys
immediacy and costs the single-writer property, which is the one thing that
makes "one Apex identity" true rather than aspirational.

### Snapshot cadence

`scripts/backup_brain.py` already makes a consistent copy using SQLite's online
backup API (correct about WAL, which a plain file copy is not). Phase 6 reuses
it: snapshot on a schedule and on clean shutdown, encrypt, push, record the
byte length and time.

### Config

```
RELAY_ENABLED=false            # off until deliberately configured
RELAY_URL=                     # https://apex-relay.example.com
RELAY_TOKEN=                   # authenticates this laptop to the relay
RELAY_KEY=                     # symmetric key; NEVER sent to the relay
RELAY_SNAPSHOT_MINUTES=30
```

`RELAY_KEY` joins `VAPID_PRIVATE_KEY` in the set of values that must never reach
a repository, and — unlike it — must never reach the relay either.

---

## 5. Phase 7 design

### Capabilities are probed, never declared

`devices.py` gains a capability set: `shell`, `camera`, `blender`, `gpu`,
`local_model`, `printer`. A node reports what it can do **by testing**, the same
way `handtrack.available()` and `blender_bridge` already check rather than
assume.

This is the MCP annotation lesson applied one layer out: a node saying "I can
run Blender" is a claim by the party the claim is about. A dispatcher that
believes it queues work that can never run, and the task sits "in progress"
forever. Capability rows carry `verified_at`, and a stale verification is
reported as stale rather than treated as absent or present.

### The task queue, and the four ways it fails quietly

| Failure | What it looks like | The rule |
|---|---|---|
| Task dispatched to an offline node | Accepted, never runs, no error | A task for an offline node is `queued` with the node named, and the caller is told "waiting for the laptop" — never "done" |
| Node claims a task then dies | `in_progress` forever | Claims are **leases with an expiry**. An expired lease returns the task to the queue and increments an attempt counter |
| A task that fails every time | Infinite retry loop | `max_attempts`, then `dead` with the last error kept |
| Delegation used as a permission bypass | Cloud "pre-approves" what the laptop would refuse | **Delegated work goes through the laptop's own gates.** `safety.check`, `mcp_policy.enforce` and `subagent_scope.check` all run on the node, at execution time. The queue carries a request, never a permission |

That last row is the load-bearing one and the reason Phase 5 had to come first.
A delegation path that skips the local gates would undo the permission model in
one line — and it would look like a feature.

### Transport

The laptop **polls** the relay over HTTPS rather than holding a WebSocket open.
Less elegant, and correct for this shape: a laptop that sleeps, changes network,
and wakes on a different Wi-Fi breaks long-lived sockets constantly, and every
reconnect path is a place for a silent stall. Polling every few seconds is
stateless, survives suspend, and fails visibly. If latency ever justifies it, a
socket can be added as an optimisation over a polling fallback that still works.

---

## 6. What can be proven here, and what cannot

| Half | Who proves it | How |
|---|---|---|
| Queue, leases, expiry, retry, capability probing, encryption round-trip | Me, here | Pure functions and a real relay process on localhost |
| Two real processes, one authoritative | Me, here | The pattern `tests/test_board.py` already uses: one interpreter writes, a separate one reads back |
| Laptop actually offline, phone actually elsewhere, relay actually deployed | **You** | Shut the lid, use the phone, open it again |

The last row cannot be faked in a container, and Phase 6 stays PARTIAL until it
is done — the same boundary hand tracking is held to.

---

## 7. Prerequisite

`agent/iot.py:36` opens its own SQLite connection instead of using
`longterm._conn()`. Any snapshot or storage change would silently miss it. One
small commit, before either phase.

---

## 8. Sequence

| Step | Work | Success check |
|---|---|---|
| 0 | Route `agent/iot.py` through `longterm._conn()` | The SQL audit sees one connection path |
| 1 | `agent/relay.py` — encrypt, snapshot, push, pull. No server yet | Round-trips a snapshot through a fake transport; ciphertext is unreadable without the key |
| 2 | `relay/server.py` — the two tables, token auth, no plaintext | Boots on localhost; the test suite drives a real one |
| 3 | Outbox drain on the laptop, pending state on the phone | A message queued while "offline" is applied exactly once when the laptop returns |
| 4 | Capability probing on `devices.py` | A node without Blender never reports `blender` |
| 5 | Task queue with leases, expiry, attempts, `dead` | An expired lease requeues; a task for an offline node reads as waiting, never as done |
| 6 | Delegated execution through the local gates | A delegated shell command still hits `safety.check` — reverting that check must fail a test |
| 7 | Deploy and use it | Lid shut, phone works, lid open, work lands |

Steps 0–6 are buildable and testable here. Step 7 is yours.

**Rough size:** steps 0–3 are comparable to the MCP permission work. Steps 4–6
are a similar size again. This is the largest remaining item in the blueprint,
and it is the first one that adds a second deployed process — which is the real
cost, more than the code.

---

## 9. What this plan deliberately does not do

- **Port the database to Postgres.** 565 KB and 1,216 rows do not need it, and
  it would trade a working storage layer for a migration.
- **Let the cloud reason.** A relay that called models would need the API keys,
  the memory in plaintext, and its own permission model — three of the four
  reasons not to do Option A.
- **Move identity off the laptop.** Rule 6 of the blueprint asks for this and
  this plan declines it, on the grounds stated in §1. If that trade is wanted
  anyway, it is Option A and should be chosen with its cost visible, not arrived
  at by increments.
