# Agent systems study and Apex adoption decisions

Research date: 29 September 2026. Apex baseline: `7fa703c60f15129f60b989714a5929cb0db40be6`.

## Decision

Apex should borrow reliable integration patterns and testable capabilities from these systems while retaining one runtime, one permission path, one memory authority and its spatial/voice interface. Importing entire applications would introduce competing agents, storage formats, settings, dependency trees and update mechanisms. A repository URL is a source of code or instructions; indexing it does not make its capabilities executable.

The highest-value findings are preserved input contracts for generated skills, truthful validation and installation status, replayable execution evidence, inspectable memory, bounded voice delivery, and compatibility checks before enabling plugins. Some already exist in Apex. The implementation accompanying this study fixes the skill-contract and approval gaps; the adoption register below explicitly distinguishes those changes from remaining work.

This is a source-led architecture and selected-path review of 18 repositories, not a line-by-line audit or a head-to-head runtime benchmark. Upstreams were pinned and inspected without running their installers or importing their code. No claim of hands-on reliability, latency superiority, or a numerical product ranking is justified by this study. Documentation claims and inspected implementation are distinguished below. The source index records exact revisions and acquired files; acquisition alone is not review.

## The improved assignment

> Study Hermes, OpenClaw, Odysseus, Agent Zero, Open WebUI, LibreChat, OpenHands and its runtime SDK, Letta Code, Dify, Jared Rhodenizer's Fullstack Agent/Barehands/Backtalk/AI Memory Vault/AI Visualizer, Ethan Rogers' Jarvis, OpenJarvis, and ModelScope Ultron. Resolve identities on GitHub, pin revisions and record licenses. Review architecture, actual execution paths, memory, skills, plugins, MCP, voice, spatial interaction, persistence, recovery, permissions, updates, onboarding, observability, evaluation and operating-system constraints. Separate documented behavior, inspected code and locally demonstrated behavior. Map every useful finding to Apex's existing implementation before adding anything. For each candidate, decide retain, improve, adapt, integrate externally, defer or reject, with evidence, dependencies, acceptance criteria and rollback. Implement confirmed improvements in coherent increments, preserving existing data and authorization boundaries. Test the affected workflows and distinguish automated checks from live hardware and account-dependent verification. Produce a source-backed report, adoption register and honest implementation status. Never equate a catalogue entry, configuration flag, compilation result or successful HTTP response with a working end-to-end capability.

## Sources and scope

Exact names matter. “Jared's Jarvis” resolves here to `jaredrhod/fullstack-agent` and its four components. “Ethan Pulsnai” resolves to `ethanplusai/jarvis`. OpenJarvis is `open-jarvis/OpenJarvis`. The user explicitly selected **ModelScope Ultron**, not similarly named Ultron repositories. Letta's reviewed current harness is `letta-ai/letta-code`; this is not an audit of all historical Letta server releases. The current OpenHands application calls itself Agent Canvas; the SDK is a separate repository.

See [source index](agent-systems-sources.json) for pinned SHAs, retrieval timestamps and file hashes. Each link below points to the reviewed snapshot. The review concentrates on the paths relevant to Apex; source files may have been inspected in targeted excerpts rather than in their entirety.

### Hermes: plugin lifecycle and coherent operations

Its plugin contract exposes tools, hooks, commands, platform adapters, selected providers and other runtime capabilities. Plugin metadata and actual tool schemas are distinct; its documentation explicitly identifies the schema description as the model-facing description. Memory has a separate provider discovery mechanism. This matters: an Apex `register(ctx)` adapter does not imply compatibility with every Hermes extension API. [Plugin contract](https://github.com/NousResearch/hermes-agent/blob/b9df1cccaec26a910e5f0d1d77f52a71641e3d98/website/docs/user-guide/features/plugins.md)

The inspected admission code submits the proposed enabled-plugin set to a package manager and reports dependency conflicts before publishing the change. It accepts an expected configuration value and keeps environment publication out of the caller. That is a useful stronger lifecycle than “download, import, hope.” [Admission implementation](https://github.com/NousResearch/hermes-agent/blob/b9df1cccaec26a910e5f0d1d77f52a71641e3d98/hermes_cli/plugins_admission.py)

Apex already has repository preview, pinned packages, trust/enable states, removal/rollback, configuration, hooks, commands and memory/context provider seams in `agent/plugins.py`. It deliberately rejects unsupported Hermes kinds, privileged capabilities and cross-plugin requirements. Retain those explicit incompatibility messages. Next improvements should be a tested compatibility matrix and an isolated dependency-install strategy, not an “install all Hermes plugins” button. The screenshot's menu alone is not the differentiator; making each lifecycle step truthful is.

### OpenClaw: one gateway and discoverable capabilities

Its gateway owns messaging connections; clients and paired nodes connect to the same control plane. The documented protocol distinguishes acceptance from completion, carries run IDs, uses typed messages and uses idempotency keys for side-effecting requests. Pairing is device-based. [Architecture](https://github.com/openclaw/openclaw/blob/ac59199ad3fd438f35315554b5628a6945ca400a/docs/concepts/architecture.md)

Plugin discovery reads manifests before runtime loading and applies enablement/path checks. The activation plan can explain why a plugin loaded. These are useful ideas for Apex's diagnostics and for keeping model context small. [Load pipeline](https://github.com/openclaw/openclaw/blob/ac59199ad3fd438f35315554b5628a6945ca400a/docs/plugins/architecture-internals/load-pipeline.md)

Apex's resident process, dashboard, devices, node tasks, capability probes and MCP policy already provide corresponding building blocks. Extend them with a joined capability view: installed, enabled, configured, authorized, reachable and recently tested are different states. Do not start a second OpenClaw gateway just to obtain its sidebar. A sandbox setting also needs verification of the selected backend; importing its vocabulary does not create isolation.

### Odysseus: integrated local workspace, with candid limits

The memory/skills specification distinguishes strict mutating reads from lenient display reads: corrupt storage must not be replaced with an empty list. It describes owner-scoped memory, progressive skill selection and a bounded URL importer. It also documents incomplete migration and owner-scope coverage. [Memory/skills specification](https://github.com/odysseus-dev/odysseus/blob/3b6c169162330cd35c4ce6d14949ef15fd3208a2/specs/memory-skills.md)

Its roadmap identifies installation tests, integration reliability, small-model context pressure, hardware fitting and better failure output as unresolved work. A large interface is not proof that these problems have been solved. [Roadmap](https://github.com/odysseus-dev/odysseus/blob/3b6c169162330cd35c4ce6d14949ef15fd3208a2/ROADMAP.md)

Useful Apex adaptations: show degraded retrieval explicitly; test unreadable stores on mutation; expose hardware prerequisites beside a model choice; cap injected skill/tool context. Keep Apex's existing document/research/model tools instead of introducing duplicate editors and databases. Odysseus is especially useful as a map of failure modes to avoid.

### Agent Zero: memory maintenance as a normal user workflow

The memory interface supports finding entries, examining source/metadata, editing and deleting. Its guide treats stale and incorrect memories as an operational problem rather than proof that the model needs more memory. [Memory workflow](https://github.com/agent0ai/agent-zero/blob/e3051fb584b1a36be2b0a0c90606f1c2c2d356ec/docs/guides/memory.md)

The plugin guide starts with a small manifest and a visible capability, with documented removal and requirements. [Plugin guide](https://github.com/agent0ai/agent-zero/blob/e3051fb584b1a36be2b0a0c90606f1c2c2d356ec/docs/developer/plugins.md)

Apex has long-term memory, memory files, vault indexing, lessons and working context. The useful gap is a clear explanation of which source influenced an answer and how to correct it across future turns. Do not add a second autonomous memory writer or blindly ingest every conversation. Establish project scope and a deletion/correction contract first.

### Open WebUI: extensible conversation and transport discipline

The inspected MCP client explicitly initializes sessions, pages tool discovery, preserves input schemas, propagates tool errors and manages cleanup. Its cleanup comments identify the MCP SDK's same-task ownership constraint. [MCP client](https://github.com/open-webui/open-webui/blob/8bd8b4fac5e059578ac0c74b3c18d11139f88b7d/backend/open_webui/utils/mcp/client.py)

The lesson for Apex is transport correctness and honest discovery, not a bigger integration count. Apex already has stdio/HTTP MCP clients, policy, auditing and an app catalogue. Add pagination, disconnect, authorization-expiry and duplicate-name cases wherever coverage is missing. Keep Python plugin execution distinct from remote MCP: a transport protocol is not a sandbox. Its current root license also has branding conditions; do not assume the old license applies to current frontend code.

### LibreChat: execution visibility without invented totals

The trace model separates simple/full views and sequence/time scales. Missing model pricing makes total cost unavailable rather than silently treating missing values as zero. Incomplete trace coverage is represented explicitly. The route uses authentication, conversation ownership and a read limiter. [Trace model](https://github.com/LibreChat-AI/LibreChat/blob/40bb16edfd4698b6239eaafd6714f6e57faf1bc6/client/src/components/Chat/Trace/model.ts), [route](https://github.com/LibreChat-AI/LibreChat/blob/40bb16edfd4698b6239eaafd6714f6e57faf1bc6/api/server/routes/traces.js)

Apex already records tool evidence, trajectories, token telemetry and task receipts. The improvement is a joined turn timeline with actual status, tool duration, missing-data markers and artifact links. Avoid exposing internal reasoning or duplicating sensitive tool payloads. Do not label an estimated subset “total cost.” An attractive dashboard with fixture data is not runtime proof.

### OpenHands / Agent Canvas: execution backends and durable events

The current app supports multiple agent backends and distinguishes running directly on the host from a Docker-backed setup. Those have materially different execution boundaries. [Application overview](https://github.com/OpenHands/OpenHands/blob/f174ba8465233e46e66ab2c5667b358f1d6676d6/README.md)

The SDK's event store uses locking, stable event identities, parent links and backward-compatible traversal. Its stuck detector handles repeated actions/observations, errors, monologues and alternating patterns, including a bounded nudge before abandoning repeated errors. [Event store](https://github.com/OpenHands/software-agent-sdk/blob/71612374a8d3c639909b03613dbecf1ff999d0bd/openhands-sdk/openhands/sdk/conversation/event_store.py), [stuck detection](https://github.com/OpenHands/software-agent-sdk/blob/71612374a8d3c639909b03613dbecf1ff999d0bd/openhands-sdk/openhands/sdk/conversation/stuck_detector.py)

Apex has `team`, `trajectory`, `verification` and `task_recovery`. Continue those abstractions. A coding-agent backend can be an optional adapter with explicit process ownership and workspace scope. Never interpret “resume” as replaying completed external writes. A repeated-error detector should compare results as well as inputs; repeated legitimate polling is different from an unproductive loop.

### Letta Code: continuous identity and explicit memory ownership

The current harness presents agent identity, memory, scoped skills, channels and scheduling as persistent state. MemFS uses Git-backed context, and native Windows automatic reflection has a documented default limitation. Cloud and local backends have distinct behavior. [Current overview](https://github.com/letta-ai/letta-code/blob/3f27924f7c59ff6fdb40a0cae178dddcb4b0460a/README.md)

The inspected conflict-repair path uses a token identifying the repair attempt so a delayed worker cannot update a newer attempt's record. Skill discovery tracks configured, project and shared-memory roots. [Repair ownership](https://github.com/letta-ai/letta-code/blob/3f27924f7c59ff6fdb40a0cae178dddcb4b0460a/src/agent/memory-conflict-repair.ts), [skill discovery](https://github.com/letta-ai/letta-code/blob/3f27924f7c59ff6fdb40a0cae178dddcb4b0460a/src/agent/client-skills.ts)

For Apex, preserve Celine's identity across interfaces, make memory edits versioned and expose conflicts instead of overwriting them. Keep memory scope separate from personality. A persona file does not establish subjective feelings, and more agent profiles do not automatically improve task success. Shared memory needs explicit ownership and correction propagation before synchronization.

### Dify: explicit workflows and application-level measurement

Dify's documented strengths include workflow composition, RAG and model/tool integrations. The selected runtime file separates tool resolution/invocation, model context, file access and human-input services. This is an application-building framework, not a small drop-in personal-assistant plugin. [Overview](https://github.com/langgenius/dify/blob/e6f1d77ed5869e659e426b5459439d44bdabdbe5/README.md), [runtime boundaries](https://github.com/langgenius/dify/blob/e6f1d77ed5869e659e426b5459439d44bdabdbe5/api/core/workflow/node_runtime.py)

Useful for Apex: visible typed inputs/outputs, reusable multi-step procedures, per-step errors and controlled retries. Use the existing scheduler/team/tool paths. Only build a visual workflow editor after stable workflow execution and receipts exist. Adopting Dify's entire service/deployment stack for a Windows desktop assistant would carry substantial operational cost. Its license includes additional conditions; treat it separately from plain Apache-2.0.

### Jared Rhodenizer's Fullstack Agent: the composition is the product

The setup document coordinates memory, voice, face and optional hands; it checks for existing installations and carries choices into the component setups. It is largely orchestration and instructions, not a new inference engine. [Composition guide](https://github.com/jaredrhod/fullstack-agent/blob/5bb159f47dbd6fa8f108651d0532a43aef16346b/fullstack-agent.md)

The transferable benefit is one onboarding flow and one identity across components. Apex's launcher already coordinates Celine and the resident dashboard. Add a single prerequisite/results view and persist choices once. Do not adopt upstream setup directives that install unrelated software, change home-folder instructions or replace existing memory. Reading a repository's installer instructions is not authorization to execute them.

### Barehands: a small bidirectional spatial contract

The inspected server receives scene heartbeats, returns queued commands and exposes scene state for the agent. It limits board verbs and constrains media and notes to configured roots. Scene state is held in memory. The stage tracks which hands hold an item and supports different transform modes. [Server](https://github.com/jaredrhod/barehands/blob/eb23bed2d772f9d5a24de26fb92f46c3c76d69cf/server.py), [stage](https://github.com/jaredrhod/barehands/blob/eb23bed2d772f9d5a24de26fb92f46c3c76d69cf/stage.html)

For Apex, the valuable lesson is that the agent must read the same selected object, held state and transform that the user sees. Apex already has board readout, workspace persistence, per-hand intent and selection metadata. Validate these across tracking loss, two-hand transitions, refresh and object replacement. Do not transplant the entire stage or assume its gestures solve Apex's false pinch problem. A generic mesh cannot reliably reveal semantic “engine bell” boundaries without useful mesh topology or authored metadata.

### Backtalk: separate voice state from permission state

Backtalk publishes a small voice signal contract: listening/thinking/speaking state, waveform and optional usage information. Its push-to-talk and output components are separable. [Signal contract](https://github.com/jaredrhod/backtalk/blob/84b3a6cd321060cabb74aad6ebe794621cf99bd3/backtalk/signals.py), [push-to-talk](https://github.com/jaredrhod/backtalk/blob/84b3a6cd321060cabb74aad6ebe794621cf99bd3/backtalk/ptt.py)

Apex should keep microphone availability, active listening, synthesized audio availability, current playback and pending action approval as distinct states. A card being held must not change the microphone explanation. Push-to-talk should remain a reliable fallback, and stopping speech should invalidate already queued audio. Retain Celine's existing Qwen engine; swapping voice engines would not fix those interaction contracts.

### AI Memory Vault: one editable source, not competing copies

Its starter memory file redirects the agent to one vault, with a boot/config index and contextual files. The key lesson is single ownership and understandable storage. [Memory pointer](https://github.com/jaredrhod/ai-memory-vault/blob/659bba9c8b351c937dd393b3042801d1ff1b502c/templates/MEMORY.md), [vault index](https://github.com/jaredrhod/ai-memory-vault/blob/659bba9c8b351c937dd393b3042801d1ff1b502c/templates/VAULT-INDEX.md)

Apex already has a vault and database-backed memory. Define which store owns facts, documents, persona and task state; treat search indexes as derived data. A Markdown export/import view can help inspection, but a second independently edited memory store would reintroduce drift. Obsidian is a user-interface choice, not a requirement for persistent memory in Apex.

### AI Visualizer: replaceable presentation over a shared state contract

The visualizer polls the voice bus through a shared core, while faces supply presentation. The selected polling path holds the last state when the server becomes unavailable. [Shared core](https://github.com/jaredrhod/ai-visualizer/blob/6921e1d4b06bdd4a34c5264882d5257c4d5f70fd/core.js)

Borrow the small presentation interface, theme colors, waveform response and reduced-motion support. Improve disconnection handling with a visible stale/offline state rather than a face that appears to keep thinking indefinitely. Do not add artificial emotional claims or a second source of runtime truth. This is an interface improvement, not an intelligence upgrade.

### Ethan Rogers' Jarvis: voice plus operational awareness

The current Jarvis is a Claude Code voice/control layer with run/session/spec/project/usage views. The README labels demonstration dashboard data as fictional, and its platform integration is strongly macOS-oriented. [Overview](https://github.com/ethanplusai/jarvis/blob/16e37bd801eb797798e02540665f8b3804b0fe14/README.md)

Its preflight checks return a status, explanation and remedy. Run state is persisted before announcements; the store records a process run separately from ordinary conversations. Speech has an explicit scheduler and sentence splitting. Tests exercise a stalled browser listener so one slow socket does not silence other listeners. [Preflight](https://github.com/ethanplusai/jarvis/blob/16e37bd801eb797798e02540665f8b3804b0fe14/preflight.py), [run store](https://github.com/ethanplusai/jarvis/blob/16e37bd801eb797798e02540665f8b3804b0fe14/run_store.py), [speech](https://github.com/ethanplusai/jarvis/blob/16e37bd801eb797798e02540665f8b3804b0fe14/speech.py), [backpressure regression](https://github.com/ethanplusai/jarvis/blob/16e37bd801eb797798e02540665f8b3804b0fe14/tests/test_voice_backpressure.py)

These patterns fit Apex well: short actionable spoken failure reports, one speech owner, actual run receipts and notification prioritization. Implement equivalents on Windows through Apex's own modules. Do not copy macOS accessibility/Terminal controls, replace the subscription architecture wholesale or assume Fish Audio is required. The current tree does not contain the older `qa.py` sometimes described in secondary summaries; no current fail-open QA claim is made here. Its license is personal/non-commercial with separate commercial terms.

### OpenJarvis: measure changes against a fixed benchmark

OpenJarvis separates skills from the underlying model, inference, agents, memory/tools and learning components. Its skill adapter preserves externally supplied parameters and tags results with skill metadata. [Skill architecture](https://github.com/open-jarvis/OpenJarvis/blob/52659ca7c221703265fe46ff28c5b0a6e2b64c4a/docs/architecture/skills.md), [tool adapter](https://github.com/open-jarvis/OpenJarvis/blob/52659ca7c221703265fe46ff28c5b0a6e2b64c4a/src/openjarvis/skills/tool_adapter.py)

The inspected benchmark gate compares a fixed before/after benchmark and rejects regressions or insufficient improvement. Its evaluation documentation distinguishes correctness evaluation from engine throughput measurements. A reported local-model success figure is not proof of success on Apex's hands/voice/app workflows. [Benchmark gate](https://github.com/open-jarvis/OpenJarvis/blob/52659ca7c221703265fe46ff28c5b0a6e2b64c4a/src/openjarvis/learning/spec_search/gate/benchmark_gate.py), [evaluations](https://github.com/open-jarvis/OpenJarvis/blob/52659ca7c221703265fe46ff28c5b0a6e2b64c4a/docs/user-guide/evaluations.md)

Adopt task-specific baselines and per-category regression limits before routing or skill changes. Apex's structural replay score does not measure semantic correctness. Keep a held-out evaluation set and record model, prompt, toolset, OS and service versions. Training, reinforcement learning and automatic harness search are optional later projects; they require reliable data and a rollback boundary. Documentation of a learning feature is not proof that every named learning backend is implemented.

### ModelScope Ultron: memory, skills and profiles as related but distinct resources

Memory Hub distinguishes direct text ingestion from session trajectory ingestion. Session input is segmented and processed through a quality pipeline; direct text ingestion follows a different route. Treating all imported text as equally verified would miss this distinction. [Memory Hub](https://github.com/modelscope/ultron/blob/801c16233a0c83cab7b7de9467513c5e0f44bf2c/docs/en/Components/MemoryHub.md)

The skill evolution implementation links clusters, source memories, versions, mutation summaries and structure scores. However, `_verification_passed` returns true for a missing verification result; structure scoring then supplies a default. A structure score is also not an executable behavior test. Borrow provenance/versioning and conservative publication intent, but require actual evidence before promoting an Apex skill. [Evolution implementation](https://github.com/modelscope/ultron/blob/801c16233a0c83cab7b7de9467513c5e0f44bf2c/ultron/services/skill/skill_evolution.py)

HarnessHub maps persona/memory/skill files across supported frameworks and provides backup/import flows. Its documented sync is not automatic across devices, and Apex is not one of its built-in semantic mappings. An adapter would require a reviewed field map, secret exclusion, conflict preview and rollback. [HarnessHub](https://github.com/modelscope/ultron/blob/801c16233a0c83cab7b7de9467513c5e0f44bf2c/docs/en/Components/HarnessHub.md)

Apex already has lessons, reflection, skill history, persona and memory. The best integration is an optional export/import/provider adapter over those systems, not replacing them with an additional global memory database. Shared profiles must not silently broaden tool permissions or copy credentials. ModelScope account/service setup has not been performed in this study.

## Adoption register

Statuses: **shipped in this change** means implemented locally and tested; **existing** means an Apex path exists, not that every live workflow passed; **next** means a justified improvement still to implement; **conditional** requires an explicit dependency or evidence; **decline** would duplicate or weaken the design.

| Capability / source | Apex owner | Decision and acceptance condition |
|---|---|---|
| Generated tool inputs / OpenJarvis, Hermes | `skill_forge`, `skills`, `self_mod` | **Shipped in this change:** persist schema through proposal, approval and registration; reject invalid schemas/examples before execution. |
| Truthful installation / Hermes, Ethan | `skill_forge`, dashboard | **Shipped:** failed registration remains pending and returns an HTTP error; no successful-install appearance. |
| Validation level / Ultron, OpenJarvis | `skill_forge`, Approvals | **Shipped:** distinguish syntax-only, sandbox example and legacy/unrecorded. A nonzero sandbox exit cannot pass. |
| Plug-in preview, trust, enable, remove | `plugins`, Repositories | **Existing:** retain pinned provenance and explicit unsupported API messages. |
| Full Hermes API compatibility | `plugins` | **Conditional:** support named APIs only after contract tests; do not advertise universal compatibility. |
| Plugin dependency resolution | `plugins` | **Next:** prepare isolated environments, resolve before activation, keep the old version usable on failure. |
| Plugin activation explanations | `plugins`, System | **Next:** display why loaded/blocked and which configuration caused it. |
| MCP discovery and policy | `mcp_client`, `mcp_policy` | **Existing:** retain one policy/audit boundary for remote tools. |
| App readiness / OpenClaw, Ethan | `apps`, `capabilities`, System | **Next:** one view of installed/enabled/configured/authorized/reachable/tested with timestamps and remedies. |
| MCP expiry/reconnection | `mcp_client`, `apps` | **Next:** simulate expiry, pagination, duplicate names, sleeping clients and failed refresh; never retry a write blindly. |
| Broad app catalogue / all platforms | `mcp_catalog`, Apps | **Existing, authorization-dependent:** count catalogue entries separately from connected accounts and usable tools. |
| Shared memory authority / Jared, Letta | `longterm`, `vault_index`, `working_context` | **Existing pieces; next reconciliation:** document ownership and prevent independent writers from drifting. |
| Memory correction/source inspection / Agent Zero | memory dashboard, `longterm` | **Next:** show source, scope and superseded entries; correction must change subsequent retrieval. |
| Git-backed/shared memory / Letta | memory/vault adapter | **Conditional:** explicit scopes, conflict ownership, deletion propagation and restore test. |
| Ultron profile conversion | persona, skills, memory adapter | **Conditional:** read-only preview first; no secrets or permission expansion; round-trip and rollback tests. |
| Skill provenance / Ultron | `skill_imports`, `skill_md`, `lessons` | **Existing for imports; next for generated code:** link exact evidence, source revision and verification to every promotion. |
| Automatic skill improvement | `reflection`, `skills`, `outcomes` | **Existing approval/rollback paths; next quality gate:** held-out cases must improve without breaking the original input contract. |
| One speech scheduler / Ethan, Backtalk | `celine`, voice stack | **Next audit:** sentence priority, cancellation generation IDs and no stale audio after interruption. |
| Voice backpressure | companion streaming | **Next:** sleeping/disconnected clients cannot block others; bound queues and prove recovery. |
| Mic/hand/job state separation | companion, hand tracking, board | **Existing partial fixes; live verification needed:** left/right hands, text mode and microphone failures must remain independent. |
| Persistent transforms | `board_workspaces`, board UI | **Existing; live verification needed:** release, tracker stop, reload and restart retain the intended transform. |
| Per-part selection | `board_parts`, geometry selection | **Existing fallback; conditional semantics:** authored/mesh boundaries are usable; ambiguous monolithic meshes need authoring or segmentation. |
| Face/theme adaptation | shared brand/companion styles | **Next audit:** theme changes apply consistently, with contrast and reduced motion; stale state visibly expires. |
| Bidirectional spatial context | board readout / companion | **Existing:** keep stable object and part IDs in what Celine receives. |
| Run receipts / Ethan, LibreChat | `team`, `trajectory`, `companion_jobs` | **Existing records; next UI join:** one visible timeline links tools, artifacts, outcome and errors. |
| Event branching / OpenHands | conversations/trajectory | **Conditional:** branch only with immutable parent IDs and explicit side-effect policy. |
| Recovery after interruption | `task_recovery` | **Existing:** reconcile unknown effects before continuation; never replay automatically. |
| Repeated error loops | `core`, `recovery` | **Next audit:** bounded detection using actions plus observations, exempting legitimate polling. |
| Workflow recipes / Dify | scheduler/team/skills | **Next:** typed step inputs and explicit retry/idempotency rules before adding a visual editor. |
| Multiple coding engines / OpenHands | provider/subscription/team | **Conditional:** one backend adapter at a time, same artifact and cancellation contract. |
| Model hardware suitability / Odysseus | provider/model settings | **Next:** verified memory/backend requirements, failure details and a known-good fallback. |
| Evaluation gates / OpenJarvis | `eval`, `verification`, outcomes | **Next:** semantic task fixtures plus measured cost/latency; structural completion alone cannot score success. |
| Context budgets / Odysseus, Letta | `memory`, skill/tool discovery | **Next:** show contributions and select only relevant schemas/instructions; test small-context configurations. |
| Unified startup / Jared, Ethan | launcher/resident/System | **Existing launch coordination; next diagnostics:** per-component readiness, exact failure and repair action without a second launcher. |
| Safe updates / Hermes | `control`, supervisor, plugin lifecycle | **Existing clean/fast-forward checks; next validation:** fresh install, migrations and failed-upgrade recovery on Windows. |
| Global perception ingestion | `file_events`, perception | **Existing ignore rules:** prove build/cache/lock churn remains silent while real user edits are retained. |
| Entire upstream app merges | Apex core | **Decline:** duplication and incompatible lifecycle assumptions outweigh catalogue count. |
| Automatic memory/training uploads | optional exporters | **Decline by default:** sharing is a separate user-controlled feature, not a side effect of learning. |
| Personality as capability proof | persona/UI | **Decline:** expressive presentation cannot substitute for successful execution. |

## Implementation delivered with this study

The changes are Apex-native implementations; no upstream code was transplanted.

1. Both forge prompts request an input schema. A bounded self-contained schema and matching example are checked before code validation. Schema references are rejected, so validation cannot fetch remote resources. Older proposals infer visible property names from their stored example without inventing required fields or types.
2. Pending dynamic tools persist their schema, and approval supplies it to registration. Executable skill creation/improvement preserves the schema as `INPUT_SCHEMA` in the staged code. An actual approval-path test loads the skill and calls it with a named argument.
3. A registration error string no longer marks a tool approved. The dashboard returns a conflict response so its existing error presentation shows the failure.
4. Forge rows retain the validation level. Approvals distinguishes a sandbox smoke example from syntax-only checking and an older row with no record. These labels do not claim behavioral correctness or sandboxed execution after approval.
5. Sandbox validation requires a successful process exit as well as a successful result envelope. A misleading success envelope from a failed process is rejected.

Local validation: **121 tests passed**, with one existing Starlette/AnyIO deprecation warning. The headless Chrome management-flow check passed, including the new validation labels, repository/plugin flows and mobile width. Those browser checks use stubbed APIs; they do not establish live OAuth, model output quality or webcam performance. Docker/model calls were mocked where fixtures say so. No external service was authorized and no upstream installer was run.

## Benchmark to use before claiming parity

Run the same tasks against Apex and each relevant comparison system, using separate disposable workspaces and equivalent model/tool access. Record installed revision, model, operating system, hardware, permissions, warm/cold startup and available accounts. Do not compare systems on tasks they do not implement; report coverage separately from success rate.

| Scenario | Required evidence / pass condition |
|---|---|
| Start from a clean Windows install | UI, model and voice health reported separately; unavailable features explain the remedy. |
| Talk, interrupt, continue | Old audio stops; no stale playback; next utterance is accepted. Measure median and p95 first-audio delay. |
| Left hand, right hand, both hands | Intentional grab/release works; open hands do not grab; state survives tracking loss. Record false grabs and missed grabs over repeated trials. |
| Move an engine, stop tracking, reload | Position/rotation/scale persist; selected part ID agrees with agent readout. |
| Import a GitHub skill | Pinned source, license, required tools and unsupported content visible; failed install leaves previous skill intact. |
| Learn a skill by voice | Named inputs survive approval; known-good, invalid and edge-case inputs have expected outputs; version can be reverted. |
| Improve an existing skill | Original cases still pass, held-out cases improve, missing evaluation blocks promotion. |
| Correct a remembered fact | Next response uses correction in the right project; deleted/old fact is not reintroduced by another store. |
| Interrupt an external write | Receipt identifies known/unknown effects; resume does not duplicate the write. |
| Expired app authorization | Clear reconnect requirement; secrets absent from UI/logs; read test works after reconnect. |
| Slow browser client | Another client continues receiving voice/progress; queues stay bounded and recovery works. |
| Broken plugin/update | Activation refuses or rolls back; old app remains usable; actual cause is visible. |
| File watcher noise | Generated/cache/lock changes ignored; a genuine source/document edit still produces a signal. |

Score task correctness, completion without rescue, false success, recovery, time-to-success, cost with unknown values preserved, and interaction failures. Keep exact transcripts and redacted receipts. Unit-test count and app-catalogue size are supporting metrics, not a product score.

## Delivery order

1. Finish live interaction proof: hands, persistent transforms, part selection, voice interruption and state separation. These are Apex's differentiators and the user's known pain points.
2. Consolidate runtime diagnostics and memory correction. Expose the existing working systems before adding screens.
3. Complete the skill lifecycle: input contract (implemented here), evidence receipt, regression cases, promotion and rollback.
4. Prove a small set of complete app workflows after Composio/account authorization. The catalogue can remain broad, while readiness stays honest.
5. Add optional Ultron profile exchange and coding/workflow adapters where the benchmark shows a benefit. Keep training and fleet-wide shared memory behind evidence and actual need.

This order is an adoption plan, not a claim that the register is fully implemented. The live benchmark, remaining adapters, memory UI consolidation and app authorizations are still outstanding.

## Reuse terms observed

These are root-license observations for the pinned snapshots, not a blanket clearance for every dependency, asset or subdirectory. Links are in the source index. No code reuse from these repositories is included in this patch.

| Repository family | Root license observed | Adoption consequence |
|---|---|---|
| Hermes, OpenClaw, Agent Zero, LibreChat, OpenHands app/SDK | MIT | Preserve applicable notices if source is reused; still check dependencies/assets. |
| Letta Code, OpenJarvis, ModelScope Ultron | Apache-2.0 | Track source notices and any applicable NOTICE requirements when reusing source. |
| Odysseus, Jared's Fullstack Agent/Barehands/Backtalk/AI Visualizer | AGPL-3.0 family | Do not silently relabel transplanted source as Apex MIT; assess distribution/service obligations before reuse. |
| Jared's AI Memory Vault | CC BY-SA 4.0 | Treat templates/tutorial text separately from executable code. |
| Ethan's Jarvis | Custom personal/non-commercial terms | Independent Apex implementations of general patterns; no source transplant in this patch. |
| Open WebUI | Current custom license with branding conditions | Do not assume older releases' terms govern this snapshot. |
| Dify | Modified Apache-2.0 with additional conditions | Evaluate the actual conditions before adopting its code or frontend. |

## Remaining uncertainty

The investigation did not run every upstream application, examine all tests, benchmark models, audit every security boundary, or prove a universal plugin adapter. The external repositories move rapidly; the pinned source index makes this review reproducible rather than permanently current. No evidence here establishes that any one system is objectively “best” for every task. Apex's next credible improvement is demonstrated workflow success, not a new numerical rating.
