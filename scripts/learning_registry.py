"""Operator-only version lifecycle. Evaluation stays in scripts.learning_gate."""
import argparse
import json
from pathlib import Path
from agent import learning_registry as registry, learning_eval


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('list')
    freeze=sub.add_parser('freeze');freeze.add_argument('contract',type=Path)
    propose=sub.add_parser('propose');propose.add_argument('name');propose.add_argument('artifact',type=Path);propose.add_argument('object',type=Path);propose.add_argument('--failure-class',default='procedure')
    promote=sub.add_parser('promote');promote.add_argument('id');promote.add_argument('--expected-active')
    canary=sub.add_parser('canary');canary.add_argument('id');canary.add_argument('--correct',action='store_true');canary.add_argument('--permission-violation',action='store_true');canary.add_argument('--contradiction',action='store_true')
    args=parser.parse_args();registry.init_db()
    if args.action=='list':result=registry.listing()
    elif args.action=='freeze':result={'contract_sha':registry.freeze_contract(learning_eval.Contract.from_dict(json.loads(args.contract.read_text())))}
    elif args.action=='propose':result=registry.propose(args.name,args.artifact.read_text(),json.loads(args.object.read_text()),failure_class=args.failure_class)
    elif args.action=='promote':result=registry.promote(args.id,expected_active=args.expected_active)
    else:result=registry.canary(args.id,correct=args.correct,permission_violation=args.permission_violation,contradiction=args.contradiction)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
