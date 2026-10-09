# Chronicle incident recording and replay

Status: implemented adapter and first execution path; opt-in runtime recording.
Source: https://github.com/theagentplane/chronicle
Dependency: `agent-chronicle==0.5.0` (MIT).
Reviewed upstream revision: `b9c0630d3df4e63f8b5041778d582140576cad1f`.
Apex baseline: `103567f6fffc93022ec8f43c326575962bcd9a59`.

## Install and activate

Install the optional dependency into the Python environment that runs Apex:

```sh
python -m pip install -r requirements-research.txt
```

Add to Apex's `.env` and restart:

```ini
INCIDENT_RECORDING_ENABLED=true
INCIDENT_RECORDING_DIR=~/.apex/incidents
```

`CHRONICLE_ENABLED=false` also disables recording. Replay remains available.
Without the dependency or with recording disabled, ordinary execution continues.
The adapter performs no optional import on the disabled path.

## What is connected

- `AgentCore.run`: one context-scoped trace per turn, inside its channel lock.
- `router.route_model`: model-routing decisions, including disabled routing.
- `safety.check`: permission decisions, including refusals.
- `core._execute_tool`: full dispatch input/return, with safety calls nested.

Existing telemetry, observed outcomes, and trajectory learning remain in place.
Tool dispatch includes memory tools, so memory effects are stubbed when replaying
that dispatch. Direct memory API calls are not independently instrumented.

Each turn writes a separate local JSONL file. Concurrent threads use separate
ContextVars and files; nested turn scopes reuse the enclosing trace. Context is
restored on normal exit and exceptions. Failure to initialize/persist/export
recording does not retry the action or replace its original error.

## Export a controlled incident

For standalone tool or routing calls outside `AgentCore.run`, open a scope:

```python
from agent import incident_replay, router

with incident_replay.record_turn(
    "local-test", store=".chronicle/run.jsonl", export="fixtures/traces/router-case"
):
    router.route_model("hello", "claude-sonnet-5")
```

For an existing runtime run, export with upstream's store/graph APIs:

```python
from chronicle import JsonlStore, ExecutionGraph

envelopes = JsonlStore("/absolute/path/to/run.jsonl").read_all()
ExecutionGraph.from_envelopes(envelopes[0].trace_id, envelopes).save(
    "fixtures/traces/incident"
)
```

Review fixtures for private content before committing. Redaction masks known
vendor secret patterns, configured secret values, sensitive structured fields,
and common textual assignments. It does not anonymize personal information,
guarantee detection of every secret encoding, or make trace files public-safe.
Raw runtime traces stay local; do not commit them. No automatic retention/deletion
policy is implemented yet: use a private directory and remove runs when done.

## Replay safely

```python
from agent import incident_replay, router

with incident_replay.replay("fixtures/traces/router-case"):
    recorded = router.route_model("hello", "claude-sonnet-5")

with incident_replay.replay("fixtures/traces/router-case", live_router=True):
    current = router.route_model("hello", "claude-sonnet-5")
```

Default replay returns recorded boundary outputs without executing their bodies.
The sole live cut-point allowed by the adapter is the pure model router. Tool
dispatch, safety checks (which may invoke a model or confirmation), and all other
boundaries are stub-only. Missing boundaries fail rather than run live. Recorded
exceptions raise `RecordedBoundaryError` with the sanitized incident message.
Live callers receive original values; replay receives sanitized fixture values.

**`AgentCore.run()` is refused in adapter replay.** Orchestration has direct side effects
outside these boundaries: conversation writes, subscription execution, telemetry,
and live model calls. This release is a boundary replay test bench, not end-to-end
agent replay. SDK LLM recording, streaming, direct memory boundaries, and skill
promotion gates are subsequent work, not completed capabilities.

## Verification

```sh
python -m pytest tests/test_incident_replay.py tests/test_safety.py tests/test_subagent_scope.py
```

Tests use real Chronicle storage and replay, including twenty repeated routing
replays, live router cut-points, no repeated tool effects/confirmation, nested and
concurrent scopes, secret removal, missing fixtures, recorded errors, and storage
or capture failures without duplicate execution. CI installs the optional package.

The adapter pins upstream 0.5.0 because session restoration and replay safeguards
touch its internal ContextVar/cursor. Revalidate these tests before upgrading.

## Research backlog after this integration

1. LLM call recording and serializable SDK response reconstruction; streaming
   and subscription paths need separate designs.
2. Independent memory admission/provenance boundaries and safe pure policy cuts.
3. Frozen evaluation contracts and versioned skill promotion/rollback.
4. When Debate Helps metrics and Council trace adapters.
5. Engram memory-selection benchmark, then measured retrieval integration.
6. Hermes and SkillAA component/license assessment; availability checks for
   ReCAP and the remaining watchlist. These are not yet integrated.
