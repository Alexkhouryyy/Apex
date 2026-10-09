import json
from pathlib import Path
from agent.research_experiments import assess


def test_all_brief_experiments_have_fixed_thresholds_and_missing_data_cannot_pass():
    contracts=json.loads(Path('docs/research/experiment-contracts.json').read_text())['experiments']
    assert len(contracts)==18 and len({c['id'] for c in contracts})==18
    for contract in contracts:
        result=assess(contract,{'kind':'synthetic-engineering','source':'unit fixture','metrics':{}})
        assert result['status']=='not_ready' and not result['authorization']
        assert result['reasons']


def test_zero_permission_violations_remain_a_hard_check_even_when_gain_is_high():
    c={'id':'example','evidence_kind':'apex-replay','checks':{'gain':['>=',.05],'permission_violations':['==',0]}}
    evidence={'kind':'apex-replay','source':'independent execution','metrics':{'gain':1,'permission_violations':1}}
    assert assess(c,evidence)['reasons']==['failed:permission_violations']
