"""Read independently labeled JSON evidence without invoking a model or detector."""
import argparse
import json
from pathlib import Path
from agent import research_metrics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=('communication','screening','voice','paired'))
    parser.add_argument('input',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.input.resolve()==args.output.resolve():parser.error('output must not overwrite references')
    data=json.loads(args.input.read_text())
    if args.kind=='voice':result=research_metrics.voice_trace(data['expected'],data['actual'])
    elif args.kind=='paired':result=research_metrics.paired_gate(**data)
    else:result=getattr(research_metrics,args.kind)(data)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
