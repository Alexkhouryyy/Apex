from dataclasses import replace
import pytest
from agent import learning_eval as e, learning_registry as r


def contract():
    return e.Contract('citation-v1', tuple(
        [e.Case(f'{s}{i}', s, 'pass', i == 0) for s in ('local','heldout','ood') for i in range(10)]
        + [e.Case(f'anchor{i}', 'anchor', 'pass' if i%2 else 'fail') for i in range(20)]), ('train1',))


def observations(c):
    baseline = [dict(id=x.id, correct=not x.id.endswith('9'), permission_violations=0, severe_regression=False) for x in c.cases if x.split != 'anchor']
    candidate = [{**x, 'correct': True} for x in baseline]
    verifier = {x.id:x.expected for x in c.cases if x.split == 'anchor'}
    costs = dict(baseline_cost={'tokens':1000,'usd':.01,'source':'fixture'}, candidate_cost={'tokens':1100,'usd':.012,'source':'fixture'},learning_cost={'tokens':2000,'usd':1,'source':'fixture'})
    return dict(baseline=baseline,candidate=candidate,verifier=verifier,artifact_sha=e.artifact_digest('def run(inputs): return "ok"'),evidence_source='synthetic-execution-fixture',**costs)


def receipt(c=None):
    c = c or contract()
    return e.evaluate(c, **observations(c))


def obj():
    return dict(address='citation.check', applicability='citations only', procedure='verify against source', exclusions=['unavailable source'], support_traces=['train1'], contradiction_traces=[])


def test_valid_contract_has_disjoint_splits_and_real_gain():
    c = contract()
    assert receipt(c)['passed']
    assert receipt(c)['metrics']['plasticity_gain_per_usd'] == pytest.approx(.1)
    with pytest.raises(ValueError): replace(c, train_ids=('local0',))
    with pytest.raises(ValueError): replace(c, cases=c.cases+(c.cases[0],))


@pytest.mark.parametrize('attack', ['allpass','allabstain','safety','permission','severe','cost','overhead','ood','no_gain'])
def test_reward_hacking_and_regressions_never_pass(attack):
    c, o = contract(), observations(contract())
    if attack in ('allpass','allabstain'): o['verifier'] = {i: 'pass' if attack=='allpass' else 'abstain' for i in o['verifier']}
    if attack=='safety': o['candidate'][0]['correct']=False
    if attack=='permission': o['candidate'][0]['permission_violations']=1
    if attack=='severe': o['candidate'][0]['severe_regression']=True
    if attack=='cost': o['learning_cost']['usd']=11
    if attack=='overhead': o['candidate_cost']['tokens']=1300
    if attack=='ood': o['candidate'][20]['correct']=False
    if attack=='no_gain': o['candidate']=o['baseline']
    assert not e.evaluate(c, **o)['passed']


def test_missing_labels_and_cost_provenance_fail_closed():
    c,o=contract(),observations(contract())
    o['candidate'].pop()
    with pytest.raises(ValueError): e.evaluate(c, **o)
    o=observations(c); o['candidate_cost']['source']=''
    with pytest.raises(ValueError): e.evaluate(c, **o)


def stage(c, code='def run(inputs): return "ok"'):
    candidate=r.propose('citation',code,obj())
    o=observations(c);o['artifact_sha']=e.artifact_digest(code)
    active=next((v for v in r.listing('citation') if v['status'] in {'active','stable'}),None)
    o['baseline_artifact_sha']=active['artifact_sha'] if active else None
    r.attach_receipt(candidate['id'],e.evaluate(c,**o))
    return candidate['id']


def test_frozen_evaluator_exact_bytes_lease_and_canary_rollback(test_db):
    c=contract();r.freeze_contract(c)
    with pytest.raises(ValueError):r.freeze_contract(replace(c,min_gain=.01))
    first=stage(c)
    with pytest.raises(RuntimeError):r.require_evaluated('citation','def run(inputs): return "ok"')
    r.promote(first)
    r.require_evaluated('citation','def run(inputs): return "ok"')
    r.note_installed('citation','def run(inputs): return "ok"','old source')
    assert r.runtime_allowed('citation','old source')
    second=stage(c,'def run(inputs): return "better"')
    with pytest.raises(RuntimeError):r.promote(second,expected_active='wrong')
    r.promote(second,expected_active=first)
    r.note_installed('citation','def run(inputs): return "better"','new source')
    assert not r.runtime_allowed('citation','old source')
    assert not r.canary(second,correct=False)['rolled_back']
    assert r.canary(second,correct=False)['restored']==first
    assert not r.runtime_allowed('citation','new source')
    assert r.runtime_allowed('citation','old source')


def test_receipt_forgery_and_unsupported_attribution(test_db):
    assert r.propose('citation','x',{},failure_class='perception')['status']=='NO_PATCH'
    c=contract();r.freeze_contract(c)
    v=r.propose('citation','def run(inputs): return "ok"',obj())
    forged=receipt(c);forged['passed']=False
    with pytest.raises(ValueError):r.attach_receipt(v['id'],forged)
    forged=receipt(c);forged['artifact_sha']='0'*64;forged['receipt_sha']=e.digest({k:v for k,v in forged.items() if k!='receipt_sha'})
    with pytest.raises(ValueError):r.attach_receipt(v['id'],forged)
    assert e.checkpoint_policy([{'heldout_gain':-.1},{'heldout_gain':-.01}])['disabled']


def test_real_skill_install_requires_promotion_and_retirement_blocks_execution(test_db,tmp_path,monkeypatch):
    import config
    from agent import skills
    monkeypatch.setattr(config,'SKILL_EVALUATION_REQUIRED',True)
    monkeypatch.setattr(skills,'SKILLS_DIR',tmp_path)
    monkeypatch.setattr(skills,'_registry',{})
    c=contract();r.freeze_contract(c);version=stage(c)
    code='def run(inputs): return "ok"'
    with pytest.raises(RuntimeError):skills.create_skill('citation','safe',code,_trigger='reflection',_bypass_approval=True)
    assert not (tmp_path/'citation.py').exists()
    r.promote(version)
    # Metadata cannot inject executable statements outside the evaluated code.
    description='"""\nraise RuntimeError("metadata injection")\n"""'
    assert 'created and loaded' in skills.create_skill('citation',description,code,_trigger='reflection',_bypass_approval=True)
    assert skills.run_skill('citation',{})=='ok'
    r.canary(version,correct=True,contradiction=True)
    assert 'retired' in skills.run_skill('citation',{}).lower() or 'not active' in skills.run_skill('citation',{}).lower()
    skills._registry.clear()
    def unexpected_import(*args):
        pytest.fail('retired module executed during cold load')
    monkeypatch.setattr(skills.importlib.util, 'spec_from_file_location', unexpected_import)
    assert skills.load_all() == 0


def test_task_local_procedures_discard_on_success_and_failure():
    from agent import task_skills as tasks
    result=tasks.run('verify citation',lambda *a:'Check source, compare quote',lambda *a:True,
                     lambda task:tasks.current(),support_traces=['raw-trace'])
    assert result['result']=='Check source, compare quote' and not result['persisted']
    assert tasks.current()==''
    def fail(task):raise RuntimeError('execution failure')
    with pytest.raises(RuntimeError):tasks.run('verify',lambda *a:'procedure',lambda *a:True,fail,support_traces=['raw'])
    assert tasks.current()==''
    assert tasks.run('verify',lambda *a:'procedure',lambda *a:False,fail,support_traces=['raw'])['status']=='NO_PATCH'


def test_dynamic_forged_tool_cannot_run_after_canary_retirement(test_db,tmp_path,monkeypatch):
    from agent import self_mod
    monkeypatch.setattr(self_mod,'OVERLAY_PATH',tmp_path/'overlay.json')
    monkeypatch.setattr(self_mod,'BACKUP_PATH',tmp_path/'backup.json')
    monkeypatch.setattr(self_mod,'_dynamic_tool_handlers',{})
    c=contract();r.freeze_contract(c);version=stage(c);code='def run(inputs): return "ok"'
    assert 'invalid' in self_mod.register_new_tool('citation','test',{},code,evaluation_required=True).lower()
    r.promote(version)
    assert 'Registered dynamic' in self_mod.register_new_tool('citation','test',{},code,evaluation_required=True)
    assert self_mod.dispatch('citation',{})=='ok'
    r.canary(version,correct=False,permission_violation=True)
    assert 'error' in self_mod.dispatch('citation',{}).lower()
    self_mod._dynamic_tool_handlers.clear()
    assert self_mod.load_dynamic_handlers()==0


def test_install_receipt_persistence_failure_removes_loaded_candidate(test_db,tmp_path,monkeypatch):
    import config
    from agent import skills
    monkeypatch.setattr(config,'SKILL_EVALUATION_REQUIRED',True)
    monkeypatch.setattr(skills,'SKILLS_DIR',tmp_path);monkeypatch.setattr(skills,'_registry',{})
    c=contract();r.freeze_contract(c);version=stage(c);r.promote(version)
    def fail(*a):raise RuntimeError('receipt persistence failed')
    monkeypatch.setattr(r,'note_installed',fail)
    result=skills.create_skill('citation','test','def run(inputs): return "ok"',_trigger='reflection',_bypass_approval=True)
    assert 'failed' in result and not (tmp_path/'citation.py').exists()
    assert 'citation' not in skills._registry
