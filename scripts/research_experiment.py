"""Assess independently measured evidence against a fixed research experiment."""
import argparse
import json
from pathlib import Path
from agent.research_experiments import assess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('id');p.add_argument('--contracts',type=Path,required=True)
    p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.resolve() in {args.contracts.resolve(),args.evidence.resolve()}:p.error('output must preserve inputs')
    contracts=json.loads(args.contracts.read_text())['experiments']
    contract=next((c for c in contracts if c['id']==args.id),None)
    if not contract:p.error('unknown experiment')
    result=assess(contract,json.loads(args.evidence.read_text()))
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(result['status'])


if __name__=='__main__':main()
