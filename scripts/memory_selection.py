"""Run an offline memory-selection replay: python -m scripts.memory_selection."""
import argparse
import json
from pathlib import Path

from agent.memory_selection import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.cases.resolve() == args.output.resolve():
        parser.error("output must not overwrite the input")
    cases = [json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()]
    report = evaluate(cases)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
