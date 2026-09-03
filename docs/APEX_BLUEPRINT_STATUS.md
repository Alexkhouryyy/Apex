# Apex — Blueprint Status

Status of the 14-phase roadmap in `APEX_FINAL_Master_Blueprint_V2.docx`
(Table 7), checked against the code on 2026-09-02.

**Basis:** 1763 tests, 14 smoke checks, 3 static audits, CI on every push.

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
| 2 | Markdown vault | Retrieve the correct project page *without loading the full vault* | **NOT MET.** `agent/vault.py` writes Obsidian-compatible Markdown with frontmatter and wikilinks, and reads back by title. There is no retrieval by content — `list_notes()` and `read_note(title)` both require you to already know which page you want, which is the thing the check says you should not need to know. The embedding index that would answer it covers `memories`; the vault is not in it |
| 3 | Outcome loop | A corrected task changes future behaviour traceably | **MET, and past it.** `outcomes.py`, `feedback.py`, `reflection.py`, `lessons.py`, plus `observed.py` — outcomes Apex *sees* at the call site (tool results, test verdicts), not only ones it reports about itself |
| 4 | Adaptive Council | Council invoked only when it improves expected outcome | **MET.** `council.py` runs it, `consensus.py` measures divergence across *opening* answers (deliberately separate from the chair's self-reported confidence), `council_stats.advise()` answers "is it worth convening" from recorded runs rather than from belief |
| 5 | MCP foundation | Discover and use a tool **end-to-end safely** | **MET 2026-09-02.** `agent/mcp_policy.py` classifies every MCP tool read or write, gates it against `MCP_POLICY` / `MCP_ALLOW` / `MCP_DENY`, and records every decision — refusals included — in `mcp_audit`, with argument key names and a hash rather than values. The gate sits inside `mcp_client.call()`, the single door, and `tests/test_mcp_client.py::TestTheGateIsAtTheChokePoint` asserts a refused write never reaches the transport rather than merely that a refusal string came back. A server's own annotation may tighten the classification and never loosen it. **One thing still unproven:** the transport is stubbed in tests, so no real server's annotations have been parsed in anger — handled defensively (both the snake_case and camelCase spellings) but not yet observed |
| 6 | Cloud Core | Laptop offline, phone still reaches Apex memory | **NOT STARTED** |
| 7 | Local node | Cloud Core delegates a local task | **NOT STARTED.** `agent/devices.py` is an 88-line heartbeat registry — it knows what is connected, it cannot delegate to it |
| 8 | Spatial MVP | Pinch, move, rotate, scale work **reliably** | **BUILT, RELIABILITY UNPROVEN.** All four transforms exist with dwell-before-grab, open-palm cancel with revert, and undo/redo (`agent/board.py`). `HANDTRACK_PINCH_RATIO` is now measured rather than guessed. Nobody has yet pinched a card and had it grab |
| 9 | Spatial + Apex | Create, manipulate, close, reopen, continue | **MOSTLY.** Voice-to-scene via `board_create`/`board_recolor` into Blender; versioned assets with provenance (`agent/assets.py`); the board survives a restart, proven by test. The *manipulate* leg inherits Phase 8's unproven reliability |
| 10 | Mobile/Web continuity | Start on one interface, continue on another | **MOSTLY.** PWA (`manifest.webmanifest`, `sw.js`, `mobile.css`, `voice-mobile.js`), device registry, one shared SQLite brain. Worth being precise: handoff works because there is one database, not because handoff was designed. No explicit task-handoff affordance exists |
| 11 | Apex Drive / CarPlay | Driving-safe recall through an approved interface | **NOT STARTED.** Gated on Apple requirements, not on us |
| 12 | Forge | A designed object reaches a **validated manufacturable** representation | **PARTIAL, and the gap is the adjective.** `agent/blender_bridge.py` does parameterized creation, colour and controlled export — to `.glb`, which is a visualisation format. Nothing exports STL/STEP/3MF, checks wall thickness, or validates printability |
| 13 | Concept Genesis | Testable novel hypotheses with evidence and critique | **NOT STARTED** |

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
2. **Phase 2's retrieval half** — put the vault in the embedding index so the
   success check can be met. Small, self-contained, and it makes the vault
   actually load-bearing instead of a write-only mirror.
3. **Phase 8's proof** — one person, one camera, one card that grabs. Cheap,
   and it unblocks the honest labelling of 8 and 9.
4. Then 6 and 7, which are the real architectural work.

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

Phases 11, 12 and 13 are further out and none of them blocks anything above.
