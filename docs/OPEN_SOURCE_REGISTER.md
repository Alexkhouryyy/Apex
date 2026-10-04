# Open-source register

**Observed 2026-10-04.** Next review 2027-01-04. Machine-readable source: [`research/oss-register.json`](research/oss-register.json).

This is Phase 4's candidate register from the environment roadmap, started small. It covers three gaps named in the [capability matrix](CAPABILITY_MATRIX.md). For each gap it lists the candidates (projects with at least 1,000 stars) and gives each one a verdict: **integrate**, **adapt**, **learn only** or **avoid**. Every candidate is pinned to the exact revision that was reviewed.

**What this is not:**
- Not an exhaustive search. The GitHub search API wasn't reachable from the review machine, so candidates come from targeted capability searches and known projects. The intended search queries are recorded in the JSON.
- Not a security audit of the code. Licence, activity, dependencies and fit were checked; the source wasn't read line by line.
- Star counts are as shown on each page that day, rounded. They change.

| Verdict | Means |
| --- | --- |
| **Integrate** | A version-pinned adapter, acceptance evidence and a rollback path |
| **Adapt** | A bounded port with provenance, notices kept and an update owner |
| Learn only | A documented lesson; no production dependency |
| Avoid | A recorded reason and the condition for a later review |

## 1. Voice: knowing when you've finished talking

**The gap:** Hands-free voice decides you stopped talking by loudness, then waits a fixed 1.2 s of silence. That wait is most of Pillar 1's 1.5 s budget. Background noise also triggers barge-in, and the threshold is a manual slider.

**Apex today:** dashboard/static/companion.js + ApexHandsFree: an RMS threshold, a 1.2 s silence timer, and barge-in at 3x the threshold for 0.2 s. On the PC side, faster-whisper vad_filter is used only inside transcription.

**Success means:** Measured by agent.voice_timing over 20 turns: end-of-speech-to-transcript drops by at least 0.5 s at the median, with no more cut-off sentences than today (counted the same way).

| Project | Stars | Licence | Verdict | Why |
| --- | --- | --- | --- | --- |
| [ricky0123/vad](https://github.com/ricky0123/vad/tree/2e5aca93e3902f04ff5b7eb4a9633926cdca9cf6) | 2.1k | ISC | **Integrate** | Small, permissive, and fits the existing hands-free interface. Pilot it behind the existing hands-free switch, with the RMS path kept as fallback and rollback. |
| [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn/tree/4786657e242dfe77dd138699ac564ee074a2a543) | 1.6k | BSD-2-Clause (code) | **Adapt** | Valuable, but only after the VAD pilot gives a measured baseline. The weights license must be confirmed first. |
| [snakers4/silero-vad](https://github.com/snakers4/silero-vad/tree/1e261b036686cd0017d500ee96acd1c4ba572a9d) | 10.3k | MIT | Learn only | The model is used via vad-web. No separate dependency needed. |
| [pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat/tree/c027943125c0ef7ea1b946b6c438032eb8ff0483) | 16.2k | BSD-2-Clause | Learn only | Adopting the framework would replace working code and add heavy churn. Take the design lessons only. |
| [KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT/tree/777727553eedfa19aead15337ce66bab549add3f) | 10.2k | MIT | Learn only | Wrong side of the microphone for the companion. |
| [livekit/agents](https://github.com/livekit/agents/tree/530ec952c030a68fcb4ac94c0a22c8b58806a256) | 14.5k | Apache-2.0 (framework); turn-detector weights under the LiveKit Model License | Avoid | A server dependency and a separate model license, while smart-turn covers the same need under BSD-2. |

## 2. Source documents: Word, PowerPoint, Excel and scans

**The gap:** Projects can only take plain text and text-layer PDFs as source material. Word, PowerPoint, Excel and scanned PDFs come in empty or not at all, so the roadmap's workflow ("start from a brief and supporting documents") fails on common files.

**Apex today:** agent/knowledge.py and agent/apocalypse.py use pypdf for PDFs, and plain reads for text. The knowledge base also needs the embedding model. scripts/apex_demo.py uses a markdown source.

**Success means:** A fixed set of 10 real files (docx, pptx, xlsx, 2 PDFs with tables, 1 scanned PDF) converts to text that contains each file's checked key facts, on the Windows laptop, offline, in under 5 s per file for non-scanned files.

| Project | Stars | Licence | Verdict | Why |
| --- | --- | --- | --- | --- |
| [microsoft/markitdown](https://github.com/microsoft/markitdown/tree/4cc9fa17653d695d64fb9eee5b33d4de55ff84e8) | 188.3k | MIT | **Integrate** | The smallest change that closes most of the gap. Pin the version, wrap it in one function, and keep pypdf as fallback and rollback. |
| [docling-project/docling](https://github.com/docling-project/docling/tree/58f1f8d907fdc4610cd8dca20da6ef53a4550ee8) | 68.4k | MIT | **Adapt** | Offer it only as an optional "deep conversion" for scans and tables, after markitdown covers the common case. |
| [Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured/tree/2c0c7a6ca71334bdfbbcb083311c4c859c0b59c7) | 15.5k | Apache-2.0 | Avoid | Install burden on Windows, and overlap with markitdown and docling. |
| [datalab-to/marker](https://github.com/datalab-to/marker/tree/e7c67f1d239ea6a805cbf4ed6c6b2056d435e22d) | 40.2k | Apache-2.0 (code); modified OpenRAIL-M (model weights) | Avoid | Usage-conditional weights license; docling covers the same ground under MIT. |

## 3. Task evaluation with recorded cost

**The gap:** Phase 2's exit evidence is "two representative tasks complete end to end with inspectable evidence and recorded cost". Phase 5 needs a predefined task benchmark. Apex has a structural replay harness (agent/eval.py), cost telemetry (usage_log), goal verification contracts and the demonstration script, but no repeatable task benchmark that reports success and cost together.

**Apex today:** agent/eval.py replays logged turns and scores them structurally; agent/telemetry.py records tokens and cost per call; agent/verification.py checks completion contracts; scripts/apex_demo.py runs the acceptance test.

**Success means:** One command runs 2+ representative tasks against the configured model, and reports per task: pass/fail from checks (not model self-report), the tools used, and the dollar cost from usage_log. The results are comparable between two models.

| Project | Stars | Licence | Verdict | Why |
| --- | --- | --- | --- | --- |
| [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai/tree/9f6accda8dce5ccd754e6be1a22fbcee96042e82) | 2.9k | MIT | Learn only | Copy the structure (task, checks, cost per sample, viewable log) into an extension of apex_demo, not the dependency. |
| [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo/tree/5fde786d0c296339be3fd189d1542157e1d4217a) | 25.7k | MIT | Learn only | Wrong layer for task evaluation. Borrow the injection test cases. |
| [confident-ai/deepeval](https://github.com/confident-ai/deepeval/tree/a200ecede6456f92d7cbbb350b7b3fb4d78d36b3) | 18.6k | Apache-2.0 | Avoid | Model-graded by default and pulls toward a hosted service; Apex's contracts already do deterministic checks. |
| [langfuse/langfuse](https://github.com/langfuse/langfuse/tree/f75c661dbe8c6b85523c81486b39e8403ac2c141) | 35.4k | MIT, except ee/ folders (commercial) | Avoid | Infrastructure burden out of proportion to a single-user environment. |

## What to do first

Pilots follow the roadmap's rule: one module at a time, behind a switch, with the old path kept as the rollback.

1. **Speech detection in the browser (`ricky0123/vad`).** Small and permissive, and it attacks the biggest piece of voice delay, the fixed 1.2 s wait. **Piloted 2026-10-04:** `Setup-Apex-Speech-Model.cmd` installs it, and hands-free uses it with loudness as the fallback ([HANDS_FREE_COMPANION.md](HANDS_FREE_COMPANION.md)). It still needs measuring on the laptop: about 10 hands-free turns each way, compared by `agent.voice_timing`.
2. **Office files as sources (`markitdown`).** One function with local paths only and no URLs, with `pypdf` kept as the fallback. Acceptance: the 10-file set.
3. **Task suite in the demo.** Take the structure from `inspect_ai` without the dependency: two real tasks, checks instead of model self-grading, and cost read from Apex's own `usage_log`. This is Phase 2's exit evidence.

Later, and only with evidence:
- **`smart-turn`**, once the VAD pilot has numbers. Its model-weights licence must be read first; it couldn't be checked from here.
- **`docling`**, for scans and tables, once its install size on the laptop is measured.

**Rejected, with the condition that would reopen them:**
- **`livekit/agents`:** reopen only if smart-turn's weights are restricted.
- **`unstructured`:** reopen if document processing moves to the Linux relay.
- **`marker`:** its weights licence depends on revenue.
- **`deepeval`:** model-graded, and pulls toward a hosted service.
- **`langfuse`:** needs four servers.
