# Apex research integration suite

This branch covers the briefs dated **18 September, 2 October and 9 October 2026**, in that order: 18 primary papers and 11 watchlist items. It implements reusable components, original Apex prototypes, operator controls and offline evaluation seams. **It does not establish the papers' numerical gains in Apex.** The proposed live experiments need independently labeled histories, model outputs and measured costs; the personalization study also needs consenting participants. New experimental behavior remains opt-in. Merged into main (PRs #47-#49); the governed store is not read by normal turns, and the Memory tab labels its records that way.

## What was imported

| Resource | Pinned revision / version | Actual reuse | License |
|---|---|---|---|
| Chronicle | `agent-chronicle==0.5.0`; `b9c0630d3df4e63f8b5041778d582140576cad1f` | Real recording, JSONL/export and cut-point replay library | MIT |
| [When Debate Helps](https://github.com/Wang-ML-Lab/when-debate-helps) | `899b2834acd13f7b3298eb88233d39ab419d7a15` | Metrics; opening/revision trace adapter | Apache-2.0 |
| [Engram](https://github.com/ris3abh/Engram) | `2080752aaabb38b56a28f2e68429cfde18b6739d` | `HashEmbedder`, `top_k`; isolated raw/extracted comparison | MIT |
| [MemAgent](https://github.com/WalkerWorldPeace/MemAgent) | `7121cfe991c329748cd610515a3419cabc875ba6` | Provider interface and typed requests/responses, with Apex scope-first providers | Apache-2.0 |
| [Hermes](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.14) | `345cd2b057a452236de401d3534b8502a7465e8d` | Advisory kernel-lock helpers for participating SQLite writers | MIT |
| [Mem++](https://github.com/AIDAChip-Inc/mem-plus-plus) | `26ae8787542e1b1024dae37dc2c388bbf2411d05` | Weighted reciprocal rank fusion; unchanged function body with standalone default | Apache-2.0 |
| [HGP](https://github.com/Ouan6/HGP-) | `265c7c28137e583bf79a15295b74720feece50e6` | Threshold method, unchanged; Apex supplies validated state and conservative type deferral | MIT |
| [Learn2Play](https://github.com/liushiliushi/Learn2Play-Bench) | `5e5fdf9db65d6ab45314f2baa0f7bcddcfc75624` | External-checkout probe, all 20 game interfaces tested; no game code copied | No software license found |

Attribution and license copies accompany vendored components under `agent/_vendor`. No SkillAA source was copied: that repository still has no explicit software license. Original Apex implementations use the documented architectural ideas rather than copying unlicensed code. Research snapshots are isolated from the upstream agents, their credential stores and automatic training loops.

## 18 September: each recommendation

1. **[Groups overstate consensus](https://arxiv.org/abs/2609.20543).** Council traces retain independent openings and revisions. `research_metrics.communication` separately reports correction, preservation, supported-dissent survival, unanimous-wrong outcomes and the no-message revision control. Correctness is supplied by independent labels, never unanimity or chair confidence. Existing Council readout retains external exact-answer labels. The Zenodo artifact at DOI `10.5281/zenodo.21318346` could not be downloaded in this session, so its human replay trajectories were not imported. The 100-replay/30% reduction target is an outstanding empirical experiment.

2. **[SAIGE](https://arxiv.org/abs/2609.19759).** `dependency_plan` validates a DAG, rejects missing/cyclic dependencies, measures direct dependency density and keeps uncertain/dense work with the parent. Independent waves run at most four specialists through the existing orchestrator, role restrictions and budgets; only declared predecessor artifacts reach each worker. Failed predecessors block dependents. `run_dependency_plan` is wired into the parent tool dispatcher; workers cannot recursively call it. One 10-minute budget covers the whole plan, the turn's Stop ends it within a second, and a long worker result is cut to 15,000 characters instead of failing its dependents. This is an original heuristic, not a reproduction of the unpublished dependency-inference method. The 50-task sparse/dense experiment remains unmeasured.

3. **[SkillAA](https://arxiv.org/abs/2609.20455).** `learning_registry` stores addressable applicability/procedure/exclusion objects and supporting/contradicting trace IDs. Attribution to retrieval, perception, permission or model capability produces `NO_PATCH`. `learning_eval` applies the local affected-scope gate before held-out, OOD, safety, regression and cost checks. Candidates progress through versioned states with a promotion lease and canary retirement/rollback. No source copy or paid paper-scale run occurred. The synthetic citation pilot executes a narrow literal-quote check; it is not a learned citation skill evaluation on real incidents.

4. **[Chronicle](https://arxiv.org/abs/2609.20625).** Real Chronicle boundaries now cover routing, tool dispatch, non-streaming SDK responses, terminal streaming-turn results, memory admission/writes, skill promotion, dependency planning, Council selection and context selection. Reviewed pure decisions can run live while recorded effects remain stubbed. SDK snapshots preserve tool calls and usage without serializing clients. Ten seeded admission incidents replay twenty times each with zero live calls/effects. Streaming fixtures reproduce the terminal result, not event timing or UI effects. Full `AgentCore.run` remains blocked in replay; subscription and arbitrary orchestration are not end-to-end replayable. The <1% *live-turn* overhead target is unmeasured.

5. **[Tailored to you](https://arxiv.org/abs/2609.20077).** Approved evidence records distinguish observed from inferred information, retain sources and allowed purposes, and expose owner-only inspect/forget APIs plus dashboard controls. Recall returns citations and provenance. Forget purges the source and transitively linked governed-memory views, then tombstones IDs to prevent resurrection. This covers the governed store; it does not promise deletion from legacy memory, historical conversations, incident archives, backups or external services. Human awareness, disclosure and satisfaction require the opt-in human pilot and are unmeasured.

6. **[Harness design](https://arxiv.org/abs/2609.20804).** The optional reversible context path selects deterministic whole dependency groups before any paid summary call, retains canonical history and preserves user instructions/latest writes. Missing dependencies or protection overflow keep full context. Existing summarization remains the default. This supplies no-compaction versus deterministic-selection versus legacy-summary arms; it does not implement or claim the paper's exact elision-to-summary hybrid, capability-trained planning policy or action-space optimum. The 50-task matched-cost/quality comparison remains pending.

## 2 October: each recommendation

The brief's arXiv IDs for all six primary papers were mismatched. The links below identify the studies matching the titles and methods; the original identifiers are recorded in `source-corrections.md`.

7. **[MemAgent](https://arxiv.org/abs/2609.32521).** The actual licensed provider interface is reused. Approved fact, episode and procedure providers route reads deterministically; unknown task signatures use facts only. Typed owner writes reject inferred permission and retain immutable raw records. The optional HGP score adapter can route an owner-supplied record type, but cannot grant approval. No 13-provider learned policy or model training is imported. The 50-task pass-rate, cost and latency experiment is unmeasured.

8. **[Selection versus extraction / Engram](https://arxiv.org/abs/2609.34227).** `memory_selection` and its CLI compare raw/extracted arms with cosine and recorded reranking at 300/1,000/4,000 conservative UTF-8-byte budget units. Scope, revocation and as-of filters precede embedding. Hash embeddings are explicitly fixture-only; real semantic runs require frozen normalized vectors and scores. Reports measure evidence recall and unanswerable-context exposure, not answer accuracy or correct abstention. Actual tokenizer-matched answer generation and write costs remain required for the brief's acceptance target.

9. **[You're Hired](https://arxiv.org/abs/2609.38816).** `council_selection` enumerates bounded teams using same-case held-out capability vectors, joint failure, error covariance and measured token cost. It rejects train/held-out overlap and incomplete/self-rated labels, and falls back when calibration is absent. Optional per-domain JSON profiles wire into Council via `COUNCIL_PROFILE_PATH`. This is a transparent heuristic, not a learned selector or a proven gain. The six-model/100-query profiling experiment remains unmeasured.

10. **[MADBench](https://arxiv.org/abs/2609.39146).** Six internal attack-family fixtures exercise the real role-limited dispatcher: direct/indirect injection, poisoned retrieval, tool hijacking, collusion and coordinator claims cannot grant a researcher write access. Existing deterministic tool authority remains separate from debate. Paired readouts count damage and measured cost. These are synthetic authority tests, not the released MADBench data or a 100-task attacked-Council performance evaluation; semantic answer poisoning is still an empirical risk.

11. **[Held-out skill generalization / GSO](https://arxiv.org/abs/2609.39148).** `task_skills.run` binds a stable metaskill to a generated, independently validated task-local procedure, executes within a scoped prompt view, and discards it on success or exception. Generation/execution/evaluation callbacks belong to the host; no arbitrary generated code is executed by this helper. Durable candidates require frozen held-out contracts and operator promotion. The GSO experiment records a stricter 1.20× token limit than the general 1.25× gate default. Actual learned-skill generalization is unmeasured.

12. **[ReCAP](https://arxiv.org/abs/2609.40118).** `context_graph` provides immutable-prefix archives, dependency-closed selection, protected instructions/latest writes, exact revival and cold restore. Repeated selection preserves previously omitted blocks. The linked repository still has no HEAD implementation to import. The Apex selector uses lexical relevance and recency, not attention weights or a proxy model. Byte budgets are not token counts. The 40% token-reduction/quality/latency target remains unmeasured on real tasks.

## 9 October: each recommendation

13. **[When Debate Helps](https://arxiv.org/abs/2610.04686).** Actual upstream metrics separate proposal supply, recoverable headroom, recovered headroom and vote-correct damage. Opening and revision traces retain external answer labels; optional verification-aware prompts preserve minority proposals without converting confidence into correctness. Model weights and benchmark data are not imported. Existing integration is documented in `council-readout-integration.md`. The 100-replay wrong-consensus reduction target is unmeasured.

14. **[Agent Plasticity](https://arxiv.org/abs/2610.08902).** Receipts distinguish task outcomes from held-out learning gain per dollar, record learning/inference costs, reject severe/safety/OOD regressions and identify negative checkpoints/saturation. Costs need provenance; zero cost leaves gain-per-dollar undefined rather than infinity. Two negative checkpoints disable the learner policy. No model learning lineages were run; the three-configuration/30-incident comparison remains pending.

15. **[Learn2Play](https://arxiv.org/abs/2610.08215).** The previously unavailable repository is reachable at the pinned revision. `learn2play_probe` runs its standard-library interface externally with host credentials removed and results outside the checkout. All 20 canonical game protocols completed. The snapshot has no software license, so no games were vendored. Interface completion is not episode success, transfer or raw-history-versus-rules improvement. Canonical context archives and skill support/contradiction references implement the raw-evidence principle; the ten-workflow learning comparison remains unmeasured.

16. **[MemTrim](https://arxiv.org/abs/2610.07311).** Admission checks task signatures, original support facts, expiry, duplicates, current-fact disagreement and mutually conflicting facts. Changed-support outcomes are removed; duplicate evidence receives no repeated slot. The 200 synthetic policy cases preserve their beneficial unit and exclude every inadmissible unit. That is not an 80% reduction in model-induced answer regressions or proof of beneficial task utility.

17. **[Memory governance](https://arxiv.org/abs/2610.11188).** Subject, domain, purpose, explicit permission, inference policy, freshness and conflict admission precede retrieval ranking and prompt presentation. Rejection reports contain IDs/reasons rather than excluded text. Owner-only routes ignore supplied subject/approval fields. Deterministic policy tests and latency measurements pass locally, but the task-utility target has not been measured. Enforcement is limited to calls using the governed store and a host-supplied `Context`; it does not migrate or globally govern legacy recall/prompt files.

18. **[Who verifies the verifier?](https://arxiv.org/abs/2610.11464).** Named contracts freeze split IDs, thresholds and mixed pass/fail anchors. Exact observations are required; all-pass/all-abstain/all-fail graders, missing labels, permissions, severe regressions, excessive costs and artifact/baseline mismatches fail. Receipts bind the contract, candidate, baseline and observations; active-version leases prevent stale promotion. There is no model-facing evaluation/promote tool. Five synthetic reward-hacking candidates are rejected. Human-anchored semantic grading, the reference-loop gain comparison and evaluation-cost target remain unmeasured.

## Every watchlist item

| Brief | Item | Concrete handling | Limit |
|---|---|---|---|
| Sep 18 | [MTVA-Bench](https://arxiv.org/abs/2609.20152) | `voice_trace` scores tool choice, arguments, order, externally labeled rules and spoken conduct separately | No audio, latency, channel damage or original benchmark artifact |
| Sep 18 | [Memory Has Geometry](https://arxiv.org/abs/2609.17969) | Explicit event time, freshness, changing support and uncertainty deferral rather than inferred timeless facts | Conceptual mapping only; no geometry model or validated user-state estimator |
| Sep 18 | Hermes v2026.9.14 | Licensed kernel locks serialize participating writers; three-process contention test | No OAuth integration; SQLite's native locking still governs nonparticipating writers |
| Oct 2 | [Rep2Skill](https://arxiv.org/abs/2609.39149) | Representation-based learner remains explicitly unavailable; fixed external execution gates can accept future candidates | Requires hidden states and grouped rollouts; no implementation imported |
| Oct 2 | [Communication audit](https://arxiv.org/abs/2610.01042) | Independent correction/preservation/dissent/no-message metrics and CLI | No measured communication gain on Apex histories |
| Oct 2 | [Mem++](https://arxiv.org/abs/2610.02002) | Actual RRF helper, immutable evidence, lexical/frozen-semantic fusion and observed-event temporal bounds | No upstream PostgreSQL stack or OrgMemBench reproduction |
| Oct 9 | [REMORY](https://arxiv.org/abs/2610.11287) | Explicitly blocked neural method; exact archived history remains revivable | Soft input vectors/actor gradients require white-box integration and substantial training |
| Oct 9 | [HGP](https://arxiv.org/abs/2610.10071) | Actual licensed threshold method; optional owner-score type routing defers uncertain/multi-label cases | No classifier checkpoint, automatic training, Neo4j or claimed 5-ms end-to-end behavior |
| Oct 9 | [SkillForge](https://arxiv.org/abs/2610.09832) | Original trial/active/stable/retired lifecycle with canary retirement and prior-version reactivation | No co-trained model weights or reported benchmark gain |
| Oct 9 | [RH-Detect](https://arxiv.org/abs/2610.10947) | Offline labeled screening readout and separate multi-turn confusion matrices; never authorization | Dataset download/card could not be verified (401); no dataset rows copied or detector-performance claim |
| Oct 9 | [Hippocam](https://arxiv.org/abs/2610.12124) | Task signatures, linked source evidence and revivable canonical archives supply compatible building blocks | No intent-stack/recursive consolidation method implemented or claimed |

## Operator workflow and trust boundary

Install `requirements-research.txt` alongside the normal requirements. Experimental switches:

- `INCIDENT_RECORDING_ENABLED=true`: opt-in sanitized trace recording.
- `REVERSIBLE_CONTEXT_ENABLED=true`, `CONTEXT_SELECTION_BYTES=64000`: deterministic context selection instead of paid summarization at the existing threshold. Protected overflow keeps full history and can still overflow the model; it is surfaced, not solved.
- `COUNCIL_PROFILE_PATH=/absolute/profile.json`: optional externally calibrated member selection.
- `COUNCIL_VERIFY_READOUT=true`: existing verification-aware Council prompt mode.
- `SKILL_EVALUATION_REQUIRED=true`: generated skill installation requires an independently evaluated and promoted exact code version, even after ordinary approval. Existing human approval remains required. Manual skills remain outside this evaluated lifecycle.

Use `python -m scripts.learning_registry freeze CONTRACT.json`, then `propose NAME CODE.py OBJECT.json`. Run `python -m scripts.learning_gate --contract CONTRACT.json --observations OBSERVATIONS.json --artifact CODE.py --candidate-id ID --output RECEIPT.json`. Promote with `python -m scripts.learning_registry promote ID --expected-active PRIOR_ID`; omit the prior flag for a first version. Evaluations of successor versions must provide the prior version's `baseline_artifact_sha`. Contract IDs cannot be redefined. Canary observations come from trusted executions or owner controls. Two consecutive failures or any permission violation/verified contradiction retire the version and reactivate its eligible predecessor metadata. **Rollback does not rewrite Python files:** execution is blocked until the known prior bytes are restored. Automatic execution resumes only when active status and the installed full-source hash agree.

The registry is an audit/consistency boundary, **not an OS sandbox**. Its SHA receipts detect mismatches, not malicious forgery by code that can write the DB. A production learner must run under a separate identity/container without write access to evaluator code, frozen cases, receipts or the registry; the trusted operator/evaluator process performs promotion. Apex's general owner-authorized Python/shell environment is not that isolation. Keep the evaluation-required path experimental until this deployment boundary and real acceptance experiments are established. Neither model confidence nor these offline readouts grants tool authority.

`AgentCore.run(memory_context=Context(...))` or `memory_governance.use(Context(...))` binds trusted subject/domain/purpose/support to calls; under that scope the existing `recall` dispatcher uses admitted portfolio evidence. The scope follows specialist and DAG worker threads, and automatic summarization cannot approve or persist its generated facts into legacy memory. Explicit owner memory management uses `/api/research/memory`, `/memory/recall`, and `/memory/{id}/forget`. Inspection, canonical revival and canary routes require the owner token and same-origin writes. The dashboard shows only governed memory in its Approved evidence panel, alongside the existing legacy memory table.

Offline readouts:

```sh
python -m scripts.research_readout communication LABELS.json --output REPORT.json
python -m scripts.research_readout screening LABELS.json --output REPORT.json
python -m scripts.research_readout voice LABELS.json --output REPORT.json
python -m scripts.research_readout paired LABELS.json --output REPORT.json
python -m scripts.research_experiment memtrim --contracts docs/research/experiment-contracts.json --evidence MEASUREMENTS.json --output READINESS.json
```

`experiment-contracts.json` records all 18 proposed acceptance targets. Missing metrics, wrong evidence kinds and failed thresholds yield `not_ready`; no result is authorization. Sources and labels are supplied by the trusted evaluator. The files are templates that the operator freezes, not secret test assets inaccessible to a host with full filesystem permissions.

## Validation and outstanding work

The committed results in `results/` are explicitly labeled synthetic engineering validation or artifact-interface checks. They do not support production promotion. Ten permanent seeded Chronicle fixtures are in `tests/fixtures/research/incidents`; repeated replay and reviewed pure cuts run without live effects. The citation pilot uses a deliberately weak first-word baseline and exact literal quotes against synthetic snapshots, not current Apex as its baseline.

Local verification: 469 affected-code regression tests passed; 65 existing/new dashboard and DOM checks passed, and boot smoke passed all 14 checks. SQL auditing checked 165 simple SELECTs against 84 tables with no invalid columns (145 complex queries were outside the checker). Wiring auditing reported four pre-existing findings. The full local suite could not complete: automatic review stopped unexpected Microsoft telemetry egress despite HTTP/native-client unit-test guards. A network-isolated namespace was unavailable. The full-suite status remains unverified; the rejection was not bypassed. See the final PR for the exact final test count and CI status.

Still required before claiming all research experiments complete: real labeled Apex histories and measured matched budgets, human awareness/satisfaction evidence, tokenizer-matched Engram answers/abstention, learned router/selector calibration, full adversarial debate outcomes, evaluator OS isolation, original blocked data/artifacts, and white-box model/training access for Rep2Skill/REMORY/SkillForge. Every such gap is recorded above rather than silently treated as success.
