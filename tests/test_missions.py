"""Missions (agent/missions.py): round after round until the checks pass.

The team task is faked so each test scripts what a round does: what passed,
what failed, what it cost, whether it was cut off. Checked here are the
decisions: when to go on, when it's done, when it's stuck, when it needs a
human, and what it does after a restart.
"""
import sys
import types

import pytest
from fastapi.testclient import TestClient

import config
from agent import missions, team

CHECK = {'kind': 'file_exists', 'spec': '/tmp/result.md'}


class FakeTeam:
    """Each round's outcome comes from `script`: dicts with passed, cost, status, error, unknown, evidence."""

    def __init__(self, script, hold=False):
        self.script, self.hold, self.runs, self.bodies, self.stopped = list(script), hold, {}, [], []

    def submit(self, body, agent):
        self.bodies.append(body)
        step = self.script.pop(0) if self.script else {'passed': False}
        passed = step.get('passed', False)
        status = step.get('status', 'done')
        run = {'id': body['id'], 'status': 'running' if self.hold else status, 'error': step.get('error', ''),
               'cost_usd': step.get('cost', 0.2),
               'verification': {'status': 'verified' if passed else 'failed',
                                'results': [{'kind': 'file_exists', 'spec': '/tmp/result.md', 'passed': passed,
                                             'evidence': step.get('evidence', 'exists' if passed else 'missing: /tmp/result.md')}]}
                               if status == 'done' else {'status': 'unknown', 'results': []},
               'steps': [{'role': 'coder', 'evidence': [{'tool': 'bash', 'status': 'outcome_unknown' if step.get('unknown') else 'returned'}]},
                         {'role': 'apex', 'result': f"round summary {len(self.bodies)}", 'evidence': []}]}
        self.runs[body['id']] = run
        return run

    def get(self, rid):
        return self.runs.get(rid)

    def stop(self, rid):
        self.stopped.append(rid)
        run = self.runs.get(rid)
        if run and run['status'] == 'running':
            run.update(status='interrupted', error='Stopped by user. Completed actions remain in effect.',
                       verification={'status': 'unknown', 'results': []})
        return True


@pytest.fixture
def env(test_db, monkeypatch):
    monkeypatch.setattr(missions, '_POLL', 0.001)
    monkeypatch.setattr(missions, '_ready', None)
    monkeypatch.setattr(missions, 'start', lambda mid, agent=None: None)     # tests drive run() themselves
    monkeypatch.setattr(team, 'validate', lambda body: body)
    monkeypatch.setattr(team, 'ensure_db', lambda: None)
    sent = []
    from agent import notify
    monkeypatch.setattr(notify, 'notify', lambda title, body, **k: sent.append((title, body)))
    env = types.SimpleNamespace(sent=sent)

    def use(script, hold=False):
        fake = FakeTeam(script, hold)
        for name in ('submit', 'get', 'stop'):
            monkeypatch.setattr(team, name, getattr(fake, name))
        return fake
    env.use = use
    return env


def new(**extra):
    return missions.create(dict(dict(title='Make the report', task='Write the weekly report to /tmp/result.md',
                                     checks=[CHECK], budget_usd=5, max_rounds=8), **extra))


# --- validation --------------------------------------------------------------------------

@pytest.mark.parametrize('change,needle', [
    ({'checks': []}, '1–10 completion checks'),
    ({'checks': [{'kind': 'manual', 'spec': 'Alex says it looks good'}]}, 'can run itself'),
    ({'checks': [CHECK, {'kind': 'manual', 'spec': 'Alex approves'}]}, 'never pass on its own'),
    ({'checks': [{'kind': 'contains', 'spec': '/tmp/r.md'}]}, 'needs the text'),
    ({'budget_usd': 50}, 'spending cap'),
    ({'max_rounds': 0}, 'Rounds'),
    ({'task': 'short'}, 'Describe the mission'),
])
def test_a_mission_needs_a_finish_line_it_can_check(env, change, needle):
    with pytest.raises(ValueError, match=needle):
        new(**change)


def test_command_checks_need_the_docker_sandbox(env, monkeypatch):
    from tools import sandbox

    def none():
        raise sandbox.SandboxUnavailable('no docker')
    monkeypatch.setattr(sandbox, 'autonomous_backend', none)
    with pytest.raises(ValueError, match='Docker'):
        new(checks=[{'kind': 'command', 'spec': 'pytest -q'}])


# --- the loop -----------------------------------------------------------------------------

def test_keeps_going_until_the_checks_pass_and_feeds_back_what_failed(env):
    fake = env.use([{'passed': False, 'evidence': 'missing: /tmp/result.md'},
                    {'passed': False, 'evidence': 'exists but empty: /tmp/result.md'},
                    {'passed': True}])
    m = new()
    missions.run(m['id'], None)
    m = missions.get(m['id'])
    assert m['status'] == 'complete' and len(m['rounds']) == 3 and m['spent_usd'] == pytest.approx(0.6)
    assert 'round 3' in m['reason']
    # Round 2 was told what round 1's check said, and to inspect before redoing.
    assert 'missing: /tmp/result.md' in fake.bodies[1]['task'] and 'inspect the current state' in fake.bodies[1]['task']
    assert fake.bodies[0]['goal_id'] == m['goal_id'] and fake.bodies[0]['budget_usd'] <= missions.ROUND_BUDGET
    assert env.sent and 'complete' in env.sent[-1][0]


def test_same_failure_three_times_is_stuck_not_burning_money(env):
    env.use([{'passed': False}] * 8)
    m = new()
    missions.run(m['id'], None)
    m = missions.get(m['id'])
    assert m['status'] == 'stuck' and len(m['rounds']) == missions.STALL_ROUNDS and 'No progress' in m['reason']


def test_out_of_rounds_is_stuck(env):
    env.use([{'passed': False, 'evidence': f'attempt {i}'} for i in range(3)])
    m = new(max_rounds=3)
    missions.run(m['id'], None)
    m = missions.get(m['id'])
    assert m['status'] == 'stuck' and 'Used all 3 rounds' in m['reason']


def test_budget_is_a_hard_stop(env):
    env.use([{'passed': False, 'evidence': f'try {i}', 'cost': 0.4} for i in range(9)])
    m = new(budget_usd=1.0)
    missions.run(m['id'], None)
    m = missions.get(m['id'])
    assert m['status'] == 'out_of_budget' and m['spent_usd'] >= 0.95


@pytest.mark.parametrize('step,needle', [
    ({'status': 'blocked', 'error': 'A tool was blocked. [BLOCKED] git push needs approval'}, 'needs your approval'),
    ({'status': 'failed', 'unknown': True, 'error': 'boom'}, 'cut off in the middle of an action'),
    ({'status': 'blocked', 'error': '[Safety] Daily spend cap $3.00 reached'}, 'spending cap'),
    ({'status': 'failed', 'error': 'AuthenticationError: invalid x-api-key'}, 'invalid x-api-key'),
])
def test_a_human_is_asked_when_a_step_needs_one(env, step, needle):
    env.use([step, {'passed': True}])
    m = new()
    missions.run(m['id'], None)
    m = missions.get(m['id'])
    assert m['status'] == 'needs_you' and needle in m['reason']
    missions.resume(m['id'])
    missions.run(m['id'], None)
    assert missions.get(m['id'])['status'] == 'complete'


def test_an_ordinary_unfinished_round_just_tries_again(env):
    env.use([{'status': 'blocked', 'error': 'Specialist reached its four-call limit.'}, {'passed': True}])
    m = new()
    missions.run(m['id'], None)
    assert missions.get(m['id'])['status'] == 'complete'


def test_pause_stops_the_running_round(env):
    import threading
    fake = env.use([{'passed': True}], hold=True)
    m = new()
    worker = threading.Thread(target=missions.run, args=(m['id'], None))
    worker.start()
    for _ in range(2000):
        if fake.runs:
            break
        threading.Event().wait(0.001)
    missions.pause(m['id'])
    worker.join(5)
    m = missions.get(m['id'])
    assert m['status'] == 'paused' and fake.stopped and m['rounds'][0]['status'] == 'interrupted'


def test_resume_with_more_rounds_and_money(env):
    env.use([{'passed': False, 'evidence': f'e{i}'} for i in range(2)] + [{'passed': True}])
    m = new(max_rounds=2)
    missions.run(m['id'], None)
    assert missions.get(m['id'])['status'] == 'stuck'
    with pytest.raises(ValueError, match='extra rounds'):
        missions.resume(m['id'])
    missions.resume(m['id'], extra_rounds=2, extra_budget=1)
    missions.run(m['id'], None)
    m = missions.get(m['id'])
    assert m['status'] == 'complete' and m['max_rounds'] == 4 and m['budget_usd'] == 6


def test_stop_is_final(env):
    env.use([])
    m = new()
    missions.stop(m['id'])
    assert missions.get(m['id'])['status'] == 'stopped'
    with pytest.raises(ValueError):
        missions.resume(m['id'])


# --- after a restart ---------------------------------------------------------------------------

def test_restart_carries_on_unless_an_outcome_is_unknown(env, monkeypatch):
    started = []
    monkeypatch.setattr(missions, 'start', lambda mid, agent=None: started.append(mid))
    fake = env.use([])
    clean, cut = new(), new(title='Second mission')
    # Apex stopped while each was in round 1; the restart marked those rounds interrupted.
    fake.runs[f"{clean['id']}-r1"] = {'id': f"{clean['id']}-r1", 'status': 'interrupted', 'error': 'Apex restarted.',
                                      'cost_usd': 0.1, 'verification': {}, 'steps': [{'role': 'researcher', 'evidence': [{'status': 'returned'}]}]}
    fake.runs[f"{cut['id']}-r1"] = {'id': f"{cut['id']}-r1", 'status': 'interrupted', 'error': 'Apex restarted.',
                                    'cost_usd': 0.1, 'verification': {}, 'steps': [{'role': 'coder', 'evidence': [{'status': 'outcome_unknown'}]}]}
    started.clear()
    missions.start_supervisor(agent=None)
    assert started == [clean['id']]
    assert missions.get(clean['id'])['status'] == 'running' and len(missions.get(clean['id'])['rounds']) == 1
    after = missions.get(cut['id'])
    assert after['status'] == 'needs_you' and 'restarted mid-round' in after['reason']


def test_windows_is_asked_not_to_sleep_while_a_mission_works(env, monkeypatch):
    calls = []
    fake_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=types.SimpleNamespace(
        SetThreadExecutionState=lambda flags: calls.append(flags))))
    monkeypatch.setattr(missions.sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'ctypes', fake_ctypes)
    env.use([{'passed': True}])
    m = new()
    missions.run(m['id'], None)
    assert calls == [0x80000001, 0x80000000]                  # awake while working, released after


# --- the page and API ---------------------------------------------------------------------------

@pytest.fixture
def api(env, monkeypatch):
    from dashboard import server
    from dashboard.ratelimit import AuthThrottle
    monkeypatch.setattr(server, '_throttle', AuthThrottle())
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'missions-test')
    monkeypatch.setattr(server, '_agent_ref', object())
    with TestClient(server.app) as c:
        yield c


def test_api_create_watch_and_control(api, env):
    env.use([])
    api.headers['Authorization'] = 'Bearer missions-test'
    r = api.post('/api/missions', json=dict(title='Make the report', task='Write the weekly report to /tmp/result.md', checks=[CHECK]))
    assert r.status_code == 200, r.text
    mid = r.json()['id']
    assert [m['id'] for m in api.get('/api/missions').json()['missions']] == [mid]
    assert api.post(f'/api/missions/{mid}/pause').json()['status'] == 'paused'
    assert api.post(f'/api/missions/{mid}/pause').status_code == 409
    assert api.post('/api/missions', json={'title': 'x'}).status_code == 400
    assert api.get('/missions').status_code == 200


def test_only_the_owner_starts_missions(api, monkeypatch):
    from agent import access_tokens
    monkeypatch.setattr(access_tokens, 'verify', lambda t: t == 'phone-token')
    api.headers['Authorization'] = 'Bearer phone-token'
    assert api.get('/api/missions').status_code == 200
    r = api.post('/api/missions', json=dict(title='Make the report', task='Write the weekly report to /tmp/result.md', checks=[CHECK]))
    assert r.status_code == 403
