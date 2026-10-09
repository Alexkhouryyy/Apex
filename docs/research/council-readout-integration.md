# When Debate Helps: Council readout integration

Source: https://github.com/Wang-ML-Lab/when-debate-helps
Pinned revision: `899b2834acd13f7b3298eb88233d39ab419d7a15` (version 0.1.0).

## Code actually reused

`agent/_vendor/when_debate_helps/metrics.py` is a byte-for-byte copy of the
upstream metrics module. Its Apache-2.0 license and attribution are alongside
it. Upstream metric tests are retained with their import adapted to Apex.
No GPU generation code, perturbed model weights, datasets, or Transformers/Torch
dependencies are added. This is selected-code reuse, not just an imitation of
the paper's metric formulas or a full reproduction of its experiments.

## Runtime connection

Every completed `council.convene()` now includes a `readout_trace`:

- independent opening responses, preserved before debate;
- revisions grouped by round, with stable model IDs and ordering;
- explicit provider and chair failures;
- final response and standard/verify mode;
- null correctness/vote fields and `evaluation_status: unscored`.

The `/api/council` response and `council_done` event expose this trace. The agent
tool returns only its trace ID and scoring status alongside the final answer,
so the full debate is not duplicated into the calling model's context.
Returned traces are not automatically persisted in a new database table.
Save the API's `readout_trace` or the Python result to a private JSONL file if
you want to evaluate it. Existing agreement and chair-confidence fields are
unchanged and are never used as correctness labels.

Trace creation is best-effort: its failure cannot discard a completed answer.

## Verification-aware prompt experiment

Enable with `.env` and restart:

```ini
COUNCIL_VERIFY_READOUT=true
```

Or run a single Python experiment:

```python
from agent import council
result = council.convene(question, rounds=1, verify_readout=True)
```

Debaters and the chair are asked to check decisive reasoning, assumptions and
available evidence, preserve supported minority answers, and report verification
gaps. The ordinary mode remains the default and is available for comparison via
`verify_readout=False`. Both modes use the same number of model calls and output
limits; the verify mode adds prompt tokens. This is a prompt-level readout
experiment, not a new source-fetching tool, independent judge, or proof of
correctness. It does not change tool permissions or execute member proposals.

## Independent exact-answer evaluation

Canonical labels must be supplied separately by a trusted adjudicator or a
deterministic closed-answer test. We do not infer correctness from prose,
agreement, model confidence, or an LLM grading its own answer. These files
contain private responses; review before committing or sharing.

For every trace's `example_id`, supply one reference row:

```json
{
  "example_id": "matching-trace-id",
  "gold": "A",
  "reference_source": "locked-reviewed-benchmark/case-001",
  "round_answers": {
    "0": {"model-1": "B", "model-2": "B", "model-3": "A"},
    "1": {"model-1": "B", "model-2": "A", "model-3": "A"}
  },
  "final_answer": "A"
}
```

Use actual trace model IDs. A label is the canonical answer adjudicated from
that response, not what the reference wants the model to say. Explicit `null`
represents abstention/unparseable output; errors must also have null answers.
Matching is exact and case-sensitive, with no semantic equivalence claims.
Supply every candidate and every recorded round: incomplete labels, unknown
rounds, duplicates, and silently omitted cases are rejected. The scorer reads
references; it does not rewrite them, generate answers, or change live policy.
The separate reference file is a workflow boundary, not an OS-enforced learner
write restriction; that isolation belongs to the later promotion-gate work.

```sh
python -m scripts.council_readout \
  --traces traces.jsonl --references locked-labels.jsonl --output readout-results.json
```

This reports upstream metrics:

| Metric | Meaning |
| --- | --- |
| `proposal_hit` | Fraction with a correct independent opening proposal |
| `initial_vote_accuracy` | Accuracy of the unique most-common opening answer |
| `recoverable_headroom` | Correct opening proposal exists but opening vote is wrong/tied |
| `recovery_rate` | Correct final answers conditional on that headroom |
| `new_correctness_mass` | Correct final answer despite no correct opening proposal |
| `damage_rate` | Incorrect final answers conditional on a correct opening vote |
| `gain` | Final accuracy minus opening vote accuracy |

The upstream `majority_answer` function uses a unique plurality: it does not
require more than half of valid votes. Ties return null; failed or abstaining
members cast no valid vote. The original member count remains visible.
No eligible cases means a conditional rate is null, not measured zero.
The decomposition `gain = recovery_mass + new_correctness_mass - damage_mass`
must hold. Apex adds count aliases `headroom_recovered` and `vote_correct_damage`.
Scores with loose booleans or inconsistent labels are rejected before reaching
the upstream metric module.

## Evidence and limits

Tests exercise real vendored metrics, minority recovery, damaged correct votes,
ties, all-abstention cases, revisions, provider failures, matched call counts,
unscored live traces, invalid labels, and the CLI file interface.

This integration does not establish a live accuracy improvement, implement
neural-thicket team selection, add a trained verifier, or reproduce the
paper-scale GPU experiments. The next evidence needed is a frozen set of real
Apex tasks with independently checked labels, matched budgets, and separate
standard/verification-mode results. Automatic adoption based on such results is
future work. No correctness score is fabricated for ordinary conversation.
