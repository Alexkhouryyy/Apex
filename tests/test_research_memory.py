from dataclasses import replace
import pytest
from agent import memory_governance as m


def unit(ident='raw', **kw):
    return m.Unit(**dict(id=ident,text='citation source fact',subject='owner',domain='work',purposes=('answer',),source='owner-input',provenance=('input1',),created_at=10,approved=True)|kw)


def context(**kw):
    return m.Context(**dict(subject='owner',domain='work',purpose='answer',now=20)|kw)


@pytest.mark.parametrize('changes,reason', [({'subject':'other'},'subject'),({'domain':'private'},'domain'),({'purposes':('other',)},'purpose'),({'approved':False},'permission'),({'inferred':True},'inferred'),({'revoked':True},'revoked'),({'created_at':30},'freshness'),({'expires_at':15},'freshness'),({'kind':'procedure','task_signature':'different'},'task_signature')])
def test_policy_is_physical_exclusion(changes,reason):
    u=replace(unit(),**changes)
    admitted,rejected=m.admit([u],context())
    assert admitted==[] and rejected=={'raw':reason}
    assert u.text not in m.render(admitted)


def test_memtrim_changed_support_conflicts_and_duplicate_evidence():
    ctx=context(task_signature='citation',support={'url':'new'})
    changed=unit('outcome',kind='outcome',task_signature='citation',support={'url':'old'})
    a=unit('a',fact_key='version',fact_value='1')
    b=unit('b',fact_key='version',fact_value='2')
    dup=unit('duplicate')
    kept,rejected=m.admit([changed,a,b,unit(),dup],ctx)
    assert len(kept)==1
    assert rejected['outcome']=='changed_support'
    assert rejected['a']==rejected['b']=='conflicting_memory'
    assert 'duplicate_evidence' in rejected.values()


def test_forget_purges_transitive_views_without_cross_subject_delete(test_db):
    raw=unit();derived=unit('derived',provenance=('raw',));nested=unit('nested',provenance=('derived',))
    other=replace(unit('other'),subject='someone')
    for u in (raw,derived,nested,other):m.store(u)
    assert m.forget('raw','someone')['deleted']==[]
    assert m.forget('raw','owner')['deleted']==['derived','nested','raw']
    assert m.inspect('owner')==[] and len(m.inspect('someone'))==1
    with pytest.raises(ValueError):m.store(raw)


def test_actual_memagent_interface_and_scope_first_portfolio(test_db):
    from agent._vendor.memagent.base_memory import BaseMemoryProvider
    m.store(unit());m.store(unit('procedure',kind='procedure',task_signature='citation'))
    m.store(replace(unit('revoked'),revoked=True))
    provider=m.ScopedProvider('verified',{'fact'},context())
    assert isinstance(provider,BaseMemoryProvider)
    assert provider.initialize()
    assert provider.probe('citation')['ids']==['raw']
    assert m.recall('citation',context())['providers']==['verified']
    result=m.recall('citation',context(task_signature='citation'))
    assert {u['id'] for u in result['memories']}=={'raw','procedure'}
    with m.use(context()): assert m.current().subject=='owner'
    assert m.current() is None


def test_governed_dispatch_reads_scoped_store_and_cannot_write_legacy(test_db,monkeypatch):
    from agent import core
    m.store(unit())
    monkeypatch.setattr(core.longterm,'remember',lambda *a,**kw:pytest.fail('legacy write escaped scope'))
    monkeypatch.setattr(core.longterm,'forget',lambda *a,**kw:pytest.fail('legacy delete escaped scope'))
    with m.use(context()):
        assert 'owner approval' in core._execute_tool_inner('remember',{'content':'new fact'})
        assert 'Approved evidence' in core._execute_tool_inner('forget',{'memory_id':1})
        assert 'raw' in core._execute_tool_inner('recall',{'query':'citation'})
    assert m.current() is None


def test_scope_metadata_requires_sequences_and_copies_current_support():
    with pytest.raises(ValueError):replace(unit(),purposes='answer')
    with pytest.raises(ValueError):replace(unit(),provenance='source')
    support={'version':'1'};ctx=context(support=support);support['version']='2'
    assert ctx.support['version']=='1'
    assert replace(unit(),purposes=['answer'])==unit()
def test_governed_summary_cannot_auto_approve_legacy_facts(monkeypatch):
    from types import SimpleNamespace
    from agent import memory as conversation
    import config
    monkeypatch.setattr(config, 'REVERSIBLE_CONTEXT_ENABLED', False)
    monkeypatch.setattr(conversation.longterm, 'remember', lambda *a, **kw: pytest.fail('unapproved summary fact persisted'))
    response = SimpleNamespace(content=[SimpleNamespace(text='{"summary":"short","save_to_memory":["inferred preference"]}')])
    monkeypatch.setattr(conversation.telemetry, 'create', lambda *a, **kw: response)
    history = conversation.Memory()
    for index in range(40):
        history.add_user(f'turn {index}')
    with m.use(m.Context('owner', 'work', 'answer', 20)):
        history.maybe_summarize(None)
    assert history.summary == 'short'
