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
import json, os, subprocess, sys, pathlib
here = pathlib.Path(os.environ['FAKE_DIR'])
# How it is signed in: the real tools' own answers (claude auth status --json, codex login status).
if sys.argv[1:3] in (['auth', 'status'], ['login', 'status']):
    (here / '{name}.asked').write_text('yes')
    auth = (here / '{name}.auth').read_text().strip()
    if '{name}' == 'claude':
        print(auth); sys.exit(0 if '"loggedIn": true' in auth else 1)
    print(auth, file=sys.stderr); sys.exit(0 if auth.startswith('Logged in') else 1)
if '--help' in sys.argv or '--version' in sys.argv:   # what the real tools print, in short
    print('{name} 9.9 -p --output-format --permission-mode --allowedTools --skip-git-repo-check --ephemeral '
          '--cd --sandbox workspace-write --output-last-message instructions are read from stdin'); sys.exit(0)
mode = (here / '{name}.mode').read_text().strip()
(here / '{name}.args').write_text(json.dumps({{
    'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin': sys.stdin.read(),
    'keys': [k for k in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY') if k in os.environ]}}))
if mode == 'slow':
    import time; time.sleep(5)
if mode == 'check':                    # does what the live check asks, like a real plan would
    pathlib.Path('apex-check.md').write_text('ready'); mode = 'done'
if mode == 'hang':                     # starts a helper of its own, as the real tools do, then hangs
    helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    (here / '{name}.helper').write_text(str(helper.pid))
    import time; time.sleep(60)
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


PLAN_CLAUDE = json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'max'}, indent=2)


@pytest.fixture
def fake(tmp_path, monkeypatch, test_db):
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    for name in ('claude', 'codex'):
        p = bin_dir / name
        p.write_text(FAKE.format(python=sys.executable, name=name))
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        (bin_dir / f'{name}.mode').write_text('done')
    (bin_dir / 'claude.auth').write_text(PLAN_CLAUDE)
    (bin_dir / 'codex.auth').write_text('Logged in using ChatGPT')
    work_engines.forget_checks()
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
        def auth(self, name, text): (bin_dir / f'{name}.auth').write_text(text); work_engines.forget_checks()
        def asked(self, name): return (bin_dir / f'{name}.asked').exists()
        def helper(self, name): return int((bin_dir / f'{name}.helper').read_text())
    yield Fake()
    work_engines.forget_checks()


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
    assert r['status'] == 'missing' and 'Setup-Apex-Work-Plans.cmd' in r['summary']
    assert work_engines.installed() == {'claude': True, 'chatgpt': False, 'api': True}
    fake.mode('claude', 'slow')
    r = work_engines.run('claude', 'x', tmp_path / 'g', timeout=1)
    assert r['status'] == 'failed' and 'longer than' in r['summary']


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                   # a finished child that is not reaped yet counts as gone
        return open(f'/proc/{pid}/stat').read().split(')')[-1].split()[0] != 'Z'
    except OSError:
        return True


@pytest.mark.parametrize('engine,name,auth,ok,why', [
    ('claude', 'claude', PLAN_CLAUDE, True, ''),
    ('claude', 'claude', json.dumps({'loggedIn': True, 'authMethod': 'oauth_token', 'apiProvider': 'firstParty'}), True, ''),
    ('claude', 'claude', json.dumps({'loggedIn': True, 'authMethod': 'api_key', 'apiProvider': 'firstParty'}), False, 'bills credits'),
    ('claude', 'claude', json.dumps({'loggedIn': True, 'authMethod': 'apiKeyHelper', 'apiProvider': 'firstParty'}), False, 'bills credits'),
    ('claude', 'claude', json.dumps({'loggedIn': True, 'authMethod': 'third_party', 'apiProvider': 'bedrock'}), False, 'bills credits'),
    ('claude', 'claude', json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                                     'apiKeySource': 'ANTHROPIC_API_KEY'}), False, 'bills credits'),
    ('claude', 'claude', json.dumps({'loggedIn': False, 'authMethod': 'none', 'apiProvider': 'firstParty'}), False, 'not signed in'),
    ('claude', 'claude', 'Error: something odd', False, 'could not read'),
    ('chatgpt', 'codex', 'Logged in using ChatGPT', True, ''),
    ('chatgpt', 'codex', 'Logged in using an API key - sk-proj-***ABCDE', False, 'with an API key, which is not your ChatGPT plan'),
    ('chatgpt', 'codex', 'Logged in using Amazon Bedrock API key', False, 'not your ChatGPT plan'),
    ('chatgpt', 'codex', 'Not logged in', False, 'not signed in'),
])
def test_only_a_plan_sign_in_counts(fake, engine, name, auth, ok, why):
    """The real tools' sign-in answers (checked against Claude Code 2.1 and Codex 0.160)."""
    fake.auth(name, auth)
    got = work_engines.check(engine)
    assert got['ok'] is ok and why in got['why']
    assert 'sk-' not in got['why']                                   # a key is never repeated, even masked
    if not ok:                                                       # and the task never reaches it
        r = work_engines.run(engine, 'x', fake.dir / 'f')
        assert r['status'] == 'signed_out' and not (fake.dir / f'{name}.args').exists()


def test_the_sign_in_check_is_cached_and_refreshed(fake):
    assert work_engines.check('claude')['plan'] == 'Claude Max'
    (fake.dir / 'claude.auth').write_text(json.dumps({'loggedIn': True, 'authMethod': 'api_key'}))
    assert work_engines.check('claude')['ok'] is True                # within ten minutes: the last answer
    work_agent.check_plans()                                         # "Check sign-in" asks again
    assert work_engines.check('claude')['ok'] is False


def test_handing_over_refuses_a_plan_that_would_bill_credits(fake):
    fake.auth('codex', 'Logged in using an API key - sk-***')
    t = work.add_task(title='x')
    with pytest.raises(work.WorkError, match='not your ChatGPT plan'):
        work.give_to_apex(t['id'], agent=object(), engine='chatgpt')
    assert work.get_task(t['id'])['apex_state'] is None


@pytest.mark.parametrize('engine,name', [('claude', 'claude'), ('chatgpt', 'codex')])
def test_stop_ends_the_tool_and_everything_it_started(fake, engine, name):
    fake.mode(name, 'hang')
    t = work.add_task(title='long one')
    run_id = work.give_to_apex(t['id'], agent=object(), engine=engine)['apex_run']
    end = time.time() + 10
    while not (fake.dir / f'{name}.helper').exists() and time.time() < end:
        time.sleep(0.05)
    helper = fake.helper(name)
    assert _alive(helper)
    assert work.stop_apex(t['id'])['apex_run'] == run_id
    back = wait_for(t['id'])
    assert back['apex_state'] == 'stopped' and back['status'] == 'todo' and 'You stopped it' in back['apex_summary']
    end = time.time() + 10
    while _alive(helper) and time.time() < end:
        time.sleep(0.05)
    assert not _alive(helper), 'the helper the tool started must not keep running on your plan'
    with pytest.raises(work.WorkError, match='not working'):
        work.stop_apex(t['id'])


def test_a_timeout_ends_the_whole_tree(fake, tmp_path):
    fake.mode('claude', 'hang')
    r = work_engines.run('claude', 'x', tmp_path / 'f', timeout=2)
    assert r['status'] == 'failed' and 'longer than' in r['summary']
    helper = fake.helper('claude')
    end = time.time() + 10
    while _alive(helper) and time.time() < end:
        time.sleep(0.05)
    assert not _alive(helper)


@pytest.mark.parametrize('text,hours', [
    ('Claude AI usage limit reached|1791306000', None),             # an exact time (epoch)
    ("You've hit your usage limit. Upgrade to Pro or try again in 2 hours 14 minutes.", 2 + 14 / 60),
    ('5-hour limit reached ∙ resets 3pm', 3),                       # 12:00 now
    ("You've hit your usage limit. Try again at 1:30 PM.", 1.5),
    ('try again in 3 days', 72),
    ('Weekly limit reached', -1),                                   # no time given
])
def test_reset_times_the_tools_give(text, hours):
    got = work_engines.reset_time(text, NOON)
    if hours is None:
        assert got == 1791306000
    elif hours < 0:
        assert got is None
    else:
        assert abs(got - (NOON.timestamp() + hours * 3600)) < 1
    assert work_engines.reset_time('resets 9am', NOON) == datetime(2026, 10, 7, 9, 0).timestamp()   # tomorrow


def test_a_plan_rests_until_its_reset_time(fake):
    now = NOON.timestamp()
    work_agent.mark_unavailable('chatgpt', 'limited', 'try again in 2 hours', now=now, until=now + 7200)
    assert work_agent.available(now + 7200)['chatgpt'].endswith('resting until 14:02')
    assert work_agent.available(now + 7400)['chatgpt'] is None
    work_agent.mark_unavailable('claude', 'limited', 'limit', now=now, until=now + 30 * 86400)   # nonsense: use rest_hours
    assert work_agent.available(now)['claude'].endswith('resting until 17:00')


def test_limits_carry_the_reset_time_into_the_run(fake, tmp_path):
    fake.mode('codex', 'limited')
    r = work_engines.run('chatgpt', 'x', tmp_path / 'f')
    assert r['status'] == 'limited' and abs(r['reset_at'] - (time.time() + 3 * 3600)) < 60


def test_noise_is_left_out_of_summaries_and_numbers_are_not_limits():
    real = """WARNING: proceeding, even though we could not create PATH aliases: Refusing to create helper binaries
2026-10-06T08:11:22.735859Z ERROR codex_api::endpoint::responses_websocket: failed to connect to websocket: HTTP error: 403 Forbidden
ERROR: Reconnecting... 2/5
ERROR: Reconnecting... 3/5
warning: Codex could not find bubblewrap on PATH.
ERROR: unexpected status 403 Forbidden: Host not in allowlist: api.openai.com.
ERROR: unexpected status 403 Forbidden: Host not in allowlist: api.openai.com."""
    tidy = work_engines._tidy(real)
    assert 'WARNING' not in tidy and 'Reconnecting' not in tidy and 'bubblewrap' not in tidy
    assert tidy.count('Host not in allowlist') == 1 and tidy.startswith('ERROR codex_api')
    assert work_engines._classify(real, failed=True) == 'failed'
    assert work_engines._classify('2026-10-06T08:14:29.429123Z ERROR boom', failed=True) == 'failed'
    assert work_engines._classify('HTTP 429 Too Many Requests', failed=True) == 'limited'


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


def test_found_where_the_installers_put_them_before_path_catches_up(fake, tmp_path, monkeypatch):
    home = tmp_path / 'home'; local = home / '.local' / 'bin'; local.mkdir(parents=True)
    tool = local / 'claude'; tool.write_text('#!/bin/sh\n'); tool.chmod(0o755)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    assert work_engines.binary('claude') == str(tool)
    assert work_engines.binary('chatgpt') is None


def test_the_plan_checker_script(fake, capsys):
    from scripts import work_plans_check
    fake.mode('claude', 'done')
    # The fake claude writes result.md, not apex-check.md: the live task must notice.
    assert work_plans_check.main(['--live', '--only', 'claude']) == 1
    assert 'FAIL  it did not write apex-check.md' in capsys.readouterr().out
    fake.auth('codex', 'Logged in using an API key - sk-***')
    assert work_plans_check.main(['--only', 'chatgpt']) == 1
    out = capsys.readouterr().out
    assert 'not your ChatGPT plan' in out and 'ChatGPT plan: NOT READY' in out
    fake.auth('codex', 'Logged in using ChatGPT')
    assert work_plans_check.main([]) == 0
    out = capsys.readouterr().out
    assert 'signed in with your plan (Claude Max)' in out and 'Apex can work on Claude plan and ChatGPT plan.' in out
    fake.mode('claude', 'check'); fake.mode('codex', 'check')
    assert work_plans_check.main(['--live']) == 0
    out = capsys.readouterr().out
    assert out.count('PASS  it wrote apex-check.md') == 2 and 'Claude plan: READY (real task passed)' in out


def test_the_setup_asks_only_whether_a_plan_is_signed_in(fake, capsys):
    from scripts import work_plans_check
    assert work_plans_check.main(['--signed-in', 'claude']) == 0
    assert 'Claude plan: signed in (Claude Max)' in capsys.readouterr().out
    fake.auth('codex', 'Not logged in')
    assert work_plans_check.main(['--signed-in', 'chatgpt']) == 1
    assert 'ChatGPT plan: is not signed in' in capsys.readouterr().out
    assert not (fake.dir / 'codex.args').exists()                     # nothing ran: no usage


def test_the_setup_script_signs_in_with_the_plan_never_the_console():
    from pathlib import Path
    cmd = (Path(__file__).parents[1] / 'Setup-Apex-Work-Plans.cmd').read_text()
    assert 'claude auth login --claudeai' in cmd and '--console' not in cmd
    assert '--signed-in claude' in cmd and '--signed-in chatgpt' in cmd and '--live' in cmd
    assert 'call claude\r\n' not in cmd                                # never drops you into the full app
