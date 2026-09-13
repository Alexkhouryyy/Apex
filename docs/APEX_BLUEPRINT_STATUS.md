# Apex — Blueprint Status

Status of the 14-phase roadmap in `APEX_FINAL_Master_Blueprint_V2.docx`
(Table 7), checked against the code on 2026-09-12.

**Basis:** 2099 tests, 14 smoke checks, 3 static audits, CI on every push.

## How a phase is judged

By the blueprint's own **success check**, not by whether code exists. Those are
different questions, and this project has repeatedly shipped the first while
believing it had the second. A phase with a module, a wiring line and a passing
test can still fail its success check — Phase 2 and Phase 5 below are exactly
that, and they are the two worth acting on.

Where a phase is short of its check, the row says what specifically is missing
rather than a percentage.

| # | Phase | Success check | Status |
|---|---|---|---|
| 0 | Backup + baseline | Apex can be restored and regressions detected | **MET** — 1679 tests, 14 smoke checks, `tools/wiring_audit.py`, `tools/sql_audit.py`, `tools/autonomy_audit.py`, CI on every push. Decision records in `docs/DECISIONS-*.md` |
| 1 | Fix persistence | Restart and retrieve prior validated memories | **MET — and the premise was wrong.** The blueprint's §20 states memory resets between sessions. It does not, and did not: `agent/longterm.py` has always been durable SQLite. Verified by restart, not by reading the code |
| 2 | Markdown vault | Retrieve the correct project page *without loading the full vault* | **MET 2026-09-03.** `agent/vault_index.py` embeds every note into a `vault_index` table and `apex_note` grew a `search` action over it. Both halves of the check are held by tests: it finds a note whose title you do not know, and `TestSearchNeverOpensANote` makes `Path.read_text` raise during a query, so a search that fell back to scanning the vault fails loudly. Indexing reads notes; querying does not. Freshness is a content hash, not mtime+size; deleted notes are swept; a never-indexed vault says so rather than returning an empty list. **Degraded here, not broken:** `sentence-transformers` is not installed in the build container, so this machine only exercises the keyword fallback — which is why every result carries `matched: semantic` or `matched: keyword` instead of leaving the mode to be inferred from answer quality |
| 3 | Outcome loop | A corrected task changes future behaviour traceably | **MET, and past it.** `outcomes.py`, `feedback.py`, `reflection.py`, `lessons.py`, plus `observed.py` — outcomes Apex *sees* at the call site (tool results, test verdicts), not only ones it reports about itself |
| 4 | Adaptive Council | Council invoked only when it improves expected outcome | **MET.** `council.py` runs it, `consensus.py` measures divergence across *opening* answers (deliberately separate from the chair's self-reported confidence), `council_stats.advise()` answers "is it worth convening" from recorded runs rather than from belief |
| 5 | MCP foundation | Discover and use a tool **end-to-end safely** | **MET 2026-09-02.** `agent/mcp_policy.py` classifies every MCP tool read or write, gates it against `MCP_POLICY` / `MCP_ALLOW` / `MCP_DENY`, and records every decision — refusals included — in `mcp_audit`, with argument key names and a hash rather than values. The gate sits inside `mcp_client.call()`, the single door, and `tests/test_mcp_client.py::TestTheGateIsAtTheChokePoint` asserts a refused write never reaches the transport rather than merely that a refusal string came back. A server's own annotation may tighten the classification and never loosen it. **One thing still unproven:** the transport is stubbed in tests, so no real server's annotations have been parsed in anger — handled defensively (both the snake_case and camelCase spellings) but not yet observed |
| 6 | Cloud Core | Laptop offline, phone still reaches Apex memory | **BUILT, NOT DEPLOYED.** `agent/relay.py` seals every artefact before it leaves the laptop (`seal()` raises rather than falling back to plaintext) and snapshots through SQLite's online backup API so a WAL-mode database is copied consistently. `relay/server.py` is a stdlib-only mailbox that imports nothing from Apex, binds `127.0.0.1`, and serves nothing at all when no token is configured. `agent/working_context.py` sends a redacted, allowlisted page rather than the archive. `relay/answer.py` lets the cloud answer without the mailbox ever holding a model key. **What is not done is step 9:** no always-on box is running this, so the success check — lid shut, phone still answers — has never been observed. Proven on localhost, not in the world |
| 7 | Local node | Cloud Core delegates a local task | **BUILT, GATED ON 6.** `agent/capabilities.py` probes rather than declares — three states, `yes`/`no`/`unknown`, and the camera probe never opens the device. `agent/node_tasks.py` holds the queue: claims are leases with an expiry, an expired lease requeues and counts an attempt, `max_attempts` ends in `dead` with the last error kept, and a task for an offline node reads as waiting rather than as done. `agent/node_worker.py` routes delegated work through `core._execute_tool` — the same door a local call uses — so `safety.check`, `mcp_policy.enforce` and `subagent_scope.check` all run on the machine that would execute it. The queue carries a request, never a permission. Same gate as Phase 6: two processes on one host is not two machines |
| 8 | Spatial MVP | Pinch, move, rotate, scale work **reliably** | **BUILT, RELIABILITY UNPROVEN.** All four transforms exist with dwell-before-grab, open-palm cancel with revert, and undo/redo (`agent/board.py`). `HANDTRACK_PINCH_RATIO` is now measured rather than guessed. Nobody has yet pinched a card and had it grab |
| 9 | Spatial + Apex | Create, manipulate, close, reopen, continue | **MOSTLY.** Voice-to-scene via `board_create`/`board_recolor` into Blender; versioned assets with provenance (`agent/assets.py`); the board survives a restart, proven by test. The *manipulate* leg inherits Phase 8's unproven reliability |
| 10 | Mobile/Web continuity | Start on one interface, continue on another | **MOSTLY.** PWA (`manifest.webmanifest`, `sw.js`, `mobile.css`, `voice-mobile.js`), device registry, one shared SQLite brain. Worth being precise: handoff works because there is one database, not because handoff was designed. No explicit task-handoff affordance exists |
| 11 | Apex Drive / CarPlay | Driving-safe recall through an approved interface | **NOT STARTED.** Gated on Apple requirements, not on us |
| 12 | Forge | A designed object reaches a **validated manufacturable** representation | **MET 2026-09-12.** `agent/forge.py` writes STL and 3MF — 3MF because it is the only format in the chain that states its own unit, which is the failure the phase is really about — and refuses to write either until the mesh passes. The checks are watertightness (every edge in exactly two faces, traversed in opposite directions, so a closed mesh wound inside out is caught too), enclosed volume and its sign, wall thickness measured by casting rays inward from each face against the nozzle, overhang angle from vertical excluding the face the part rests on, build volume, and scale. `primitive()` builds every shape `blender_bridge` knows, in millimetres, with no Blender — so the whole chain is exercised by tests rather than asserted — and `read_glb()` ingests what `board_create` actually produces, converting glTF's metres and Y-up axes and saying on the report which conversions it assumed. **The third state is the point:** a check that cannot run reports `unknown`, a report with any `unknown` is `unverified`, and `unverified` does not export. 130 tests; every guard confirmed by reverting it individually, and the readers cross-checked against files written by an unrelated library |
| 13 | Concept Genesis | Testable novel hypotheses with evidence and critique | **MET 2026-09-13.** `agent/genesis.py` is a gate, not a generator — the proportion is the point, since asking a model for novel testable hypotheses always succeeds and tells you nothing. Four checks that never consult the model's opinion of itself: **structure** (a hedged claim is compatible with every observation; a refutation that restates falsity or negates the claim names nothing to go and look at), **evidence** (every citation must resolve — a fabricated one reads exactly like a real one — and somebody must have looked for disconfirming evidence), **novelty** against the source passages, memories, vault and prior claims, and **critique** by a model that is not the proposer. A `fatal` objection kills it. **The asymmetry is the design:** word overlap proves a duplicate and its absence proves nothing, so without embeddings the novelty check may return `not novel` and may never return `novel` — it returns `unknown`, and any `unknown` makes the hypothesis `unverified` rather than `standing`. `observe()` closes the loop on Popper's rules: a supported claim can be refuted later by one contrary observation, a refuted one is never restored by a confirming one, and `refuted` is reported as the system working. 129 tests, every guard confirmed by reverting it individually |

## The finding worth acting on: Phase 5

Every outward-facing capability in Apex is deny-by-default with an explicit
allowlist. Channels are (`dfc5590`). IoT entities are. Subagent roles are
(`agent/subagent_scope.py`). Dangerous shell commands go through
`agent/safety.py`. Gesture actions ship with three verbs mapped and the rest
inert.

MCP is the exception, and it is the worst one to have made:

- It loads **third-party servers from a config file Apex does not own**
  (`~/.claude/settings.json`), so the tool surface can change without a single
  line of Apex changing.
- Those tools reach real accounts — mail, calendar, documents, deploys.
- There is no read-versus-write distinction, so "check my calendar" and "send
  this email" pass through the same ungated call.
- Nothing is recorded. If an MCP tool did something surprising, there is no
  audit trail to read afterwards.

This is not a hypothetical. It is the one place where an allow-everything path
exists in a codebase whose entire doctrine is the opposite, and the blueprint
puts Phase 5 *before* Phases 6 and 7 precisely because the cloud and the local
node inherit whatever permission model exists when they are built. Building
those on top of no permission model bakes it in.

## Recommended order

1. ~~**Phase 5's safety half**~~ — done 2026-09-02. See the row above.
2. ~~**Phase 2's retrieval half**~~ — done 2026-09-03. See the row above.
3. ~~**Phases 6 and 7**~~ — built 2026-09-05..09. Steps 0–8 of
   `docs/PHASE_6_7_PLAN.md`. Step 9 is deployment and is not mine to do.
4. ~~**Phase 12 (Forge)**~~ — done 2026-09-12. See the row above. It was
   chosen because its success check could be demonstrated here, with no
   camera, no second box and no API key; everything else outstanding needs
   hardware this machine does not have.
5. ~~**Phase 13 (Concept Genesis)**~~ — done 2026-09-13. See the row above.

Every phase that can be built without hardware is now built. What remains is
Phase 11, which is gated on Apple's requirements rather than on us, and the two
gates below, which are gated on you.

### The two things blocking an honest label, both yours

Neither is work. Both are minutes.

| Gate | What it settles | What it takes |
|---|---|---|
| Pinch one card on `/board` | Phase 8 **and** Phase 9's manipulate leg, which inherits it | One camera, one pinch, one sentence back: did it grab |
| Run the relay on any always-on box | Phase 6 from BUILT to MET, and Phase 7 with it | `relay/README.md`, step 9 |

I cannot move either. Phase 8's check is a human hand in front of a real
camera, and Phase 6's is a machine that stays awake when your laptop does not.
No test I write here substitutes for either, and a test that claimed to would
be the exact failure this document exists to catch.

## A note on Phase 1, added after the fact

Phase 1 is met, but the reason it looked met was partly luck. `main.py` and
`app/resident.py` each carried their own hand-maintained list of `init_db()`
calls, and they had drifted by twelve modules — all twelve initialised in
interactive mode, none of them in the always-on daemon. Any machine that had
ever run interactive mode had the tables on disk already, so the daemon
inherited them and looked fine; a resident-only install had no `outcomes`,
`lessons`, `restraint`, `board_cards` or `scheduled_tasks` at all, and several
of those modules print-and-continue on a write failure by design.

Both entry points now initialise from one list (`agent/schema.py`), and
`tests/test_schema.py` asserts every module defining `init_db` is in it —
because twelve added lines would have fixed the date and drifted again.

Phase 11 is gated on Apple's requirements rather than on us.

## What Phase 12 deliberately does not do

Worth writing down, because "validated manufacturable" is a phrase that can be
stretched a long way:

* **No STEP, and no solid model.** Everything here is a triangle mesh. STEP is
  a boundary representation with real curves and a schema to match, and a
  half-implemented STEP writer that emits files a CAD package opens wrong would
  be worse than not having one.
* **No slicing, and no time or material estimate.** The volume is exact; what a
  machine does with it is the slicer's business.
* **Thickness is measured perpendicular to each face, by sampling.** That can
  over-report the true minimum, never under-report it, so a `fail` is
  trustworthy and an `ok` means "nothing thin was found". The report says
  `thinnest measured` for that reason.
* **Subtractive and injection processes are not modelled.** No draft angles, no
  tool access, no undercuts. The checks describe a fused-filament machine,
  which is the machine the defaults describe.
