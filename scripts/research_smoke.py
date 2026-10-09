"""Deterministic synthetic policy benchmark. Never an Apex task-accuracy claim."""
from dataclasses import replace
import argparse
import json
import statistics
import time
from pathlib import Path

from agent import memory_governance as m, learning_eval as e, incident_replay as ir


def run(output, incidents=None):
    base=m.Unit('safe','supported citation evidence','owner','work',('answer',),'synthetic-input',('raw1',),10,approved=True)
    ctx=m.Context('owner','work','answer',20,task_signature='citation',support={'source':'new'})
    attacks=[('subject',{'subject':'foreign'}),('domain',{'domain':'private'}),('purpose',{'purposes':('different',)}),
             ('permission',{'approved':False}),('inferred',{'inferred':True}),('revoked',{'revoked':True}),
             ('stale',{'expires_at':15}),('future',{'created_at':30}),
             ('task',{'kind':'procedure','task_signature':'other'}),
             ('support',{'kind':'outcome','task_signature':'citation','support':{'source':'old'}})]
    timings=[];rejected=0;beneficial=0
    for i in range(200):
        name,changes=attacks[i%len(attacks)]
        units=[base,replace(base,id=f'attack-{i}',**changes)]
        start=time.perf_counter_ns();kept,reasons=m.admit(units,ctx);timings.append((time.perf_counter_ns()-start)/1e6)
        rejected+=int(f'attack-{i}' in reasons);beneficial+=int([u.id for u in kept]==['safe'])
    replay_count=0
    if incidents:
        incidents.mkdir(parents=True,exist_ok=True)
        for name,changes in attacks:
            units=[base,replace(base,id='attack',**changes)]
            fixture=incidents/name
            with ir.record_turn(store=incidents/f'{name}.jsonl',export=fixture):expected=m.admit(units,ctx)
            for _ in range(20):
                with ir.replay(fixture):actual=m.admit(units,ctx)
                assert [u.id for u in actual[0]]==[u.id for u in expected[0]] and actual[1]==expected[1]
                replay_count+=1
            # Re-run only the pure admission policy with an unsafe source mutation.
            with ir.replay(fixture,live_boundaries=('apex.memory_admission',)):
                kept,_=m.admit([replace(base,approved=False)],ctx)
                assert not kept
    cases=tuple([e.Case(f'{s}{i}',s,'pass',i==0) for s in ('local','heldout','ood') for i in range(30)]
                +[e.Case(f'anchor{i}','anchor','pass' if i%2 else 'fail') for i in range(20)])
    contract=e.Contract('synthetic-citation-smoke-v1',cases,tuple(f'train{i}' for i in range(30)))
    baseline=[dict(id=c.id,correct=not c.id.endswith(('8','9')),permission_violations=0,severe_regression=False) for c in cases if c.split!='anchor']
    new=[{**r,'correct':True} for r in baseline]
    verifier={c.id:c.expected for c in cases if c.split=='anchor'}
    cost={'tokens':1000,'usd':.01,'source':'synthetic deterministic fixture; not billable model measurement'}
    observations=dict(baseline=baseline,candidate=new,verifier=verifier,baseline_cost=cost,candidate_cost=cost,
                      learning_cost={**cost,'usd':1},artifact_sha=e.artifact_digest('synthetic candidate'),evidence_source='synthetic fixture')
    valid=e.evaluate(contract,**observations)
    hacks={}
    for attack in ('pass_all','abstain_all','permission','severe','cost'):
        candidate=[dict(r) for r in new];check=dict(verifier);learning_cost=dict(observations['learning_cost'])
        if attack in ('pass_all','abstain_all'):check={k:'pass' if attack=='pass_all' else 'abstain' for k in check}
        if attack=='permission':candidate[0]['permission_violations']=1
        if attack=='severe':candidate[0]['severe_regression']=True
        if attack=='cost':learning_cost['usd']=11
        receipt=e.evaluate(contract,**{**observations,'candidate':candidate,'verifier':check,'learning_cost':learning_cost})
        assert not receipt['passed'];hacks[attack]=receipt['reasons']
    result={'schema':'apex-research-smoke/v1','evidence_type':'synthetic engineering validation',
            'production_promotion_supported':False,'real_model_calls':0,'real_task_accuracy_measured':False,
            'admission':{'cases':200,'inadmissible_units_excluded':rejected,'beneficial_units_retained':beneficial,
                         'p95_ms':statistics.quantiles(timings,n=20)[18]},
            'replay':{'seeded_incidents':len(attacks) if incidents else 0,'repetitions':replay_count,'real_effects':0},
            'learning_gate':{'positive_fixture_passed':valid['passed'],'reward_hacking_rejections':hacks}}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--incidents',type=Path)
    args=parser.parse_args();print(json.dumps(run(args.output,args.incidents),indent=2))


if __name__=='__main__':main()
