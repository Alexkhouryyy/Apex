# Engram: isolated memory-selection replay

Apex can now compare **raw conversation turns** and **previously extracted
memories**, each under cosine selection and recorded relevance reranking, at
300, 1,000 and 4,000 units of context budget. This is an offline research
harness, following the October 2 brief's recommendation. It does not change
`agent.longterm.recall`, extract new facts, access the memory DB, send private
turns to a service, or enable a live Jev dependency.

## Verified source and actual imported code

The brief names `Edward-Sun-Official/Engram` and arXiv `2610.00650`. That repository
returned 404 during this integration. The matching study is **When Does Selection
Replace Extraction? A Pre-Registered Test of Agent Memory with a Typed Decision
Model**, by Rishabh Sharma and Rishika Lall, at
<https://github.com/ris3abh/Engram> and <https://arxiv.org/abs/2609.34227>.
Its README reproduces the brief's 3,061× write-cost and 17.4/9.1-point figures.
These are upstream study results, not measured improvements in Apex.

Pinned revision: `2080752aaabb38b56a28f2e68429cfde18b6739d`.
`agent/_vendor/engram/embed.py` contains the upstream `HashEmbedder` and `top_k`
definitions verbatim. MIT LICENSE and NOTICE are adjacent. The harness invokes
upstream `top_k`; it also supports externally produced normalized embeddings.
Hash embeddings are explicitly offline development fixtures, unsuitable for
measuring semantic retrieval quality. The original heavyweight package requires
Python 3.12 and includes model/API runtimes; Apex's normal installation remains
independent of it. No upstream datasets, benchmark caches or prompts are copied.

The harness adapts the registered raw-turn ranking rule from
`src/engram/pipeline/retrieve.py`: keep relevance probabilities **strictly above
0.5**, sort by descending probability and text (raw-turn belief is 1). It does
not reproduce graph expansion, relation pull, history chains or extraction.
It compares supplied scores, never fabricates model judgments or calls Jev.

## Input and replay

Run the committed synthetic smoke case:

```bash
python -m scripts.memory_selection \
  --cases docs/research/fixtures/memory-selection-smoke.jsonl \
  --output /tmp/memory-selection-report.json
```

Each JSONL case contains:

- `id`, `query`, `scope`, `as_of` (timezone-aware timestamp), `answerable`
  (boolean), and `label_source` identifying external evidence adjudication.
- `embedding`: a model/revision identifier; `query_embedding`: its normalized
  vector. Each unit has a normalized `embedding` from the same model over the
  exact rendered text. Alternatively, explicitly use
  `engram-hash-256/offline-only` without vectors for plumbing tests.
- Both `raw` and `extracted`: `units`, `gold_ids`, `scores`. Every unit needs
  unique `id`, original `text`, `speaker`, `at`, `scope`, explicit `revoked`.
  Preserve the source date and speaker on extracted items too.
- `scores`: `source`, `model`, `snapshot_id`, `probabilities` mapping exactly
  the eligible shortlist's IDs to finite relevance probabilities in [0,1].
  Synthetic scores must be identified as synthetic. Real experiments should
  record the scorer version, run artifact and original judgments externally.

Generate the manifest for external scoring with the Python API:

```python
from agent.memory_selection import shortlist, snapshot_id, render
candidates, eligible = shortlist(case, "raw")  # default top 30
fingerprint = snapshot_id(case, "raw", candidates)
texts = [render(unit) for unit in candidates]
# Supply externally obtained scores with this fingerprint; repeat for extracted.
```

Scope, revocation and `as_of` filtering occur **before** embedding and scoring.
The snapshot binds query, scope, cutoff, candidate content, vectors and model
provenance. Changed inputs, missing scores or scores for excluded items fail.
This is a consistency check, not cryptographic proof of scorer authenticity or
an authorization mechanism. Callers must supply authorized, accurately marked
data; Apex's current raw log has no owner/revocation fields suitable for automatic
export. Do not reconstruct forgotten facts from historical logs.

## Measurement limits and promotion gate

Budget accounting includes dates, speaker and newline. It uses **UTF-8 bytes** as
a conservative upper bound for byte-BPE tokens; 300 bytes is tighter than a
300-token budget. Reports name this method and `used_bytes`. These are not the
paper's tokenizer-matched budgets. Whole items that do not fit are skipped;
there is no clipping, synthesis or background extraction.

Results include selected IDs, evidence recall against externally marked IDs,
and presence of selected context on unanswerable questions. The latter is a
retrieval proxy, **not correct abstention by an answer model**. No answer model,
answer accuracy, paired statistical significance, latency comparisons, write
cost comparisons or non-inferiority claims are measured. The synthetic case
only verifies plumbing. Real score production and answer grading remain work.

Before live integration, use held-out, authorized Apex examples with semantic
embeddings, temporal updates, revoked facts and unanswerable questions; compare
both representations with the same tokenizer, answer model and externally
adjudicated labels, and track read/write costs and abstention separately.
Raw events remain canonical; extracted representations must retain provenance.
No benchmark result automatically changes a production policy.
