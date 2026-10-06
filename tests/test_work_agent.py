"""The always-on Work agent (agent/work_agent.py) and your Claude / ChatGPT
plans as the ones doing the work (agent/work_engines.py). The real `claude`
and `codex` tools are replaced by small fake programs on PATH."""
import json
import os
import stat
import sys
import time
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import work, work_agent, work_engines

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='fake CLIs are POSIX scripts')

FAKE = r'''#!{python}
import json, os, sys, pathlib
mode = pathlib.Path(os.environ['FAKE_DIR'], '{name}.mode').read_text().strip()
pathlib.Path(os.environ['FAKE_DIR'], '{name}.args').write_text(json.dumps({{
    'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin': sys.stdin.read(),
    'keys': [k for k in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY') if k in os.environ]}}))
if mode == 'slow':
    import time; time.sleep(5)
if '{name}' == 'claude':
    if mode == 'done':
        pathlib.Path('result.md').write_text('# Draft')
        print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': False,
                          'result': 'Wrote result.md. Check the rate limit section of the brief.'}}))
    elif mode == 'limited':
        print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': True,
                          'result': 'Claude AI usage limit reached|1760000000'}}))
    elif mode == 'signed_out':
        print('Invalid API key · Please run /login', file=sys.stderr); sys.exit(1)
    else:
        print('Something broke', file=sys.stderr); sys.exit(2)
else:
    out = sys.argv[sys.argv.index('-o') + 1]
    if mode == 'done':
        pathlib.Path('result.md').write_text('# Draft')
        pathlib.Path(out).write_text('Drafted result.md for you to check.')
    elif mode == 'limited':
        print("You've hit your usage limit. Try again in 3 hours.", file=sys.stderr); sys.exit(1)
    else:
        print('Not logged in. Please sign in.', file=sys.stderr); sys.exit(1)
'''


@pytest.fixture
def fake(tmp_path, monkeypatch, test_db):
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    for name in ('claude', 'codex'):
        p = bin_dir / name
        p.write_text(FAKE.format(python=sys.executable, name=name))
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        (bin_dir / f'{name}.mode').write_text('done')
    monkeypatch.setenv('FAKE_DIR', str(bin_dir))
    monkeypatch.setenv('PATH', f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-should-not-reach-the-plan')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-should-not-reach-the-plan')
    monkeypatch.setattr(work, 'WORK_DIR', tmp_path / 'ApexWork')
    notes = []
    monkeypatch.setattr(work_agent, '_notify', lambda title, body: notes.append(body))
    work_agent.init_db()

    class Fake:
        dir, sent = bin_dir, notes
        def mode(self, name, mode): (bin_dir / f'{name}.mode').write_text(mode)
        def args(self, name): return json.loads((bin_dir / f'{name}.args').read_text())
        def remove(self, name): (bin_dir / name).unlink()
    return Fake()


def wait_for(task_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        work.sync_apex()
        t = work.get_task(task_id)
        if t['apex_state'] not in ('queued', 'running'):
            return t
        time.sleep(0.05)
    raise AssertionError('the run did not finish')


# ---------------------------------------------------------------- engines

def test_each_plan_runs_in_the_task_folder_without_api_keys(fake, tmp_path):
    for engine, name in (('claude', 'claude'), ('chatgpt', 'codex')):
        folder = tmp_path / f'job-{engine}'
        r = work_engines.run(engine, 'Write the draft & %PATH% "quoted"', folder)
        assert r['status'] == 'done' and (folder / 'result.md').exists()
        seen = fake.args(name)
        assert seen['keys'] == [] and os.path.samefile(seen['cwd'], folder)    # your plan, never API credits
        assert seen['stdin'] == 'Write the draft & %PATH% "quoted"'          # on stdin, never through cmd.exe
        assert not any('draft' in a for a in seen['argv'])
    assert 'Wrote result.md' in work_engines.run('claude', 'x', tmp_path / 'a')['summary']
    claude = fake.args('claude')['argv']
    assert 'Bash' not in claude and claude[claude.index('--permission-mode') + 1] == 'acceptEdits'
    codex = fake.args('codex')['argv']
    assert codex[codex.index('--sandbox') + 1] == 'workspace-write' and not (tmp_path / 'job-chatgpt' / '.apex-last-message.txt').exists()


@pytest.mark.parametrize('engine,name,mode,want', [
    ('claude', 'claude', 'limited', 'limited'), ('chatgpt', 'codex', 'limited', 'limited'),
    ('claude', 'claude', 'signed_out', 'signed_out'), ('chatgpt', 'codex', 'signed_out', 'signed_out'),
    ('claude', 'claude', 'broken', 'failed'),
])
def test_plan_outcomes_are_told_apart(fake, tmp_path, engine, name, mode, want):
    fake.mode(name, mode)
    assert work_engines.run(engine, 'x', tmp_path / 'f')['status'] == want


def test_a_finished_answer_that_mentions_limits_is_still_finished(fake, tmp_path):
    assert work_engines._classify('Done. I noted the API rate limit.', failed=False) == 'done'


def test_missing_tool_and_timeout(fake, tmp_path):
    fake.remove('codex')
    r = work_engines.run('chatgpt', 'x', tmp_path / 'f')
    assert r['status'] == 'missing' and 'npm install -g @openai/codex' in r['summary']
    assert work_engines.installed() == {'claude': True, 'chatgpt': False, 'api': True}
    fake.mode('claude', 'slow')
    r = work_engines.run('claude', 'x', tmp_path / 'g', timeout=1)
    assert r['status'] == 'failed' and 'longer than' in r['summary']


# ---------------------------------------------------------------- handing a task to a plan

def test_a_task_done_on_your_claude_plan_comes_back_for_review(fake):
    t = work.add_task(title='Draft the proposal for Karim')
    given = work.give_to_apex(t['id'], agent=object(), engine='claude')
    assert given['apex_engine'] == 'claude' and given['status'] == 'doing' and given['apex_run'].startswith('cli-claude-')
    back = wait_for(t['id'])
    assert back['status'] == 'review' and back['apex_state'] == 'done' and back['apex_cost'] == 0
    assert work.files_of(back) == ['result.md'] and 'Wrote result.md' in back['apex_summary']


def test_a_plan_at_its_limit_puts_the_task_back_untried_and_rests(fake):
    fake.mode('claude', 'limited')
    t = work.add_task(title='Summarise the lab')
    work.give_to_apex(t['id'], agent=object(), engine='claude')
    back = wait_for(t['id'])
    assert back['apex_state'] is None and back['status'] == 'todo' and 'usage limit' in back['apex_summary']
    why = work_agent.available()['claude']
    assert why.startswith('reached its usage limit, resting until')
    assert work_agent.available()['chatgpt'] is None
    assert any('Claude plan reached its usage limit' in n for n in fake.sent)


def test_restart_mid_run_marks_it_interrupted(fake):
    t = work.add_task(title='x')
    fake.mode('claude', 'slow')
    run_id = work.give_to_apex(t['id'], agent=object(), engine='claude')['apex_run']
    work._live_runs.discard(run_id)                  # as if Apex had restarted
    work.sync_apex()
    back = work.get_task(t['id'])
    assert back['apex_state'] == 'interrupted' and back['status'] == 'todo'


def test_quick_add_marks_tasks_for_apex(fake):
    t = work.add_task(quick='research laptops +apex #studies')
    assert t['title'] == 'research laptops' and t['apex_ok'] == 1 and t['area'] == 'studies'
    assert work.update_task(t['id'], apex_ok=False)['apex_ok'] == 0
    with pytest.raises(work.WorkError):
        work.update_task(t['id'], apex_ok='yes')


# ---------------------------------------------------------------- the always-on loop

NOON = datetime(2026, 10, 6, 12, 0)


def test_off_by_default_and_does_nothing(fake):
    work.add_task(quick='x +apex')
    assert work_agent.settings()['enabled'] is False and work_agent.tick(NOON, agent=object()) == []


def test_brief_and_evening_once_a_day(fake):
    work_agent.update_settings(enabled=True)
    work.add_task(title='pay rent', due='2026-10-06')
    assert 'brief' in work_agent.tick(datetime(2026, 10, 6, 8, 31))
    assert 'brief' not in work_agent.tick(datetime(2026, 10, 6, 9, 0))
    assert 'Due today: pay rent' in fake.sent[0]
    assert 'evening' in work_agent.tick(datetime(2026, 10, 6, 18, 5))
    assert work_agent.tick(datetime(2026, 10, 6, 19, 0)) == []
    assert 'brief' in work_agent.tick(datetime(2026, 10, 7, 8, 30))


def test_waiting_work_gets_a_nudge_every_few_days(fake):
    work_agent.update_settings(enabled=True, brief_time='23:59', evening_time='23:59')
    t = work.add_task(title='quote from supplier', status='waiting')
    work.update_task(t['id'], waiting_on='Ahmad')
    base = work.get_task(t['id'])['updated']
    at = lambda days: datetime.fromtimestamp(base + days * 86400).replace(hour=12)
    assert work_agent.tick(at(1)) == []
    assert any('Still waiting on Ahmad' in x for x in work_agent.tick(at(3.2)))
    assert work_agent.tick(at(4)) == []
    assert any('Still waiting' in x for x in work_agent.tick(at(6.5)))


def test_works_on_its_own_on_your_plans_and_moves_on_at_a_limit(fake):
    work_agent.update_settings(enabled=True, auto_work=True, brief_time='23:59', evening_time='23:59')
    later = work.add_task(quick='later one +apex', due='2026-10-20')
    urgent = work.add_task(quick='urgent one +apex', due='2026-10-07')
    work.add_task(title='not for Apex', due='2026-10-01')
    fake.mode('claude', 'limited')
    did = work_agent.tick(NOON, agent=object())
    assert did == ['Apex picked up "urgent one" on your Claude plan.']
    wait_for(urgent['id'])
    # Claude is resting: the same, untried task goes to the ChatGPT plan.
    did = work_agent.tick(NOON, agent=object())
    assert did == ['Apex picked up "urgent one" on your ChatGPT plan.']
    assert wait_for(urgent['id'])['status'] == 'review'
    finished = work_agent.tick(NOON, agent=object())
    assert any('Apex finished "urgent one"' in x for x in finished)
    assert any('picked up "later one" on your ChatGPT plan' in x for x in finished)
    wait_for(later['id'])


def test_one_task_at_a_time(fake):
    work_agent.update_settings(enabled=True, auto_work=True, brief_time='23:59', evening_time='23:59')
    first = work.add_task(quick='first +apex', due='2026-10-07')
    work.add_task(quick='second +apex', due='2026-10-08')
    fake.mode('claude', 'slow')
    assert work_agent.tick(NOON, agent=object()) == ['Apex picked up "first" on your Claude plan.']
    assert work_agent.tick(NOON, agent=object()) == []            # the first is still running
    assert [t['title'] for t in work.list_tasks() if t['apex_state']] == ['first']
    wait_for(first['id'])


def test_holds_when_no_plan_is_free_and_never_uses_credits_unless_asked(fake, monkeypatch):
    from agent import team
    submitted = []
    monkeypatch.setattr(team, 'submit', lambda body, agent: submitted.append(body))
    work_agent.update_settings(enabled=True, auto_work=True, brief_time='23:59', evening_time='23:59')
    work.add_task(quick='x +apex')
    for engine in ('claude', 'chatgpt'):
        work_agent.mark_unavailable(engine, 'limited', now=NOON.timestamp())
    assert work_agent.tick(NOON, agent=object()) == ['waiting']
    assert work_agent.tick(NOON, agent=object()) == []                  # said once
    assert submitted == []
    assert 'Claude plan reached its usage limit' in work_agent.events()[0]['text']
    work_agent.update_settings(engines=['claude', 'chatgpt', 'api'])
    assert 'API credits' in work_agent.tick(NOON, agent=object())[0] and len(submitted) == 1
    work_agent.clear_limits()
    assert work_agent.available(NOON.timestamp())['claude'] is None


def test_a_daily_limit_on_plan_tasks(fake):
    work_agent.update_settings(enabled=True, auto_work=True, plan_runs=0, brief_time='23:59', evening_time='23:59')
    work.add_task(quick='x +apex')
    now = datetime.now().replace(hour=12)
    assert work_agent.tick(now, agent=object()) == ['waiting']
    assert '0 plan tasks today already' in work_agent.events()[0]['text']


def test_tried_tasks_are_never_retried_on_their_own(fake):
    fake.mode('claude', 'broken')
    work_agent.update_settings(enabled=True, auto_work=True, engines=['claude'], brief_time='23:59', evening_time='23:59')
    t = work.add_task(quick='x +apex')
    assert work_agent.tick(NOON, agent=object())
    assert wait_for(t['id'])['apex_state'] == 'failed'
    did = work_agent.tick(NOON, agent=object())
    assert any('Apex stopped on "x"' in x for x in did) and not any('picked up' in x for x in did)


def test_settings_are_checked(fake):
    for bad in (dict(enabled='yes'), dict(engines=[]), dict(engines=['claude', 'claude']), dict(engines=['gemini']),
                dict(plan_runs=-1), dict(rest_hours=0), dict(brief_time='25:00'), dict(daily_budget=500), dict(shell=True)):
        with pytest.raises(work.WorkError):
            work_agent.update_settings(**bad)
    assert work_agent.update_settings(engines=['chatgpt', 'claude'])['engines'] == ['chatgpt', 'claude']


def test_describe_for_chat_and_voice(fake):
    assert 'off' in work.tool({'action': 'agent'})
    work_agent.update_settings(enabled=True, auto_work=True)
    work_agent.mark_unavailable('claude', 'limited')
    said = work.tool({'action': 'agent'})
    assert 'Claude plan then ChatGPT plan' in said and 'Claude plan reached its usage limit' in said


# ---------------------------------------------------------------- the API

@pytest.fixture
def api(fake, monkeypatch):
    import config
    from dashboard import work as route
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', '')
    app = FastAPI(); app.include_router(route.router)
    return TestClient(app), config


def test_agent_routes(api, monkeypatch):
    client, config = api
    st = client.get('/api/work/agent').json()
    assert st['enabled'] is False and [p['id'] for p in st['plans']] == ['claude', 'chatgpt', 'api']
    assert all(p['unavailable'] is None for p in st['plans'])
    st = client.put('/api/work/agent', json={'enabled': True, 'engines': ['chatgpt']}).json()
    assert st['enabled'] and st['engines'] == ['chatgpt']
    assert client.put('/api/work/agent', json={'engines': ['bard']}).status_code == 400
    assert client.put('/api/work/agent', json={'enabled': True}, headers={'Origin': 'https://evil.example'}).status_code == 403
    work_agent.mark_unavailable('claude', 'limited')
    assert client.put('/api/work/agent', json={'clear_limits': True}).json()['plans'][0]['unavailable'] is None
    t = client.post('/api/work/tasks', json={'quick': 'research +apex'}).json()
    assert t['apex_ok'] == 1 and client.patch(f"/api/work/tasks/{t['id']}", json={'apex_ok': False}).json()['apex_ok'] == 0
    assert client.post(f"/api/work/tasks/{t['id']}/apex", json={'engine': 'gemini'}).status_code == 400
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'master')        # a device, not the owner
    assert client.get('/api/work/agent').status_code == 200
    assert client.put('/api/work/agent', json={'enabled': False}).status_code == 403
