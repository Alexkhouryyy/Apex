"""Operator evaluation replay; references and metrics never enter learner prompts."""
import argparse
import json
from pathlib import Path

from agent import learning_eval, learning_registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-id", help="Attach the receipt to a staged candidate; operator use only")
    args = parser.parse_args()
    if args.output.resolve() in {p.resolve() for p in (args.contract, args.observations, args.artifact)}:
        parser.error("output must not overwrite evaluation inputs")
    contract = learning_eval.Contract.from_dict(json.loads(args.contract.read_text()))
    observations = json.loads(args.observations.read_text())
    receipt = learning_eval.evaluate(contract, artifact_sha=learning_eval.artifact_digest(args.artifact.read_text()), **observations)
    if args.candidate_id:
        learning_registry.freeze_contract(contract)
        learning_registry.attach_receipt(args.candidate_id, receipt)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS" if receipt["passed"] else "FAIL: " + ", ".join(receipt["reasons"]))


if __name__ == "__main__":
    main()
