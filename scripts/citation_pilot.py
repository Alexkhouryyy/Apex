"""Execute a frozen synthetic citation pilot. No model generalization claim."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from agent.citation_checks import verify
from agent.learning_eval import Case,Contract,evaluate,artifact_digest


def pilot(output):
    cases=[];baseline=[];candidate=[];verifier={};fixtures=[]
    for split,count in (('local',30),('heldout',30),('ood',20),('anchor',20)):
        for i in range(count):
            ident=f'{split}-{i}';valid=i%2==0
            source=f'Frozen {split} source {i}: threshold is {i+3} units.'
            quote=f'threshold is {i+3 if valid else i+4} units.'
            sources={ident:source}
            expected='pass' if valid else 'fail'
            case=Case(ident,split,expected,safety=valid and i==0);cases.append(case)
            verdict=verify(quote,ident,sources)['verdict']
            fixtures.append(dict(id=ident,split=split,sources=sources,quote=quote,source_id=ident,expected=expected))
            if split=='anchor':verifier[ident]=verdict;continue
            # A deliberately weak first-word check demonstrates the gate seam,
            # rather than representing the current Apex research implementation.
            weak='pass' if quote.split()[0] in source else 'fail'
            common=dict(id=ident,permission_violations=0,severe_regression=False)
            baseline.append(dict(**common,correct=weak==expected));candidate.append(dict(**common,correct=verdict==expected))
    contract=Contract('literal-citation-pilot/v1',tuple(cases),tuple(f'train-{i}' for i in range(30)))
    source_path=Path(__file__).resolve().parents[1]/'agent/citation_checks.py'
    cost=dict(tokens=0,usd=0,source='deterministic Python execution; no inference or learning spend')
    receipt=evaluate(contract,baseline,candidate,verifier,baseline_cost=cost,candidate_cost=cost,learning_cost=cost,
                     artifact_sha=artifact_digest(source_path.read_text()),evidence_source='synthetic exact-quote execution')
    result={'schema':'apex-citation-pilot/v1','evidence_type':'synthetic deterministic execution',
            'baseline':'deliberately weak first-word fixture; not current Apex',
            'real_model_calls':0,'production_promotion_supported':False,'contract':asdict(contract),'receipt':receipt}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();result=pilot(args.output);print('Synthetic citation gate passed:',result['receipt']['passed'])


if __name__=='__main__':main()
