"""Apex's minimum complete demonstration (roadmap, Phase 0).

    python scripts/apex_demo.py              # fixture: scripted model and app, no network, no cost
    python scripts/apex_demo.py --live       # your configured model, and your connected apps if you name them
    python scripts/apex_demo.py --fault leak # prove a check can fail (fixture only): leak, repeat-write

The roadmap's acceptance test, run end to end. Every step says pass, fail,
unknown or skipped, with its evidence:

  1. Create project A, reference a source file, generate action items,
     produce a document, save the decision and next step.
  2. Restart (a new process) and resume project A from what was saved.
  3. Repeat in project B and check the two don't leak into each other.
  4. One app read, then one write you authorize, with its receipt.
  5. Kill a task mid-action, then recover without repeating the action.

Each phase runs in its own process, so "restart" is a real restart. The demo
always uses its own database in its own folder (~/.apex/demo/<time>/), so
your real memory, projects and board are never touched. Only the app steps
reach anything real, and only in --live, when you name the tools.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS, FAIL, UNKNOWN, SKIPPED = 'pass', 'fail', 'unknown', 'skipped'
LEDGER_LINE = 'INVOICE-42 sent'
SOURCE = """# Kickoff notes: garage workshop

- The workbench needs a second power strip before the drill press arrives.
- Alex wants the 3D printer moved away from the window (sun warps prints).
- Budget for shelving is $120; Celine prefers the white steel ones.
- Decide by Friday whether the compressor lives in the garage or the shed.
- Nobody has checked if the garage outlet is on its own circuit.
"""
PROJECT_B = dict(brief='Plan a weekend trip to the Cedars.', decisions='Leave Saturday 7am; take the Jeep.',
                 artifacts='', next_step='Check the road report on Friday evening.')
ACTION_MARK = 'APEX-DEMO-ACTION-ITEMS'


def step(name, status, evidence, **extra):
    return dict(step=name, status=status, evidence=evidence, **extra)


# ---------------------------------------------------------------- fixtures

class ScriptedClient:
    """Stands in for a model in fixture mode. It answers the demo's own
    prompts with fixed replies; it is not a measure of any model."""

    def __init__(self):
        self.messages = SimpleNamespace(create=self.create)

    @staticmethod
    def _reply(blocks, stop):
        content = [SimpleNamespace(**b) for b in blocks]
        return SimpleNamespace(content=content, stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=200, output_tokens=60))

    def create(self, **kw):
        system, messages = kw.get('system', ''), kw['messages']
        if ACTION_MARK in str(system):
            source = messages[0]['content']
            items = [line[2:].strip() for line in source.splitlines() if line.startswith('- ')][:4]
            doc = '# Workshop plan\n\n' + '\n'.join(f'{i+1}. {t}' for i, t in enumerate(items)) + \
                  '\n\nFirst, confirm the outlet circuit, because the drill press and the compressor both depend on it.'
            return self._reply([dict(type='text', text=json.dumps(dict(action_items=items, document=doc)))], 'end_turn')
        first = json.loads(messages[0]['content'])
        ledger = re.search(r'`([^`]+ledger\.txt)`', first['task']).group(1)
        if 'Summarize the actual outcome' in system:
            return self._reply([dict(type='text', text='Summary: see the specialist evidence.')], 'end_turn')
        if len(messages) > 1:                     # a tool result came back
            return self._reply([dict(type='text', text='The ledger already holds the entry, so nothing was appended.')], 'end_turn')
        if 'explicitly reviewed continuation' in first.get('project_context', '') and os.getenv('APEX_DEMO_FAULT') != 'repeat-write':
            return self._reply([dict(type='tool_use', id='t2', name='read_file', input=dict(path=ledger))], 'tool_use')
        return self._reply([dict(type='tool_use', id='t1', name='append_file',
                                 input=dict(path=ledger, content=LEDGER_LINE + '\n'))], 'tool_use')


class FakeAppService:
    """A pretend notes app for fixture mode: one read tool and one write tool,
    behind Apex's real app gate (`apps.call`, `mcp_policy`)."""

    TOOLS = [dict(name='app__demo__DEMO_LIST_NOTES', toolkit='demo', slug='DEMO_LIST_NOTES', tags=['readOnlyHint'],
                  description='List notes.', input_schema={'type': 'object', 'properties': {}}),
             dict(name='app__demo__DEMO_CREATE_NOTE', toolkit='demo', slug='DEMO_CREATE_NOTE', tags=[],
                  description='Create a note.', input_schema={'type': 'object', 'properties': {'text': {'type': 'string'}},
                                                              'required': ['text']}),
             dict(name='app__demo__DEMO_DELETE_NOTES', toolkit='demo', slug='DEMO_DELETE_NOTES', tags=['destructiveHint'],
                  description='Delete every note.', input_schema={'type': 'object', 'properties': {}})]

    def __init__(self, path):
        self.path = path

    def notes(self):
        return json.loads(self.path.read_text()) if self.path.exists() else ['Buy shelving']

    def install(self, apps):
        apps.tools = lambda: list(self.TOOLS)
        apps._state = lambda: dict(session_id='demo', apps={'demo': dict(enabled=True, status='connected')})
        apps._refresh = lambda data: None
        apps._key = lambda: 'demo'

        def request(method, path, body=None, **_):
            notes = self.notes()
            if body['tool_slug'] == 'DEMO_CREATE_NOTE':
                notes.append(body['arguments']['text'])
            elif body['tool_slug'] == 'DEMO_DELETE_NOTES':
                notes = []
            self.path.write_text(json.dumps(notes))
            return dict(data=dict(notes=notes), log_id='log_' + hashlib.sha256(json.dumps(notes).encode()).hexdigest()[:12])
        apps._request = request


def install_model_fixture():
    from agent import provider
    provider.get_client = lambda model: ScriptedClient()


# ---------------------------------------------------------------- phases

def _active_context():
    from agent import board
    return board.get_board().workspace_context()


def _new_project(name):
    from agent import board_workspaces
    board_workspaces.load_initial()
    made = board_workspaces.create(name, False, _active_context())
    board_workspaces.switch(made['id'], _active_context())
    return made['id']


def _model_call(system, user):
    import config
    from agent import provider, telemetry
    client = provider.get_client(config.AGENT_MODEL)
    resp = telemetry.create(client, call_site='demo/action-items', model=config.AGENT_MODEL, max_tokens=1500,
                            system=system, messages=[{'role': 'user', 'content': user}])
    return ''.join(b.text for b in resp.content if getattr(b, 'type', '') == 'text')


def phase_build(d, args):
    from agent import continuity, documents
    out = []
    source = Path(args['source'])
    text = source.read_text(encoding='utf-8')
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    a = _new_project('Demo A · workshop')
    out.append(step('Create project A', PASS, f'Workspace {a[:8]} created and made active.'))
    out.append(step('Reference source material', PASS, f'{source.name} (sha256 {digest}), {len(text)} characters.'))

    system = (ACTION_MARK + '\nRead the notes. Reply with JSON only: {"action_items": [3-6 short imperative items '
              'taken from the notes], "document": "a short markdown plan that uses those items"}.')
    try:
        raw = _model_call(system, text)
        parsed = json.loads(raw[raw.find('{'):raw.rfind('}') + 1])
        items = [str(i).strip() for i in parsed['action_items'] if str(i).strip()]
        body = str(parsed['document']).strip()
    except Exception as exc:
        out.append(step('Generate action items', FAIL, f'The model reply could not be used: {exc}'[:400]))
        return out
    ok = 3 <= len(items) <= 6
    out.append(step('Generate action items', PASS if ok else FAIL, f'{len(items)} items: ' + '; '.join(items)[:500]))

    documents.init_db()
    doc = documents.create('Workshop plan (demo)', body)
    stored = documents.get(doc['id'])
    ok = bool(stored) and stored['content'] == body and len(body) >= 80
    out.append(step('Produce a document', PASS if ok else FAIL, f"Document #{doc['id']}, {len(body)} characters, read back intact: {ok}."))

    handoff = dict(brief='Set up the garage workshop.', decisions='Shelving: white steel, budget $120.',
                   artifacts=f"Document #{doc['id']}; source {source.name} sha256 {digest}",
                   next_step=items[0][:300] if items else 'Check the outlet circuit.')
    saved = continuity.save_project(a, handoff, continuity.project(a)['revision'])
    continuity.snapshot('dashboard:demo-a')                     # this chat now belongs to project A
    out.append(step('Save the decision and next step', PASS, f"Handoff revision {saved['revision']}: next step '{handoff['next_step'][:120]}'."))

    b = _new_project('Demo B · trip')
    handoff_b = dict(PROJECT_B)
    if os.getenv('APEX_DEMO_FAULT') == 'leak':          # B's handoff wrongly carries A's decision
        handoff_b['decisions'] += ' ' + handoff['decisions']
    continuity.save_project(b, handoff_b, continuity.project(b)['revision'])
    continuity.snapshot('dashboard:demo-b')
    out.append(step('Create project B', PASS, f'Workspace {b[:8]} created with its own handoff; it is now the active board.'))
    (d / 'state.json').write_text(json.dumps(dict(a=a, b=b, doc=doc['id'], handoff_a=handoff, items=items)))
    return out


def phase_resume(d, args):
    """A fresh process: nothing in memory, only what was saved."""
    from agent import continuity, documents
    state = json.loads((d / 'state.json').read_text())
    out = []
    pa = continuity.project(state['a'])
    expected = state['handoff_a']
    same = all(pa['data'][k] == expected[k] for k in expected)
    doc = documents.get(state['doc'])
    out.append(step('Restart and resume project A', PASS if same and doc else FAIL,
                    f"New process {os.getpid()}. Decision, next step and artifacts match: {same}; "
                    f"document #{state['doc']} still there: {bool(doc)}."))

    with continuity.turn('dashboard:demo-a'):
        prompt_a = continuity.prompt()
    with continuity.turn('dashboard:demo-b'):
        prompt_b = continuity.prompt()
    leaks = [w for w in (expected['decisions'], expected['next_step']) if w in prompt_b] + \
            [w for w in (PROJECT_B['decisions'], PROJECT_B['next_step']) if w in prompt_a]
    pinned = expected['next_step'] in prompt_a
    out.append(step('Projects stay separate', PASS if pinned and not leaks else FAIL,
                    f"A's chat resumes A while B is the active board: {pinned}. Text shared between them: {leaks or 'none'}."))
    shared = len(documents.list_documents())
    out.append(step('Documents are per project', UNKNOWN,
                    f'Not built: documents are one shared list ({shared} here). Only the project handoff links '
                    'a document to its project, so another project can still find it by searching documents.'))
    return out


def _policy_name(tool):
    """The name Apex's gate uses for an app tool (agent/apps.call)."""
    _, toolkit, slug = tool.split('__', 2)
    return f"mcp__apps_{toolkit}__{slug.removeprefix(toolkit.upper() + '_').lower()}"


def _confirm(args, d):
    """Apex's own write gate asks this. Only the one write named on the command
    line can be granted, and in --live only when you type YES."""
    tool = args.get('write_tool')
    planned = json.dumps(args.get('write_args') or {}, sort_keys=True)
    log = d / 'authorizations.json'

    def ask(reason):
        granted = bool(tool) and reason.startswith(_policy_name(tool) + ' ')
        if granted and args['mode'] == 'live':
            print(f'\n{reason}\nTool: {tool}\nArguments: {planned}', flush=True)
            granted = input('Type YES to send exactly this, anything else to refuse: ').strip() == 'YES'
        entries = json.loads(log.read_text()) if log.exists() else []
        entries.append(dict(reason=reason, granted=granted, at=time.time()))
        log.write_text(json.dumps(entries))
        return granted
    return ask


def phase_apps(d, args):
    from agent import apps, mcp_policy
    out = []
    if args['mode'] == 'fixture':
        FakeAppService(d / 'fake-notes.json').install(apps)
    mcp_policy.set_confirm_fn(_confirm(args, d))
    available = {t['name'] for t in apps.tools()}
    if not args.get('read_tool'):
        out.append(step('App read', UNKNOWN, f"No read tool named. Connected tools: {len(available)}. "
                        "Run with --read-tool and --read-args (see docs/DEMO.md)."))
    else:
        try:
            result = apps.call(args['read_tool'], args.get('read_args') or {})
            ok = not str(result).startswith('[') and 'log_id' in str(result)
            out.append(step('App read', PASS if ok else FAIL, f"{args['read_tool']}: {str(result)[:300]}"))
        except Exception as exc:
            out.append(step('App read', FAIL, f"{args['read_tool']}: {exc}"[:400]))
    if not args.get('write_tool'):
        out.append(step('Authorized app write with receipt', UNKNOWN, 'No write tool named, so nothing was sent.'))
        return out
    if args['mode'] == 'fixture':              # a write nobody named must be refused by the gate
        result = apps.call('app__demo__DEMO_DELETE_NOTES', {})
        blocked = str(result).startswith('[MCP blocked]')
        out.append(step('Unauthorized app write is refused', PASS if blocked else FAIL,
                        f"DEMO_DELETE_NOTES, never authorized: {str(result)[:200]}"))
    try:
        result = apps.call(args['write_tool'], args.get('write_args') or {})
    except Exception as exc:
        out.append(step('Authorized app write with receipt', FAIL, f'{exc}'[:400]))
        return out
    if str(result).startswith('[MCP blocked]'):
        out.append(step('Authorized app write with receipt', SKIPPED, 'You did not authorize it, so nothing was sent: ' + str(result)[:300]))
        return out
    receipt = json.loads(result).get('log_id')
    observed = None
    if args.get('check_tool'):
        seen = apps.call(args['check_tool'], args.get('check_args') or {})
        observed = args.get('expect', '') in str(seen)
    status = PASS if receipt and observed is not False else FAIL
    if receipt and observed is None:
        status = UNKNOWN
    out.append(step('Authorized app write with receipt', status,
                    f"Receipt (provider log) {receipt}. Effect observed by a second read: "
                    f"{'not checked (add --check-tool and --expect)' if observed is None else observed}."))
    return out


class DemoAgent:
    def _all_tools(self):
        from agent.core import TOOLS
        return TOOLS


def _wait(team, rid, seconds=180):
    end = time.time() + seconds
    while time.time() < end:
        run = team.get(rid)
        if run and run['status'] not in ('queued', 'running', 'stopping', 'verifying'):
            return run
        time.sleep(0.2)
    return team.get(rid)


def phase_crash(d, args):
    """Start a task whose one action is a write, then hang right after the write
    lands and before Apex records its result. The parent kills this process
    there: the classic "did it go through?" moment."""
    import config
    from agent import core, team
    install_model_fixture()                   # the action itself is scripted in both modes
    real = core._execute_tool

    def write_then_hang(name, inputs):
        result = real(name, inputs)
        (d / 'crash.ready').write_text(result)
        time.sleep(3600)
    core._execute_tool = write_then_hang
    ledger = d / 'ledger.txt'
    rid = team.submit(dict(id='demo_crash_' + hashlib.sha256(str(d).encode()).hexdigest()[:20],
                     task=f'Append exactly one line `{LEDGER_LINE}` to `{ledger}`. It must appear once.',
                     roles=['coder'], models={'coder': config.AGENT_MODEL, 'apex': config.AGENT_MODEL}, budget_usd=0.05),
                DemoAgent())['id']
    _wait(team, rid, 120)                     # only returns if the write never came; the parent then reports that


def phase_recover(d, args):
    from agent import task_recovery, team
    if args['mode'] == 'fixture':
        install_model_fixture()
    out = []
    rid = 'demo_crash_' + hashlib.sha256(str(d).encode()).hexdigest()[:20]
    run = team.get(rid)                       # the first read after the kill runs Apex's restart sweep
    unknown = [e for s in run['steps'] for e in s['evidence'] if e['status'] == 'outcome_unknown']
    out.append(step('Interrupted action is flagged, not assumed', PASS if run['status'] == 'interrupted' and len(unknown) == 1 else FAIL,
                    f"Task status '{run['status']}'; actions with an unknown outcome: {len(unknown)} ({unknown[0]['tool'] if unknown else '-'})."))
    try:
        task_recovery.continue_run(rid, 'Finish the ledger task.', 0.05, DemoAgent())
        refused = False
    except ValueError:
        refused = True
    out.append(step('No continuing without a review', PASS if refused else FAIL,
                    'Continuing before the uncertain action was checked was refused.' if refused else 'It continued without a review.'))
    ledger = d / 'ledger.txt'
    before = ledger.read_text().count(LEDGER_LINE) if ledger.exists() else 0
    keys = {f'{i}:{j}' for i, s in enumerate(run['steps']) for j, e in enumerate(s['evidence']) if e['status'] == 'outcome_unknown'}
    observed = (f'Checked {ledger.name}: the line is there {before} time(s), so the append happened before the crash.'
                if before else f'Checked {ledger.name}: the line is not there, so the append did not happen.')
    task_recovery.review(rid, 'The task was killed mid-action. I inspected the ledger file directly before continuing.',
                         {k: observed for k in keys})
    cont = task_recovery.continue_run(rid, f'Make sure `{ledger}` holds the line `{LEDGER_LINE}` exactly once. '
                                      'Inspect it first; do not append if it is already there.', 0.05, DemoAgent())
    done = _wait(team, cont['id'])
    after = ledger.read_text().count(LEDGER_LINE) if ledger.exists() else 0
    tools = [e['tool'] for s in done['steps'] for e in s['evidence']]
    out.append(step('Recover without repeating the action', PASS if after == 1 and done['status'] == 'done' else FAIL,
                    f"Continuation '{done['status']}', tools used: {tools or 'none'}. The line appears {after} time(s) "
                    f"(was {before} before recovery)."))
    return out


PHASES = dict(build=phase_build, resume=phase_resume, apps=phase_apps, crash=phase_crash, recover=phase_recover)


def run_phase(name, d):
    from agent import longterm, mcp_policy
    if Path(longterm.DB_PATH).resolve().parent != d.resolve():
        raise SystemExit(f'Refusing to run: the database is {longterm.DB_PATH}, not the demo folder.')
    longterm.init_db()                        # what Apex's startup does, on the demo's own database
    mcp_policy.init_db()
    args = json.loads((d / 'args.json').read_text())
    if args['mode'] == 'fixture' and name in ('build',):
        install_model_fixture()
    try:
        results = PHASES[name](d, args)
    except Exception as exc:                  # a crashed phase is a failed step, with its reason
        results = [step(f'{name} phase', FAIL, f'{type(exc).__name__}: {exc}'[:500])]
    (d / f'{name}.json').write_text(json.dumps(results))


# ---------------------------------------------------------------- driver

def _env(d, mode, fault=None):
    env = dict(os.environ, DB_PATH=str(d / 'apex.db'), VAULT_PATH=str(d / 'vault'), PYTHONIOENCODING='utf-8')
    env.pop('APEX_DEMO_FAULT', None)
    if fault:
        env['APEX_DEMO_FAULT'] = fault
    if mode == 'fixture':
        env.setdefault('ANTHROPIC_API_KEY', 'sk-ant-placeholder-for-ci-tests-only')
        env['ANTHROPIC_API_KEY'] = env['ANTHROPIC_API_KEY'] or 'sk-ant-placeholder-for-ci-tests-only'
        env['AGENT_MODEL'] = 'claude-opus-5'
    return env


def _child(name, d, mode, fault=None, timeout=600):
    cmd = [sys.executable, str(Path(__file__).resolve()), '--phase', name, '--dir', str(d)]
    proc = subprocess.run(cmd, env=_env(d, mode, fault), timeout=timeout, cwd=str(ROOT))
    path = d / f'{name}.json'
    if not path.exists():
        return [step(f'{name} phase', FAIL, f'The phase exited with code {proc.returncode} and wrote no result.')]
    return json.loads(path.read_text())


def _crash(d, mode, fault=None):
    cmd = [sys.executable, str(Path(__file__).resolve()), '--phase', 'crash', '--dir', str(d)]
    proc = subprocess.Popen(cmd, env=_env(d, mode, fault), cwd=str(ROOT))
    ready = d / 'crash.ready'
    end = time.time() + 120
    while time.time() < end and not ready.exists() and proc.poll() is None:
        time.sleep(0.1)
    landed = ready.exists()
    proc.kill()
    proc.wait(timeout=30)
    return [step('Interrupt a task mid-action', PASS if landed else FAIL,
                 f'The task process was killed right after its write ({ready.read_text()[:120] if landed else "the write never happened"}) '
                 'and before Apex recorded the result.')]


def report(d, mode, results):
    stamp = time.strftime('%Y-%m-%d %H:%M')
    counts = {s: sum(r['status'] == s for r in results) for s in (PASS, FAIL, UNKNOWN, SKIPPED)}
    lines = [f'# Apex demonstration, {stamp} ({mode})', '',
             'Fixture mode: the model and the app are scripted stand-ins, so this proves the plumbing, not a model or an account.'
             if mode == 'fixture' else 'Live mode: your configured model; the app steps reach your real connected app if you named tools.',
             '', '| # | Step | Result | Evidence |', '| --- | --- | --- | --- |']
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | {r['step']} | **{r['status'].upper()}** | {r['evidence'].replace('|', '/')} |")
    lines += ['', ' · '.join(f'{k}: {v}' for k, v in counts.items()), '']
    (d / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    (d / 'report.json').write_text(json.dumps(dict(mode=mode, at=stamp, counts=counts, steps=results), indent=1))
    return '\n'.join(lines), counts


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--live', action='store_true', help='use your configured model instead of the scripted one')
    p.add_argument('--out', help='folder for the demo database and report (default ~/.apex/demo/<time>)')
    p.add_argument('--source', help='a text or markdown file to use as the source material')
    p.add_argument('--read-tool'); p.add_argument('--read-args', default='{}')
    p.add_argument('--write-tool'); p.add_argument('--write-args', default='{}')
    p.add_argument('--check-tool'); p.add_argument('--check-args', default='{}')
    p.add_argument('--expect', default='', help='text the check read must contain after the write')
    p.add_argument('--fault', choices=('leak', 'repeat-write'), help='fixture only: break one thing on purpose and watch its check fail')
    p.add_argument('--phase', help=argparse.SUPPRESS); p.add_argument('--dir', help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.phase:
        return run_phase(a.phase, Path(a.dir))

    mode = 'live' if a.live else 'fixture'
    if a.fault and a.live:
        p.error('--fault only works without --live')
    d = Path(a.out or Path.home() / '.apex' / 'demo' / time.strftime('%Y%m%d-%H%M%S')).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    source = Path(a.source).expanduser().resolve() if a.source else d / 'kickoff-notes.md'
    if not a.source:
        source.write_text(SOURCE, encoding='utf-8')
    args = dict(mode=mode, source=str(source), read_tool=a.read_tool, read_args=json.loads(a.read_args),
                write_tool=a.write_tool, write_args=json.loads(a.write_args), check_tool=a.check_tool,
                check_args=json.loads(a.check_args), expect=a.expect)
    if mode == 'fixture' and not a.read_tool:
        args.update(read_tool='app__demo__DEMO_LIST_NOTES', write_tool='app__demo__DEMO_CREATE_NOTE',
                    write_args={'text': 'Order white steel shelving'}, check_tool='app__demo__DEMO_LIST_NOTES',
                    expect='Order white steel shelving')
    (d / 'args.json').write_text(json.dumps(args))
    print(f'Apex demonstration ({mode}) in {d}', flush=True)

    results = []
    for name in ('build', 'resume', 'apps'):
        print(f'· {name}', flush=True)
        results += _child(name, d, mode, a.fault)
    print('· interrupt', flush=True)
    results += _crash(d, mode, a.fault)
    print('· recover', flush=True)
    results += _child('recover', d, mode, a.fault)
    text, counts = report(d, mode, results)
    print('\n' + text + f'\nReport: {d / "report.md"}')
    return 1 if counts[FAIL] else 0


if __name__ == '__main__':
    sys.exit(main() or 0)
