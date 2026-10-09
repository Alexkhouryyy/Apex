import pytest
from agent import research_metrics as m


def row(i,correct):
    return dict(id=str(i),reference_source='reviewed execution',initial_correct=correct,communicated_correct=True,no_message_correct=correct,supported_dissent=not correct,dissent_survived=True,unanimous=correct,all_members_wrong=False)


def test_communication_controls_separate_revision_from_peer_gain():
    result=m.communication([row(1,False),row(2,True)])
    assert result['communication_correction_lift']==1
    assert result['preservation']==1 and result['dissent_survival']==1
    assert not result['authorization']
    assert m.communication([row(1,True)])['correction'] is None
    with pytest.raises(ValueError):m.communication([row(1,True),row(1,False)])


def test_voice_correct_tool_names_do_not_hide_wrong_arguments_or_order():
    expected={'reference_source':'reviewed scenario','calls':[{'name':'read','arguments':{'id':1}},{'name':'write','arguments':{'id':1}}]}
    actual={'calls':list(reversed(expected['calls'])),'rule_violations':1,'spoken_conduct_ok':False}
    result=m.voice_trace(expected,actual)
    assert result['tool_selection'] and not result['ordering'] and not result['arguments']
    assert not result['rule_compliance'] and not result['spoken_conduct']


def test_reward_hacking_is_a_screen_with_separate_multiturn_errors():
    result=m.screening([dict(id='single',reference_source='locked label',reward_hacking=True,flagged=True,multi_turn=False),dict(id='multi',reference_source='locked label',reward_hacking=True,flagged=False,multi_turn=True)])
    assert result['single_turn']['recall']==1 and result['multi_turn']['recall']==0
    assert not result['authorization']


def test_paired_missing_cases_and_improvement_with_damage():
    baseline=[dict(id=str(i),reference_source='fixture',correct=i!=9,tokens=100) for i in range(10)]
    candidate=[{**r,'correct':True,'tokens':110} for r in baseline]
    assert m.paired_gate(baseline,candidate,min_gain=.05,min_samples=10)['passed']
    candidate[0]['correct']=False
    assert 'regression' in m.paired_gate(baseline,candidate,min_samples=10)['reasons']
    with pytest.raises(ValueError):m.paired_gate(baseline,candidate[:-1])
