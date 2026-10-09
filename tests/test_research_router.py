import pytest
from agent.memory_router import route,threshold
from agent.memory_governance import Unit


def unit():return Unit('raw','source','owner','work',('answer',),'input',('input',),1)


def test_hgp_threshold_defers_and_never_grants_approval():
    assert threshold()==.9 and threshold(100)==.5
    probabilities={'semantic':.95,'episodic':.1,'procedural':.1,'none':.1}
    result=route(unit(),probabilities,source='frozen classifier')
    assert result['status']=='routed' and not result['unit'].approved and not result['authorization']
    probabilities['episodic']=.95
    assert route(unit(),probabilities,source='frozen classifier')['status']=='defer'
    with pytest.raises(ValueError):threshold(-1)
    with pytest.raises(ValueError):route(unit(),{'semantic':float('nan')},source='classifier')


def test_procedural_routing_requires_task_signature():
    probabilities={'semantic':.1,'episodic':.1,'procedural':.95,'none':.1}
    assert route(unit(),probabilities,source='frozen classifier')['reason']=='missing_type_support'
