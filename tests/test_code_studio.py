"""Apex Code (agent/code_studio.py, agent/code_engines.py, dashboard/code.py):
sessions on their own git branch, live feeds from your plans, keep / throw away
/ undo / catch up, the second opinion and checks. Real git repositories; the
`claude` and `codex` tools are fake programs that stream the same JSON the real
ones do (formats checked against Claude Code 2.1 and Codex 0.160) and really
edit files."""
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import code_engines, code_studio, work, work_engines

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='fake CLIs are POSIX scripts')

FAKE = r'''#!{python}
import json, os, re, subprocess, sys, time, pathlib
here = pathlib.Path(os.environ['FAKE_DIR'])
if sys.argv[1:3] in (['auth', 'status'], ['login', 'status']):
    if '{name}' == 'claude':
        print(json.dumps({{'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'pro'}}))
    else:
        print('Logged in using ChatGPT', file=sys.stderr)
    sys.exit(0)
prompt = sys.stdin.read()
mode = (here / '{name}.mode').read_text().strip()
with open(here / '{name}.calls', 'a') as f:
    f.write(json.dumps({{'argv': sys.argv[1:], 'stdin': prompt, 'cwd': os.getcwd(),
                        'keys': [k for k in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY') if k in os.environ]}}) + '\n')
out = lambda d: print(json.dumps(d), flush=True)
cwd = pathlib.Path.cwd()
n = len((here / '{name}.calls').read_text().splitlines())
if mode == 'check':                    # does what the plan check asks, like a real plan would
    f = cwd / 'apex-code-check.md'
    f.write_text((f.read_text() if f.exists() else '') + ('again\n' if 'second line' in prompt else 'ready\n'))
if mode == 'hang':
    helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    (here / '{name}.helper').write_text(str(helper.pid))
    time.sleep(60)
if '{name}' == 'claude':
    sid = 'sess-claude-1'
    if mode == 'lost' and '--resume' in sys.argv:
        out({{'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'num_turns': 0,
             'session_id': sys.argv[sys.argv.index('--resume') + 1], 'permission_denials': [],
             'errors': ['No conversation found with session ID: ' + sys.argv[sys.argv.index('--resume') + 1]]}})
        sys.exit(1)
    out({{'type': 'system', 'subtype': 'init', 'session_id': sid, 'cwd': str(cwd), 'tools': ['Read', 'Write'], 'model': 'm'}})
    for chunk in ('Work', 'ing on it'):
        out({{'type': 'stream_event', 'session_id': sid, 'event': {{'type': 'content_block_delta', 'index': 0,
             'delta': {{'type': 'text_delta', 'text': chunk}}}}}})
    if mode == 'editor':
        f = cwd / 'README.md'
        f.write_text(f.read_text().replace('hello', 'hello there'))
        out({{'type': 'assistant', 'session_id': sid, 'message': {{'content': [
            {{'type': 'tool_use', 'id': 'ed1', 'name': 'Edit', 'input': {{'file_path': str(f), 'old_string': 'hello', 'new_string': 'hello there'}}}},
            {{'type': 'tool_use', 'id': 'ed2', 'name': 'Edit', 'input': {{'file_path': str(cwd / 'nope.py'), 'old_string': 'a', 'new_string': 'b'}}}}]}}}})
        out({{'type': 'user', 'session_id': sid, 'message': {{'content': [
            {{'type': 'tool_result', 'tool_use_id': 'ed1', 'content': 'ok'}},
            {{'type': 'tool_result', 'tool_use_id': 'ed2', 'content': 'File does not exist.', 'is_error': True}}]}}}})
        out({{'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'Edited.', 'session_id': sid, 'permission_denials': []}})
        sys.exit(0)
    if mode == 'limited':
        out({{'type': 'result', 'subtype': 'success', 'is_error': True, 'result': 'Claude AI usage limit reached|1791306000',
             'session_id': sid, 'permission_denials': []}}); sys.exit(1)
    if mode == 'review':
        out({{'type': 'assistant', 'session_id': sid, 'message': {{'content': [
            {{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {{'file_path': str(cwd / 'README.md')}}}}]}}}})
        out({{'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': sid, 'permission_denials': [],
             'result': '**Rating:** 6/10\nVerdict: works, thin tests.\nProblems:\n- step.py:1 no test'}}); sys.exit(0)
    if mode == 'check':
        pass
    elif mode == 'resolve':
        for p in cwd.rglob('*'):
            if p.is_file() and '.git' not in p.parts and '<<<<<<<' in p.read_text(errors='replace'):
                p.write_text('resolved: both sides\n')
    else:
        name = f'step{{n}}.py'
        (cwd / name).write_text(f'print({{n}})\n')
        out({{'type': 'assistant', 'session_id': sid, 'message': {{'content': [
            {{'type': 'thinking', 'thinking': 'Plan the change'}},
            {{'type': 'tool_use', 'id': f'w{{n}}', 'name': 'Write', 'input': {{'file_path': str(cwd / name), 'content': f'print({{n}})\n'}}}}]}}}})
        out({{'type': 'user', 'session_id': sid, 'message': {{'content': [
            {{'type': 'tool_use_id', 'x': 1}}, {{'type': 'tool_result', 'tool_use_id': f'w{{n}}', 'content': 'File written'}}]}}}})
        if mode == 'readme':
            (cwd / 'README.md').write_text('session line\n')
        out({{'type': 'assistant', 'session_id': sid, 'message': {{'content': [
            {{'type': 'tool_use', 'id': f'b{{n}}', 'name': 'Bash', 'input': {{'command': 'python -m pytest -q', 'description': 'Run tests'}}}}]}}}})
        out({{'type': 'user', 'session_id': sid, 'message': {{'content': [
            {{'type': 'tool_result', 'tool_use_id': f'b{{n}}', 'content': [{{'type': 'text', 'text': '3 passed'}}]}}]}}}})
    out({{'type': 'assistant', 'session_id': sid, 'message': {{'content': [{{'type': 'text', 'text': f'Done: step {{n}}.'}}]}}}})
    out({{'type': 'result', 'subtype': 'success', 'is_error': False, 'result': f'Added step{{n}}.py and ran the tests.',
         'session_id': sid, 'usage': {{'input_tokens': 10, 'output_tokens': 5}},
         'permission_denials': [{{'tool_name': 'Bash', 'tool_input': {{'command': 'rm -rf build'}}}}]}})
else:
    out({{'type': 'thread.started', 'thread_id': 'thread-codex-1'}})
    out({{'type': 'turn.started'}})
    if mode == 'limited':
        out({{'type': 'error', 'message': "You've hit your usage limit. Try again in 2 hours."}})
        out({{'type': 'turn.failed', 'error': {{'message': "You've hit your usage limit. Try again in 2 hours."}}}}); sys.exit(1)
    if mode == 'check':
        out({{'type': 'item.completed', 'item': {{'id': 'm1', 'type': 'agent_message', 'text': 'Done.'}}}})
    elif mode == 'review':
        out({{'type': 'item.completed', 'item': {{'id': 'i1', 'type': 'agent_message',
             'text': 'Rating: 6/10\nVerdict: works, thin tests.\nProblems:\n- step1.py:1 no test\nGood:\n- small'}}}})
    else:
        out({{'type': 'item.started', 'item': {{'id': 'c1', 'type': 'command_execution', 'command': 'bash -lc ls', 'status': 'in_progress'}}}})
        out({{'type': 'item.completed', 'item': {{'id': 'c1', 'type': 'command_execution', 'command': 'bash -lc ls',
             'aggregated_output': 'README.md\n', 'exit_code': 0, 'status': 'completed'}}}})
        name = f'codex{{n}}.py'
        (cwd / name).write_text('print("codex")\n')
        out({{'type': 'item.completed', 'item': {{'id': 'f1', 'type': 'file_change', 'status': 'completed',
             'changes': [{{'path': str(cwd / name), 'kind': 'add'}}]}}}})
        out({{'type': 'item.completed', 'item': {{'id': 'm1', 'type': 'agent_message', 'text': f'Created {{name}}.'}}}})
    out({{'type': 'turn.completed', 'usage': {{'input_tokens': 7, 'cached_input_tokens': 0, 'output_tokens': 3}}}})
'''


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@example.com', *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def lab(tmp_path, monkeypatch, test_db):
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    for name in ('claude', 'codex'):
        p = bin_dir / name
        p.write_text(FAKE.format(python=sys.executable, name=name))
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        (bin_dir / f'{name}.mode').write_text('edit')
    monkeypatch.setenv('FAKE_DIR', str(bin_dir))
    monkeypatch.setenv('PATH', f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-should-not-reach-the-plan')
    monkeypatch.setattr(work, 'WORK_DIR', tmp_path / 'ApexWork')
    notes = []
    from agent import notify, work_agent
    monkeypatch.setattr(notify, 'notify', lambda title, body, **k: notes.append(body))
    monkeypatch.setattr(work_agent, '_notify', lambda title, body: notes.append(body))
    work_engines.forget_checks()
    code_studio.init_db()
    repo = tmp_path / 'project'; repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    (repo / 'README.md').write_text('hello\n')
    git(repo, 'add', '-A'); git(repo, 'commit', '-q', '-m', 'first')
    proj = code_studio.add_project(str(repo))

    class Lab:
        root, bins, sent = repo, bin_dir, notes
        pid = proj['id']
        def mode(self, name, m): (bin_dir / f'{name}.mode').write_text(m)
        def calls(self, name):
            f = bin_dir / f'{name}.calls'
            return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
    yield Lab()
    for sid in list(code_studio._turns) + list(code_studio._side):
        code_studio.stop(sid)
    _settle()
    work_engines.forget_checks()


def _settle(timeout=15):
    end = time.time() + timeout
    while (code_studio._turns or code_studio._side) and time.time() < end:
        time.sleep(0.05)


def wait(sid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        s = code_studio.session(sid)
        if not s['working'] and not s['side']:
            return s
        time.sleep(0.05)
    raise AssertionError('the session kept working')


def kinds(sid):
    return [e['kind'] for e in code_studio.events(sid)]


# ---------------------------------------------------------------- a session

def test_a_session_works_in_its_own_copy_and_your_project_is_untouched(lab):
    s = code_studio.start(lab.pid, 'Add a step script', 'claude', 'safe')
    assert s['branch'].startswith(f"apex/{s['id']}-add-a-step-script") and s['working']
    s = wait(s['id'])
    copy = Path(s['worktree'])
    assert (copy / 'step1.py').read_text() == 'print(1)\n'
    assert not (lab.root / 'step1.py').exists()                          # your copy: untouched
    assert git(lab.root, 'status', '--porcelain') == ''
    feed = code_studio.events(s['id'])
    assert [e['kind'] for e in feed][:3] == ['you', 'thinking', 'file']
    file_event = next(e for e in feed if e['kind'] == 'file')
    assert file_event['path'] == 'step1.py' and file_event['change'] == 'add' and file_event['plus'] == 1
    assert file_event['diff'] == '@@ -0,0 +1,1 @@\n+print(1)'                 # the edit itself, shown in the feed
    cmd = next(e for e in feed if e['kind'] == 'tool' and e['tool'] == 'command')
    assert cmd['title'] == 'python -m pytest -q'
    assert next(e for e in feed if e['kind'] == 'result' and e['ref'] == cmd['ref'])['output'] == '3 passed'
    ids = [e['id'] for e in feed]                                        # its own numbers, never a tool's id
    assert all(isinstance(i, int) for i in ids) and ids == sorted(set(ids))
    assert next(e for e in feed if e['kind'] == 'blocked')['title'] == 'Bash: rm -rf build'
    # An edit counts the lines it really changes, as git will.
    edit = code_engines._claude_tool('Edit', {'file_path': 'a.py', 'old_string': 'x = 1\ny = 2', 'new_string': 'x = 1\ny = 3\nz = 4'}, Path('.'))[0]
    assert (edit['plus'], edit['minus']) == (2, 1)
    done = feed[-1]
    assert done['kind'] == 'done' and done['status'] == 'done' and done['files'] == 1 and done['total'] == 1
    assert s['summary'] == 'Added step1.py and ran the tests.' and s['engine_session'] == 'sess-claude-1'
    assert any(e['kind'] == 'checkpoint' for e in feed)
    # The brief: the request, no API key, run in the copy.
    call = lab.calls('claude')[-1]
    assert "Alex's request:\nAdd a step script" in call['stdin'] and 'Do not commit, push' in call['stdin']
    assert call['keys'] == [] and Path(call['cwd']).resolve() == copy.resolve()
    argv = call['argv']
    assert 'stream-json' in argv and '--verbose' in argv and 'Bash(git diff:*)' in argv and 'Bash' not in argv
    ch = code_studio.changes(s['id'])
    assert ch['files'] == [{'path': 'step1.py', 'plus': 1, 'minus': 0, 'change': 'add'}]
    assert '+print(1)' in code_studio.diff(s['id'], 'step1.py')
    with pytest.raises(code_studio.CodeError, match='no changes'):
        code_studio.diff(s['id'], '../../etc/passwd')


def test_follow_ups_resume_the_same_conversation(lab):
    sid = code_studio.start(lab.pid, 'First step', 'claude')['id']
    wait(sid)
    code_studio.send(sid, 'Now a second one')
    wait(sid)
    call = lab.calls('claude')[-1]
    assert call['argv'][call['argv'].index('--resume') + 1] == 'sess-claude-1'
    assert call['stdin'] == 'Now a second one'                          # the plan remembers the rest
    assert sorted(f['path'] for f in code_studio.changes(sid)['files']) == ['step1.py', 'step2.py']


def test_one_message_at_a_time_and_a_limit_on_parallel_sessions(lab, monkeypatch):
    lab.mode('claude', 'hang')
    sid = code_studio.start(lab.pid, 'Long job', 'claude')['id']
    s = code_studio.session(sid)
    assert s['working'] and abs(s['since'] - time.time()) < 10              # the page's clock starts at your message
    with pytest.raises(code_studio.CodeError, match='still working'):
        code_studio.send(sid, 'more')
    monkeypatch.setattr(code_studio, 'MAX_PARALLEL', 1)
    with pytest.raises(code_studio.CodeError, match='already working'):
        code_studio.start(lab.pid, 'Another', 'claude')
    assert code_studio.stop(sid)
    assert wait(sid)['last_status'] == 'stopped'


def test_stop_ends_the_tool_and_what_it_started(lab):
    lab.mode('claude', 'hang')
    sid = code_studio.start(lab.pid, 'Hang', 'claude')['id']
    end = time.time() + 10
    while not (lab.bins / 'claude.helper').exists() and time.time() < end:
        time.sleep(0.05)
    helper = int((lab.bins / 'claude.helper').read_text())
    assert code_studio.stop(sid)
    s = wait(sid)
    assert s['last_status'] == 'stopped' and code_studio.events(sid)[-1]['summary'].startswith('You stopped it')
    end = time.time() + 10
    while time.time() < end:
        try:
            os.kill(helper, 0)
            if open(f'/proc/{helper}/stat').read().split(')')[-1].split()[0] == 'Z':
                break
        except (ProcessLookupError, OSError):
            break
        time.sleep(0.05)
    else:
        raise AssertionError('the helper kept running')


def test_keep_merges_into_your_branch_and_tidies_up(lab):
    s = code_studio.start(lab.pid, 'Add the step', 'claude')
    s = wait(s['id'])
    kept = code_studio.keep(s['id'])
    assert kept['status'] == 'kept' and (lab.root / 'step1.py').read_text() == 'print(1)\n'
    assert kept['files_changed'] == 1
    log = git(lab.root, 'log', '-1', '--format=%s%n%b')
    assert log.startswith('Apex Code: Add the step') and 'Added step1.py and ran the tests.' in log
    assert not Path(s['worktree']).exists()
    assert git(lab.root, 'branch', '--list', s['branch']) == ''
    assert code_studio.events(s['id'])[-1]['kind'] == 'kept' and code_studio.events(s['id'])[-1]['files'] == 1
    assert code_studio.changes(s['id'])['files'][0]['path'] == 'step1.py'   # still viewable after keeping
    assert '+print(1)' in code_studio.diff(s['id'], 'step1.py')
    with pytest.raises(code_studio.CodeError, match='finished'):
        code_studio.send(s['id'], 'more')


def test_throw_away_deletes_the_copy_and_branch(lab):
    s = wait(code_studio.start(lab.pid, 'Try something', 'claude')['id'])
    gone = code_studio.discard(s['id'])
    assert gone['status'] == 'discarded' and not Path(s['worktree']).exists()
    assert git(lab.root, 'branch', '--list', s['branch']) == '' and not (lab.root / 'step1.py').exists()
    with pytest.raises(code_studio.CodeError, match='thrown away'):
        code_studio.changes(s['id'])


def test_undo_last_step(lab):
    sid = code_studio.start(lab.pid, 'Step one', 'claude')['id']
    wait(sid)
    code_studio.send(sid, 'Step two')
    copy = Path(wait(sid)['worktree'])
    assert (copy / 'step2.py').exists()
    code_studio.undo(sid)
    assert (copy / 'step1.py').exists() and not (copy / 'step2.py').exists()
    code_studio.undo(sid)
    assert not (copy / 'step1.py').exists()
    with pytest.raises(code_studio.CodeError, match='no step to undo'):
        code_studio.undo(sid)
    code_studio.send(sid, 'Again')
    wait(sid)
    assert lab.calls('claude')[-1]['stdin'].startswith('Note: the owner undid your last step')


def test_switching_plans_gives_the_other_a_recap(lab):
    sid = code_studio.start(lab.pid, 'Write the parser', 'claude')['id']
    wait(sid)
    code_studio.send(sid, 'Finish it on ChatGPT', engine='chatgpt')
    s = wait(sid)
    call = lab.calls('codex')[-1]
    assert 'resume' not in call['argv'] and 'sandbox_mode=workspace-write' in call['argv']
    assert call['stdin'].startswith('You are taking over a coding session')
    assert '- Write the parser' in call['stdin'] and 'step1.py' in call['stdin'] and call['stdin'].endswith('Finish it on ChatGPT')
    assert s['engine'] == 'chatgpt' and s['engine_session'] == 'thread-codex-1'
    feed = code_studio.events(sid)
    assert any(e['kind'] == 'note' and 'Switched to your ChatGPT plan' in e['text'] for e in feed)
    assert any(e['kind'] == 'file' and e['path'].startswith('codex') and e['change'] == 'add' for e in feed)
    # The next ChatGPT message resumes Codex's own thread.
    code_studio.send(sid, 'And one more')
    wait(sid)
    argv = lab.calls('codex')[-1]['argv']
    assert argv[:3] == ['exec', 'resume', '--json'] and argv[-2:] == ['thread-codex-1', '-']


def test_a_lost_conversation_starts_fresh_with_a_recap(lab):
    sid = code_studio.start(lab.pid, 'Start it', 'claude')['id']
    wait(sid)
    lab.mode('claude', 'lost')
    code_studio.send(sid, 'Continue')
    s = wait(sid)
    calls = lab.calls('claude')
    assert '--resume' in calls[-2]['argv'] and '--resume' not in calls[-1]['argv']
    assert calls[-1]['stdin'].startswith('You are taking over') and s['last_status'] == 'done'
    assert any('lost this conversation' in e.get('text', '') for e in code_studio.events(sid))


def test_a_plan_at_its_limit_rests_and_says_so(lab):
    lab.mode('claude', 'limited')
    s = wait(code_studio.start(lab.pid, 'Big job', 'claude')['id'])
    done = code_studio.events(s['id'])[-1]
    assert s['last_status'] == 'limited' and done['reset_at'] == 1791306000
    from agent import work_agent
    assert work_agent.available(time.time())['claude'].startswith('reached its usage limit')
    assert code_studio.overview()['default_engine'] == 'chatgpt'          # the page offers the other plan


def test_second_opinion_from_the_other_plan_rated_out_of_ten(lab):
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    lab.mode('codex', 'review')
    code_studio.review(s['id'])
    r = wait(s['id'])
    assert r['review_state'] == 'done' and r['review_engine'] == 'chatgpt' and r['review_rating'] == 6
    assert r['review_text'].startswith('Rating: 6/10')
    call = lab.calls('codex')[-1]
    assert 'sandbox_mode=read-only' in call['argv']
    assert 'brutally honest' in call['stdin'] and '+print(1)' in call['stdin'] and '- Add a step' in call['stdin']
    assert git(Path(r['worktree']), 'status', '--porcelain') == ''        # a review changes nothing
    # Claude as the reviewer: read-only tools, its rating in bold still read.
    lab.mode('claude', 'review')
    code_studio.review(s['id'], engine='claude')
    r = wait(s['id'])
    assert r['review_rating'] == 6 and '--disallowedTools' in lab.calls('claude')[-1]['argv']
    assert any(e['kind'] == 'review_step' and e['title'] == 'Read README.md' for e in code_studio.events(s['id']))


@pytest.mark.parametrize('text,want', [('Rating: 8/10', 8), ('**Rating:** 7 / 10', 7), ('rating - 9/10', 9),
                                       ('I would say 5/10 overall', 5), ('Rating: 12/10', 10), ('no number', None)])
def test_reading_the_rating(text, want):
    assert code_studio.parse_rating(text) == want


def test_checks_run_the_project_command_in_the_copy(lab):
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    code_studio.update_project(lab.pid, checks=f'{sys.executable} -c "import os; print(sorted(os.listdir()))"')
    code_studio.run_checks(s['id'])
    r = wait(s['id'])
    assert r['check_state'] == 'passed' and "'step1.py'" in r['check_output']
    code_studio.update_project(lab.pid, checks=f'{sys.executable} -c "raise SystemExit(3)"')
    code_studio.run_checks(s['id'])
    assert wait(s['id'])['check_state'] == 'failed'
    code_studio.update_project(lab.pid, checks='')
    with pytest.raises(code_studio.CodeError, match='checks command'):
        code_studio.run_checks(s['id'])


def test_catch_up_conflicts_are_fixed_by_apex_then_kept(lab):
    lab.mode('claude', 'readme')
    s = wait(code_studio.start(lab.pid, 'Change the readme', 'claude')['id'])
    (lab.root / 'README.md').write_text('your line\n')                    # meanwhile, on main
    git(lab.root, 'commit', '-qam', 'mine')
    with pytest.raises(code_studio.CodeError, match='changed in the same places'):
        code_studio.keep(s['id'])
    assert git(lab.root, 'status', '--porcelain') == ''                   # the failed keep left nothing behind
    out = code_studio.catch_up(s['id'])
    assert out['caught_up'] == 'conflict' and out['conflicts'] == ['README.md']
    with pytest.raises(code_studio.CodeError, match='Resolve the conflicts'):
        code_studio.keep(s['id'])
    lab.mode('claude', 'resolve')
    code_studio.send(s['id'], 'Fix the conflicts')
    r = wait(s['id'])
    assert r['conflict'] == 0 and (Path(r['worktree']) / 'README.md').read_text() == 'resolved: both sides\n'
    code_studio.keep(s['id'])
    assert (lab.root / 'README.md').read_text() == 'resolved: both sides\n'
    assert code_studio.catch_up  # (kept: no more catching up)


def test_catch_up_without_conflicts(lab):
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    assert code_studio.catch_up(s['id'])['caught_up'] == 'already'
    (lab.root / 'other.txt').write_text('x\n')
    git(lab.root, 'add', '-A'); git(lab.root, 'commit', '-qm', 'other')
    assert code_studio.catch_up(s['id'])['caught_up'] == 'merged'
    assert (Path(s['worktree']) / 'other.txt').exists()
    assert [f['path'] for f in code_studio.changes(s['id'])['files']] == ['step1.py']   # yours aren't counted as its


def test_keep_refuses_to_overwrite_your_unsaved_changes(lab):
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    (lab.root / 'step1.py').write_text('mine, unsaved\n')
    with pytest.raises(code_studio.CodeError, match='unsaved changes in step1.py'):
        code_studio.keep(s['id'])
    assert (lab.root / 'step1.py').read_text() == 'mine, unsaved\n'


def test_unsaved_work_in_your_project_is_mentioned(lab):
    (lab.root / 'README.md').write_text('edited, not committed\n')
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    note = code_studio.events(s['id'])[1]
    assert note['kind'] == 'note' and 'Your 1 unsaved change in project is not in this session' in note['text']


def test_projects(lab, tmp_path):
    names = [p['name'] for p in code_studio.projects()]
    assert names[0] == 'Apex' and 'project' in names                       # Apex itself is always there
    plain = tmp_path / 'plain'; plain.mkdir()
    with pytest.raises(code_studio.CodeError, match='not a git project'):
        code_studio.add_project(str(plain))
    with pytest.raises(code_studio.CodeError, match='does not exist'):
        code_studio.add_project(str(tmp_path / 'nope'))
    with pytest.raises(code_studio.CodeError, match='already here'):
        code_studio.add_project(str(lab.root / '.'))
    empty = tmp_path / 'empty'; empty.mkdir(); git(empty, 'init', '-q')
    p = code_studio.add_project(str(empty))
    with pytest.raises(code_studio.CodeError, match='no commits yet'):
        code_studio.start(p['id'], 'x')
    assert code_studio.project(lab.pid)['checks'] == ''                    # nothing to detect in a bare readme


def test_validation(lab):
    for bad in (dict(prompt=''), dict(prompt='x', engine='api'), dict(prompt='x', mode='yolo'),
                dict(prompt='x' * (code_studio.MAX_PROMPT + 1))):
        with pytest.raises(code_studio.CodeError):
            code_studio.start(lab.pid, **{'engine': 'claude', 'mode': 'safe', **bad})


def test_restart_marks_running_sessions_interrupted(lab, monkeypatch):
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    code_studio._set(s['id'], turn_state='working', review_state='working')
    monkeypatch.setattr(code_studio, '_recovered', False)
    code_studio.init_db()
    r = code_studio.session(s['id'])
    assert r['turn_state'] == 'idle' and r['review_state'] == 'failed'
    assert code_studio.events(s['id'])[-1]['status'] == 'interrupted'


def test_overview(lab):
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    o = code_studio.overview()
    assert o['owner'] == 'Alex' and [p['id'] for p in o['plans']] == ['claude', 'chatgpt']
    assert all(p['ready'] for p in o['plans']) and o['default_engine'] == 'claude'
    assert o['week']['sessions'] >= 1 and o['week']['credits'] == 0
    assert o['sessions'][0]['id'] == s['id'] and o['sessions'][0]['files_changed'] == 1


# ---------------------------------------------------------------- the command lines

def test_command_lines_per_mode(tmp_path):
    safe = code_engines.command('claude', 'claude', tmp_path, 'safe')
    full = code_engines.command('claude', 'claude', tmp_path, 'full')
    review = code_engines.command('claude', 'claude', tmp_path, 'review')
    assert 'Bash' not in safe and 'Bash(python -m pytest:*)' in safe and '--strict-mcp-config' in safe
    assert 'Bash' in full and 'PowerShell' in full
    assert 'Write' not in review[review.index('--allowedTools'):review.index('--strict-mcp-config')]
    assert review[review.index('--disallowedTools') + 1:][:2] == ['Write', 'Edit']
    assert code_engines.command('chatgpt', 'codex', tmp_path, 'full')[-3:] == ['-c', 'sandbox_mode=danger-full-access', '-']
    for bad in ('x & calc', '../../x', 'a b'):
        with pytest.raises(ValueError):
            code_engines.command('claude', 'claude', tmp_path, 'safe', resume=bad)


def test_the_real_tools_error_lines_are_read():
    """Lines captured from the real Claude Code 2.1 and Codex 0.160."""
    real_claude = ('{"type":"result","subtype":"error_during_execution","duration_ms":0,"is_error":true,"num_turns":0,'
                   '"session_id":"01234567-89ab-cdef-0123-456789abcdef","total_cost_usd":0,"permission_denials":[],'
                   '"errors":["No conversation found with session ID: 01234567-89ab-cdef-0123-456789abcdef"]}')
    done = code_engines.parse('claude', real_claude, Path('.'), {})[-1]
    assert done['status'] == 'failed' and code_engines.RESUME_LOST.search(done['summary'])
    assert code_engines.parse('chatgpt', '{"type":"thread.started","thread_id":"01a1117d-1baa-7363"}', Path('.'), {}) == \
        [{'kind': 'session', 'id': '01a1117d-1baa-7363', 'model': ''}]
    assert code_engines.parse('chatgpt', '{"type":"error","message":"Reconnecting... 2/5 (stream disconnected)"}',
                              Path('.'), {})[0]['kind'] == 'note'
    assert code_engines.error_text('Error: thread/resume failed: no rollout found for thread id X (code -32600)\n\n'
                                   'Stack backtrace:\n   0: <unknown>') == \
        'Error: thread/resume failed: no rollout found for thread id X (code -32600)'


# ---------------------------------------------------------------- the API

@pytest.fixture
def api(lab, monkeypatch):
    import config
    from dashboard import code as route
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', '')
    app = FastAPI(); app.include_router(route.router)
    return TestClient(app), config


def test_api_round_trip(api, lab):
    client, _ = api
    o = client.get('/api/code').json()
    assert o['owner'] == 'Alex' and any(p['id'] == lab.pid for p in o['projects'])
    s = client.post('/api/code/sessions', json={'project_id': lab.pid, 'prompt': 'Add a step', 'engine': 'claude'}).json()
    wait(s['id'])
    feed = client.get(f"/api/code/sessions/{s['id']}/events").json()['events']
    assert feed[0]['kind'] == 'you' and feed[-1]['kind'] == 'done'
    later = client.get(f"/api/code/sessions/{s['id']}/events", params={'after': feed[-2]['id']}).json()['events']
    assert [e['id'] for e in later] == [feed[-1]['id']]
    one = client.get(f"/api/code/sessions/{s['id']}").json()
    assert one['changes']['files'][0]['path'] == 'step1.py'
    assert '+print(1)' in client.get(f"/api/code/sessions/{s['id']}/diff", params={'path': 'step1.py'}).text
    assert client.get(f"/api/code/sessions/{s['id']}/diff", params={'path': 'README.md'}).status_code == 400
    assert client.post(f"/api/code/sessions/{s['id']}/teleport", json={}).status_code == 404
    assert client.post('/api/code/sessions', json={'project_id': lab.pid, 'prompt': ''}).status_code == 400
    assert client.post('/api/code/sessions', json={'project_id': 'x', 'prompt': 'y'}).status_code == 400
    assert client.post(f"/api/code/sessions/{s['id']}/keep", json={}).json()['status'] == 'kept'


def test_only_the_owner_codes_and_only_from_the_page(api, lab, monkeypatch):
    client, config = api
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'master')               # a device token, not the owner
    assert client.get('/api/code').status_code == 403                     # even reading code is the owner's
    assert client.post('/api/code/sessions', json={'project_id': lab.pid, 'prompt': 'x'}).status_code == 403
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', '')
    assert client.post('/api/code/sessions', json={'project_id': lab.pid, 'prompt': 'x'},
                       headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/code/projects', json={'path': '/etc'}).status_code == 400


def test_nothing_to_keep_and_event_numbers_are_never_overwritten(lab):
    lab.mode('claude', 'review')                                       # a turn that changes no file
    s = wait(code_studio.start(lab.pid, 'Just look around', 'claude')['id'])
    assert code_studio.changes(s['id'])['files'] == [] and s['files_changed'] == 0
    with pytest.raises(code_studio.CodeError, match='Nothing to keep'):
        code_studio.keep(s['id'])
    with pytest.raises(code_studio.CodeError, match='Nothing to review'):
        code_studio.review(s['id'])
    code_studio.event(s['id'], 'note', id='not-a-number', text='x')     # data can't take the event's own number
    last = code_studio.events(s['id'])[-1]
    assert isinstance(last['id'], int) and last['text'] == 'x'


def test_the_plan_checker_covers_apex_code(lab, capsys):
    from scripts import work_plans_check
    for name in ('claude', 'codex'):
        lab.mode(name, 'check')
    assert work_plans_check.code_check('claude') and work_plans_check.code_check('chatgpt')
    out = capsys.readouterr().out
    assert out.count('PASS  a live coding step finished as done') == 2
    assert out.count('PASS  a follow-up continued the same conversation (done)') == 2
    resumed = lab.calls('codex')[-1]['argv']
    assert resumed[:2] == ['exec', 'resume'] and 'thread-codex-1' in resumed
    lab.mode('claude', 'edit')                                          # a plan that ignores the task fails the check,
    assert not work_plans_check.code_check('claude')                    # even straight after a passing one
    assert 'FAIL  a live coding step' in capsys.readouterr().out


def _lock(monkeypatch, name, times):
    """Windows holding a new file: opening it fails `times` times (forever if None)."""
    import pathlib
    real, seen = pathlib.Path.open, {'n': 0}
    def guarded(self, *a, **k):
        if self.name == name and (times is None or seen['n'] < times):
            seen['n'] += 1
            raise PermissionError(13, 'Permission denied')
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, 'open', guarded)
    return seen


def test_a_file_held_for_a_moment_is_waited_for(lab, monkeypatch):
    monkeypatch.setattr(code_studio, 'LOCK_WAIT', 5)
    seen = _lock(monkeypatch, 'step1.py', 3)
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    assert seen['n'] == 3 and s['files_changed'] == 1
    assert any(e['kind'] == 'checkpoint' for e in code_studio.events(s['id']))


def test_a_file_windows_keeps_locked_is_reported_not_a_crash(lab, monkeypatch):
    monkeypatch.setattr(code_studio, 'LOCK_WAIT', 0.6)
    _lock(monkeypatch, 'codex1.py', None)
    s = wait(code_studio.start(lab.pid, 'Add a codex file', 'chatgpt')['id'])
    feed = code_studio.events(s['id'])
    err = next(e for e in feed if e['kind'] == 'error')
    assert "Windows won't let Apex open what the plan wrote (codex1.py)" in err['text'] and '--only chatgpt' in err['text']
    assert not any(e['kind'] == 'checkpoint' for e in feed) and feed[-1]['kind'] == 'done'
    assert not s['working']


def test_the_code_check_reports_a_locked_file_calmly(lab, monkeypatch, capsys):
    from scripts import work_plans_check
    import pathlib
    lab.mode('codex', 'check')
    real = pathlib.Path.read_text
    def refuse(self, *a, **k):
        if self.name == 'apex-code-check.md':
            raise PermissionError(13, 'Permission denied')
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, 'read_text', refuse)
    monkeypatch.setattr(work_plans_check.time, 'sleep', lambda s: None)
    assert work_plans_check.code_check('chatgpt') is False                    # not a crash
    out = capsys.readouterr().out
    assert 'Windows blocks reading the file it wrote' in out and 'Windows would not let Apex read it' in out


def test_on_windows_codex_is_told_to_write_files_with_its_edit_tool(lab):
    assert work_engines.codex_prompt('Do it', windows=False) == 'Do it'
    told = work_engines.codex_prompt('Do it', windows=True)
    assert told.endswith('\n\nDo it') and 'apply_patch' in told and 'Set-Content' in told


def test_codex_gets_the_windows_note_in_code_sessions_not_reviews(lab, monkeypatch):
    real = work_engines.codex_prompt
    monkeypatch.setattr(work_engines, 'codex_prompt', lambda p, windows=None: real(p, windows=True))   # as on Windows
    s = wait(code_studio.start(lab.pid, 'Add a codex file', 'chatgpt')['id'])
    assert lab.calls('codex')[-1]['stdin'].startswith(work_engines.WINDOWS_CODEX_NOTE)
    lab.mode('codex', 'review')
    code_studio.review(s['id'], engine='chatgpt'); wait(s['id'])
    assert not lab.calls('codex')[-1]['stdin'].startswith(work_engines.WINDOWS_CODEX_NOTE)   # read-only: nothing to write



# ---------------------------------------------------------------- the pro features

def test_edits_show_their_diff_with_real_line_numbers_and_failed_edits_say_so(lab):
    lab.mode('claude', 'editor')
    s = wait(code_studio.start(lab.pid, 'Edit the readme', 'claude')['id'])
    feed = code_studio.events(s['id'])
    f = next(e for e in feed if e['kind'] == 'file')
    assert f['path'] == 'README.md' and f['diff'] == '@@ -1,1 +1,1 @@\n-hello\n+hello there'
    assert next(e for e in feed if e['kind'] == 'error')['text'].startswith("Couldn't change nope.py: File does not exist.")


def test_live_text_streams_then_gives_way_to_the_stored_message(lab):
    sid = 99
    code_studio._feed_live(sid, {'kind': 'delta', 'text': 'Work'})
    code_studio._feed_live(sid, {'kind': 'delta', 'text': 'ing'})
    code_studio._feed_live(sid, {'kind': 'live', 'ref': 'c1', 'output': 'running…'})
    lv = code_studio.live(sid)
    assert lv['text'] == 'Working' and lv['outputs'] == {'c1': 'running…'}
    code_studio._feed_live(sid, {'kind': 'text', 'text': 'Working'})
    code_studio._feed_live(sid, {'kind': 'result', 'id': 'c1'})
    v = code_studio.live(sid)['v']
    assert code_studio.live(sid)['text'] == '' and code_studio.live(sid)['outputs'] == {}
    code_studio._live.pop(sid)                                          # a step ends; the next one starts
    assert code_studio.live(sid)['v'] > v                               # versions only go up: the page never goes back
    assert code_engines.parse('claude', json.dumps({'type': 'stream_event', 'event': {'type': 'content_block_delta',
        'delta': {'type': 'text_delta', 'text': 'Hi'}}}), Path('.'), {}) == [{'kind': 'delta', 'text': 'Hi'}]
    assert code_engines.parse('chatgpt', json.dumps({'type': 'item.updated', 'item': {'id': 'c9', 'type': 'command_execution',
        'aggregated_output': 'line 1\n'}}), Path('.'), {}) == [{'kind': 'live', 'ref': 'c9', 'output': 'line 1\n'}]
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    assert not any(e['kind'] == 'delta' for e in code_studio.events(s['id']))      # chunks are never stored


def test_model_effort_and_plan_reach_the_tools(lab):
    s = wait(code_studio.start(lab.pid, 'Plan a step', 'claude', model='opus', effort='high', plan=True)['id'])
    call = lab.calls('claude')[-1]
    argv = call['argv']
    assert argv[argv.index('--model') + 1] == 'opus' and argv[argv.index('--effort') + 1] == 'high'
    assert argv[argv.index('--permission-mode') + 1] == 'plan' and '--include-partial-messages' in argv
    assert call['stdin'].startswith(code_engines.PLAN_ONLY)
    assert code_studio.session(s['id'])['model'] == 'opus'                     # kept for the next message
    code_studio.send(s['id'], 'Go ahead with that plan.')
    wait(s['id'])
    argv = lab.calls('claude')[-1]['argv']
    assert argv[argv.index('--permission-mode') + 1] == 'acceptEdits' and argv[argv.index('--model') + 1] == 'opus'
    code_studio.send(s['id'], 'Finish on ChatGPT', engine='chatgpt', model='', effort='max', plan=True)
    wait(s['id'])
    argv = lab.calls('codex')[-1]['argv']
    assert 'sandbox_mode=read-only' in argv and 'model_reasoning_effort=high' in argv and '-m' not in argv
    for bad in (dict(model='opus; rm -rf'), dict(effort='ludicrous')):
        with pytest.raises(code_studio.CodeError):
            code_studio.send(s['id'], 'x', **bad)


def test_allow_once_and_always_like_claude_codes_prompt(lab):
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    blocked = next(e for e in code_studio.events(s['id']) if e['kind'] == 'blocked')
    assert blocked['command'] == 'rm -rf build'
    code_studio.allow(s['id'], blocked['command'])
    wait(s['id'])
    argv = lab.calls('claude')[-1]['argv']
    assert 'Bash(rm -rf build)' in argv and 'Bash(rm -rf build:*)' not in argv
    assert '`rm -rf build`' in lab.calls('claude')[-1]['stdin']
    code_studio.allow(s['id'], 'npm run build', always=True)
    wait(s['id'])
    code_studio.send(s['id'], 'Later message'); wait(s['id'])
    assert 'Bash(npm run build:*)' in lab.calls('claude')[-1]['argv']           # this project, from now on
    assert json.loads(code_studio.project(lab.pid)['allow']) == ['npm run build']
    code_studio.forget_allowed(lab.pid, 'npm run build')
    code_studio.send(s['id'], 'Again'); wait(s['id'])
    assert 'Bash(npm run build:*)' not in lab.calls('claude')[-1]['argv']
    for bad in ('rm (x)', 'two\nlines', ''):
        with pytest.raises(code_studio.CodeError):
            code_studio.allow(s['id'], bad)
    # Codex can't allow one command: that message gets full access instead.
    code_studio.send(s['id'], 'x', engine='chatgpt', allow=['make']); wait(s['id'])
    assert 'sandbox_mode=danger-full-access' in lab.calls('codex')[-1]['argv']


def test_at_mentions_point_the_plan_at_files(lab):
    s = wait(code_studio.start(lab.pid, 'Look at @README.md and @nope.txt', 'claude')['id'])
    call = lab.calls('claude')[-1]
    assert call['stdin'].startswith('Files the owner pointed at (read these first): README.md\n')
    assert code_studio.events(s['id'])[0]['files'] == ['README.md']


def test_your_own_terminal_in_the_sessions_copy(lab):
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    code_studio.terminal(s['id'], 'ls && echo done-here')
    end = time.time() + 10
    while code_studio.session(s['id'])['terminal'] and time.time() < end:
        time.sleep(0.05)
    done = [e for e in code_studio.events(s['id']) if e['kind'] == 'term_done'][-1]
    assert done['exit_code'] == 0 and 'step1.py' in done['output'] and 'done-here' in done['output']
    code_studio.terminal(s['id'], 'exit 3')
    end = time.time() + 10
    while code_studio.session(s['id'])['terminal'] and time.time() < end:
        time.sleep(0.05)
    assert [e for e in code_studio.events(s['id']) if e['kind'] == 'term_done'][-1]['exit_code'] == 3
    code_studio.terminal(s['id'], 'sleep 30')
    with pytest.raises(code_studio.CodeError, match='still running'):
        code_studio.terminal(s['id'], 'ls')
    assert code_studio.stop(s['id'])
    with pytest.raises(code_studio.CodeError):
        code_studio.terminal(s['id'], '')


def test_file_tree_and_viewer_stay_inside_the_project(lab):
    (lab.root / 'logo.bin').write_bytes(b'\x00\x01binary')
    (lab.root / '.gitignore').write_text('secret.env\n'); (lab.root / 'secret.env').write_text('KEY=1')
    git(lab.root, 'add', 'logo.bin', '.gitignore'); git(lab.root, 'commit', '-qm', 'more')
    t = code_studio.tree(pid=lab.pid)
    assert 'README.md' in t['files'] and 'logo.bin' in t['files'] and 'secret.env' not in t['files']
    f = code_studio.read_file('README.md', pid=lab.pid)
    assert f['text'] == 'hello\n' and f['lang'] == 'md'
    assert code_studio.read_file('logo.bin', pid=lab.pid)['binary'] is True
    for bad in ('secret.env', '../../etc/passwd', '/etc/passwd'):
        with pytest.raises(code_studio.CodeError):
            code_studio.read_file(bad, pid=lab.pid)
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    assert 'step1.py' in code_studio.tree(s['id'])['files']                         # the session's copy
    assert code_studio.read_file('step1.py', sid=s['id'])['lang'] == 'python'


def test_history_of_checkpoints_and_their_diffs(lab):
    sid = code_studio.start(lab.pid, 'Step one', 'claude')['id']; wait(sid)
    code_studio.send(sid, 'Step two'); wait(sid)
    h = code_studio.history(sid)
    assert len(h) == 2 and h[0]['files'] == 1 and not h[0]['undone']
    assert '+print(2)' in code_studio.commit_diff(sid, h[0]['sha'])
    with pytest.raises(code_studio.CodeError):
        code_studio.commit_diff(sid, 'HEAD~5')
    code_studio.undo(sid)
    assert code_studio.history(sid)[0]['undone']


def test_keep_and_push(lab, tmp_path):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    git(lab.root, 'remote', 'add', 'origin', str(remote)); git(lab.root, 'push', '-q', '-u', 'origin', 'main')
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    code_studio.keep(s['id'], push=True)
    pushed = code_studio.events(s['id'])[-1]
    assert pushed['kind'] == 'pushed' and pushed['ok'] and pushed['text'] == 'Pushed to GitHub.'
    assert 'step1.py' in subprocess.run(['git', 'ls-tree', '-r', '--name-only', 'main'], cwd=remote,
                                        capture_output=True, text=True).stdout
    git(lab.root, 'remote', 'set-url', 'origin', str(tmp_path / 'gone.git'))
    s2 = wait(code_studio.start(lab.pid, 'Another step', 'claude')['id'])
    kept = code_studio.keep(s2['id'], push=True)
    assert kept['status'] == 'kept' and not code_studio.events(s2['id'])[-1]['ok']    # kept, even if the push fails


def test_the_stream_sends_steps_and_live_text(api, lab):
    client, _ = api
    s = wait(code_studio.start(lab.pid, 'Add a step', 'claude')['id'])
    code_studio._feed_live(s['id'], {'kind': 'delta', 'text': 'still typing'})
    lines = [json.loads(l) for l in client.get(f"/api/code/sessions/{s['id']}/stream", params={'once': True}).text.splitlines()]
    kinds = [l['t'] for l in lines]
    assert kinds.count('live') == 1 and kinds.index('live') == len(kinds) - 1
    assert [l['kind'] for l in lines if l['t'] == 'event'][0] == 'you'
    assert lines[-1]['text'] == 'still typing'
    later = client.get(f"/api/code/sessions/{s['id']}/stream", params={'once': True, 'after': lines[-2]['id']}).text
    assert [json.loads(l)['t'] for l in later.splitlines()] == ['live']
    assert client.get(f"/api/code/sessions/{s['id']}/tree").json()['files']
    assert client.get(f"/api/code/sessions/{s['id']}/file", params={'path': '../x'}).status_code == 400
    assert client.post(f"/api/code/sessions/{s['id']}/terminal", json={'command': 'echo hi'},
                       headers={'Origin': 'https://evil.example'}).status_code == 403
