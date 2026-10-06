"""Work (agent/work.py, dashboard/work.py): tasks and projects, quick add,
the Today view, the chat/voice tool, and handing a task to Apex."""
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import work

TUE = date(2026, 10, 6)          # a Tuesday


@pytest.fixture
def db(test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(work, 'WORK_DIR', tmp_path / 'ApexWork')
    work.init_db()
    return tmp_path


@pytest.mark.parametrize('text,want', [
    ('send invoice to Karim fri #job !high', dict(title='send invoice to Karim', due='2026-10-09', area='job', priority=1)),
    ('study chapter 4 tomorrow #studies', dict(title='study chapter 4', due='2026-10-07', area='studies')),
    ('pay rent by tue', dict(title='pay rent', due='2026-10-13')),               # "tue" on a Tuesday is next week's
    ('call the accountant next week waiting', dict(title='call the accountant', due='2026-10-12', status='waiting')),
    ('meet supplier on 12/10', dict(title='meet supplier', due='2026-10-12')),    # day/month
    ('renew domain 3/1', dict(title='renew domain', due='2027-01-03')),           # past this year: next year
    ('fix login 2026-10-20 #software !low', dict(title='fix login', due='2026-10-20', area='software', priority=3)),
    ('prepare for the SAT', dict(title='prepare for the SAT')),                   # an exam, not Saturday
    ('read #nonsense tags stay', dict(title='read #nonsense tags stay')),
])
def test_quick_add_shorthand(text, want):
    got = work.parse_quick(text, TUE)
    assert {k: got.get(k) for k in want} == want
    if 'due' not in want:
        assert 'due' not in got


def test_quick_add_files_under_a_project_by_its_name(db):
    p = work.add_project('Website for Karim', 'job', client='Karim')
    t = work.add_task(quick='send the mockups @website thu', today=TUE)
    assert t['project_id'] == p['id'] and t['area'] == 'job' and t['due'] == '2026-10-08'
    assert work.parse_quick('@nobody hello', TUE, work.list_projects())['title'] == '@nobody hello'


def test_validation(db):
    for bad in (dict(quick='#job !high'), dict(title='x', area='hobbies'), dict(title='x', due='Friday'),
                dict(title='x', priority=9), dict(title='x' * 300), dict(title='x', project_id=999)):
        with pytest.raises(work.WorkError):
            work.add_task(**bad)
    t = work.add_task(title='real')
    with pytest.raises(work.WorkError):
        work.update_task(t['id'], status='finished')
    with pytest.raises(work.WorkError):
        work.update_task(9999, status='done')


def test_status_changes_and_done_time(db):
    t = work.add_task(title='write report')
    done = work.update_task(t['id'], status='done')
    assert done['status'] == 'done' and done['done_at']
    assert work.update_task(t['id'], status='todo')['done_at'] is None


def test_today_view_sorts_work_into_the_right_places(db):
    add = lambda title, **kw: work.add_task(title=title, **kw)
    late = add('late one', due='2026-10-01')
    today = add('due today', due='2026-10-06', area='studies')
    soon = add('this week', due='2026-10-10')
    later = add('next month', due='2026-11-20')
    waiting = add('waiting one', due='2026-10-01', status='waiting')
    review = add('apex finished', status='review')
    finished = add('done already', due='2026-10-06'); work.update_task(finished['id'], status='done')
    v = work.today_view(TUE)
    ids = lambda key: [t['id'] for t in v[key]]
    assert ids('overdue') == [late['id']]                      # waiting work is not "overdue" on you
    assert ids('today') == [today['id']] and ids('week') == [soon['id']]
    assert ids('waiting') == [waiting['id']] and ids('review') == [review['id']]
    assert later['id'] not in ids('week') and finished['id'] not in ids('today')
    assert v['counts']['studies'] == 1


def test_handing_a_task_to_apex_and_getting_it_back_for_review(db, monkeypatch):
    from agent import team
    submitted, runs = [], {}
    monkeypatch.setattr(team, 'submit', lambda body, agent: submitted.append(body) or runs.setdefault(body['id'], {'status': 'running', 'steps': []}))
    monkeypatch.setattr(team, 'get', lambda rid: runs.get(rid))
    p = work.add_project('Lab 3', 'studies')
    t = work.add_task(title='Summarise the op-amp lab results', notes='Use the CSV in my Downloads', project_id=p['id'], due='2026-10-09')
    given = work.give_to_apex(t['id'], agent=object())
    body = submitted[0]
    assert given['status'] == 'doing' and given['apex_state'] == 'queued'
    assert 'op-amp lab' in body['task'] and 'CSV in my Downloads' in body['task'] and "'Lab 3'" in body['task']
    assert given['apex_folder'] in body['task'] and 'Do not send, publish, pay or contact anyone' in body['task']
    assert body['budget_usd'] == work.APEX_BUDGET
    with pytest.raises(work.WorkError, match='already working'):
        work.give_to_apex(t['id'], agent=object())
    # While it runs, Today shows it as Apex's.
    work.sync_apex()
    assert [x['id'] for x in work.today_view(TUE)['apex_working']] == [t['id']]
    # Done: back to the owner for review, with the summary, cost and files; never marked done by Apex.
    from pathlib import Path
    Path(given['apex_folder'], 'result.md').write_text('# Results')
    runs[body['id']] = {'status': 'done', 'cost_usd': 0.0412,
                        'steps': [{'result': 'draft'}, {'result': 'Summary: wrote result.md with the gain table.'}]}
    back = [x for x in work.list_tasks() if x['id'] == t['id']][0]
    assert back['status'] == 'review' and back['apex_state'] == 'done' and back['apex_cost'] == 0.0412
    assert back['apex_summary'].startswith('Summary: wrote result.md') and work.files_of(back) == ['result.md']


def test_apex_stopping_short_returns_the_task_to_you(db, monkeypatch):
    from agent import team
    runs = {}
    monkeypatch.setattr(team, 'submit', lambda body, agent: runs.setdefault(body['id'], {'status': 'running', 'steps': []}))
    monkeypatch.setattr(team, 'get', lambda rid: runs.get(rid))
    t = work.add_task(title='Draft the client email')
    run_id = work.give_to_apex(t['id'], agent=object())['apex_run']
    runs[run_id] = {'status': 'blocked', 'error': 'A tool was blocked.', 'cost_usd': 0.01, 'steps': [{'result': ''}]}
    back = [x for x in work.list_tasks() if x['id'] == t['id']][0]
    assert back['status'] == 'todo' and back['apex_state'] == 'blocked' and 'blocked' in back['apex_summary']


def test_runner_refusal_is_a_clear_error(db, monkeypatch):
    from agent import team
    def refuse(body, agent): raise RuntimeError('A team task is already running. Wait or stop it before starting another.')
    monkeypatch.setattr(team, 'submit', refuse)
    t = work.add_task(title='x')
    with pytest.raises(work.WorkError, match='already running'):
        work.give_to_apex(t['id'], agent=object())
    assert work.get_task(t['id'])['apex_state'] is None


def test_chat_and_voice_tool(db):
    added = work.tool({'action': 'add', 'quick': 'send invoice to Karim tomorrow #job !high'})
    assert 'send invoice to Karim' in added and 'Job & clients' in added and 'high priority' in added
    tid = work.list_tasks()[0]['id']
    assert 'Due today' in work.tool({'action': 'today'}) or 'Next 7 days' in work.tool({'action': 'today'})
    assert 'send invoice' in work.tool({'action': 'list', 'area': 'job'})
    assert 'done' in work.tool({'action': 'done', 'id': tid})
    assert work.tool({'action': 'done'}).startswith('Give the task id')
    assert work.tool({'action': 'add', 'quick': ''}).startswith('Could not do that')
    assert 'Nothing' in work.tool({'action': 'today'})


@pytest.fixture
def api(db, monkeypatch):
    import config
    from dashboard import work as route
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', '')
    app = FastAPI(); app.include_router(route.router)
    return TestClient(app), config


def test_api_round_trip(api):
    client, _ = api
    p = client.post('/api/work/projects', json={'name': 'Sky Light', 'area': 'business'}).json()
    t = client.post('/api/work/tasks', json={'quick': 'call supplier tomorrow @sky !high'}).json()
    assert t['project_id'] == p['id'] and t['priority'] == 1
    assert client.patch(f"/api/work/tasks/{t['id']}", json={'status': 'waiting', 'waiting_on': 'Ahmad'}).json()['waiting_on'] == 'Ahmad'
    overview = client.get('/api/work').json()
    assert [a['id'] for a in overview['areas']] == list(work.AREAS)
    assert overview['today']['waiting'][0]['id'] == t['id']
    assert client.post('/api/work/tasks', json={'quick': ''}).status_code == 400
    assert client.post('/api/work/tasks', json={'title': 'x'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.delete(f"/api/work/tasks/{t['id']}").json() == {'deleted': t['id']}
    assert client.delete(f"/api/work/tasks/{t['id']}").status_code == 404
    assert client.patch(f"/api/work/projects/{p['id']}", json={'status': 'archived'}).json()['status'] == 'archived'
    assert client.get('/api/work').json()['projects'] == []


def test_only_the_owner_hands_work_to_apex(api, monkeypatch):
    client, config = api
    t = client.post('/api/work/tasks', json={'title': 'research laptops'}).json()
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'master')          # a device token, not the master one
    assert client.post(f"/api/work/tasks/{t['id']}/apex", json={}).status_code == 403
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', '')
    assert client.post(f"/api/work/tasks/{t['id']}/apex", json={'budget_usd': 50}).status_code == 400
