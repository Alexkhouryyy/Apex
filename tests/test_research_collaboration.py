import pytest
from agent import dependency_plan as d, council_selection as c


def nodes():
    return [dict(id='a',role='researcher',task='a',depends_on=[]),dict(id='b',role='researcher',task='b',depends_on=[]),dict(id='c',role='researcher',task='merge',depends_on=['a','b'])]


def test_low_confidence_and_dense_work_stay_with_parent():
    assert d.plan(nodes(),confidence=.2)['mode']=='single'
    assert d.execute(d.plan(nodes(),confidence=.2),lambda *_: pytest.fail('must not spawn'))['status']=='parent_execution_required'
    cyclic=nodes();cyclic[0]['depends_on']=['c']
    with pytest.raises(ValueError):d.plan(cyclic,confidence=1)


def test_parallel_declared_context_and_failed_predecessor():
    # Four nodes make two independent roots sparse enough for parallel execution.
    n=nodes()+[dict(id='d',role='researcher',task='d',depends_on=[])]
    seen={}
    def run(node, artifacts):
        seen[node['id']]=artifacts
        return node['id']
    result=d.execute(d.plan(n,confidence=.9),run)
    assert result['results']['c']['status']=='done'
    assert seen['c']=={'a':'a','b':'b'} and seen['b']=={}
    def fail(node,artifacts):
        if node['id']=='a':raise ValueError('poisoned predecessor')
        return node['id']
    assert d.execute(d.plan(n,confidence=.9),fail)['results']['c']['status']=='blocked'


def test_profiles_use_heldout_error_complementarity_and_cost_caps():
    labels={'a':[True,True,False,False],'b':[True,True,False,False],'c':[False,False,True,True]}
    profile={'domains':{'general':{'source':'external execution','heldout_ids':['1','2','3','4'],'train_ids':['train'],'models':{m:{'correct':dict(zip(['1','2','3','4'],v)),'tokens_per_query':100} for m,v in labels.items()}}}}
    choice=c.select(profile,list(labels),size=2)
    assert 'c' in choice['models'] and choice['all_wrong_rate']==0
    assert c.select(profile,list(labels),size=2,token_cap=100)['models']==[]
    profile['domains']['general']['train_ids']=['1']
    with pytest.raises(ValueError):c.select(profile,list(labels),size=2)


def test_governed_scope_reaches_dag_workers_across_both_thread_layers(monkeypatch):
    from agent import orchestrator, memory_governance as memory
    monkeypatch.setattr(orchestrator, '_subagents', {})
    observed = []
    class Worker:
        def run(self, *args, **kwargs):
            observed.append(memory.current())
            return 'bounded artifact'
    monkeypatch.setattr(orchestrator, '_agent_factory', Worker)
    ctx = memory.Context('owner', 'work', 'answer', 20)
    with memory.use(ctx):
        result = orchestrator.run_plan(nodes() + [dict(id='d',role='researcher',task='d',depends_on=[])], confidence=.9)
    assert len(observed) == 4 and all(value == ctx for value in observed)
    assert all(value['status'] == 'done' for value in result['results'].values())
    assert memory.current() is None
