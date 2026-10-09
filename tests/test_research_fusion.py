from dataclasses import replace
from agent.memory_governance import Context,Unit
from agent.memory_fusion import select


def unit(i,**kw):return Unit(**(dict(id=i,text='source citation',subject='owner',domain='work',purposes=('answer',),source='input',provenance=('input',),created_at=1,approved=True)|kw))


def test_temporal_bounds_use_event_time_and_cannot_override_scope():
    units=[unit('old',occurred_at=5),unit('new',occurred_at=15),unit('unknown'),unit('foreign',subject='other',occurred_at=15)]
    result=select(units,Context('owner','work','answer',20),'citation',semantic_scores={'foreign':999,'old':100,'new':1},occurred_after=10)
    assert [u.id for u in result['units']]==['new']
    assert result['rejected']['foreign']=='subject' and result['rejected']['unknown']=='unknown_event_time'
    assert len(result['snapshot_sha'])==64


def test_semantic_fusion_preserves_raw_sources_and_snapshot_binds_metadata():
    units=[unit('a',text='lexical citation'),unit('b',text='semantic evidence')]
    ctx=Context('owner','work','answer',20)
    result=select(units,ctx,'citation',semantic_scores={'b':1},limit=2)
    assert {u.id for u in result['units']}=={'a','b'}
    changed=select([replace(units[0],approved=False),units[1]],ctx,'citation',semantic_scores={'b':1})
    assert result['snapshot_sha']!=changed['snapshot_sha']
