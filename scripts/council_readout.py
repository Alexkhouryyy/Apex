"""Offline Council evaluation. Never calls a model or changes a live policy.

python -m scripts.council_readout --traces traces.jsonl --references labels.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.council_readout import score_trace, summarize


def _load(path):
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def evaluate_files(traces_path, references_path):
    traces, references = _load(traces_path), _load(references_path)
    if not all(isinstance(item, dict) for item in traces + references):
        raise ValueError("Each JSONL row must be an object.")
    reference_ids = [r.get("example_id") for r in references]
    trace_ids = [r.get("example_id") for r in traces]
    if (any(not isinstance(i, str) or not i for i in reference_ids + trace_ids)
            or len(set(reference_ids)) != len(reference_ids)
            or len(set(trace_ids)) != len(trace_ids)):
        raise ValueError("Trace and reference IDs must be unique nonempty strings.")
    if set(reference_ids) != set(trace_ids):
        raise ValueError("Every trace requires exactly one reference; no cases may be silently dropped.")
    by_id = {r["example_id"]: r for r in references}
    scored = [score_trace(trace, by_id[trace["example_id"]]) for trace in traces]
    return {"metrics": summarize(scored), "records": scored,
            "evaluation": "independent-exact-answer-labels"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", required=True)
    parser.add_argument("--references", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.output and Path(args.output).resolve() in {
            Path(args.traces).resolve(), Path(args.references).resolve()
        }:
            raise ValueError("Output must not overwrite traces or locked references.")
        result = evaluate_files(args.traces, args.references)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            dest = Path(args.output)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
