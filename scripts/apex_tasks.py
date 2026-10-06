"""Apex's task suite: representative tasks, checked by rules, with their cost.

    python scripts/apex_tasks.py                    # fixture: scripted model, no cost
    python scripts/apex_tasks.py --live             # your AGENT_MODEL
    python scripts/apex_tasks.py --live --model deepseek-flash
    python scripts/apex_tasks.py --history          # every past run, side by side
    python scripts/apex_tasks.py --fault ungrounded # prove a check can fail (fixture only)

Phase 2's exit evidence in the environment roadmap: "two representative tasks
complete end to end with inspectable evidence and recorded cost". Each task
runs through Apex's own bounded task runner (agent/team.py), the same one the
Constellation tab uses, with its tool gates and spending cap. Then:

- **Checks are rules, not opinions.** Files are parsed and compared with the
  expected answer; no model grades another model.
- **Evidence:** the task runner's receipt (every tool call and its status)
  and the files produced, kept in the run folder.
- **Cost** comes from Apex's own usage ledger (usage_log), and is checked
  against the runner's own total.

Each run uses its own folder and database under ~/.apex/tasks/<time>/, so
your real memory is never touched. In --live the model can only write where
its task says; the checks fail it if anything else in the folder changes.
"""
import argparse
import csv
import hashlib
import io
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

HOME = Path.home() / '.apex' / 'tasks'
BUDGET_USD = 0.25
PASS, FAIL = 'pass', 'fail'

BRIEF = ['The workbench needs a second power strip before the drill press arrives.',
         'Move the 3D printer away from the window, because the sun warps prints.',
         'Decide by Friday whether the compressor goes in the garage or the shed.',
         'Nobody has checked whether the garage outlet is on its own circuit.']
BUDGET = [['Item', 'Cost'], ['Shelving', 120], ['Power strip', 35], ['Extension cable', 18]]
PURCHASES = [['Item', 'Cost', 'Status'], ['Shelving', 120, 'ordered'], ['Power strip', 35, 'todo'],
             ['Drill press', 240, 'todo'], ['Extension cable', 18, 'todo'], ['Clamps', 64, 'todo'],
             ['Safety glasses', 12, 'ordered']]
TRACKER = [['Drill press', '240'], ['Clamps', '64']]
STOP = set('that this with from have been will your into when they them than what whether before because there their about away goes needs'.split())


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _words(text):
    """Content words for the grounding check: numbers, and words of four or
    more letters with a plural -s dropped, so "costs" matches "Cost"."""
    out = set()
    for w in re.findall(r'[a-z0-9]+', text.lower()):
        if w.isdigit() or (len(w) >= 4 and w not in STOP):
            out.add(w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith('ss') else w)
    return out


# ---------------------------------------------------------------- the tasks

def setup_plan(work):
    from scripts.doc_acceptance import make_docx, make_xlsx
    make_docx(work / 'kickoff.docx', 'Garage workshop kickoff', BRIEF, [['Owner', 'Alex']])
    make_xlsx(work / 'budget.xlsx', {'Budget': BUDGET})
    return (f"Read `{work / 'kickoff.docx'}` and `{work / 'budget.xlsx'}`. Write `{work / 'plan.md'}` as markdown: "
            "a heading 'Action items', then 3 to 6 bullet points, each ending with its source file name in brackets, "
            "for example [kickoff.docx]; then a line 'Total budget: $N' where N is the sum of the Cost column. "
            "Use only facts from the two files. Do not change the input files or create other files.")


def check_plan(work, run, inputs):
    out = []
    plan = work / 'plan.md'
    text = plan.read_text(encoding='utf-8', errors='replace') if plan.exists() else ''
    out.append(('Wrote plan.md', bool(text), f'{len(text)} characters' if text else 'no file'))
    section = re.split(r'(?im)^#+\s*action items\s*$', text)
    bullets = [b.strip() for b in re.findall(r'(?m)^\s*[-*]\s+(.+)$', section[1])] if len(section) > 1 else []
    out.append(('An "Action items" section with 3–6 bullets', 3 <= len(bullets) <= 6, f'{len(bullets)} bullets'))
    uncited = [b for b in bullets if not re.search(r'\[(kickoff\.docx|budget\.xlsx)\]', b)]
    out.append(('Every bullet cites its source file', bool(bullets) and not uncited, f'uncited: {uncited}' if uncited else 'all cited'))
    source = _words(' '.join(BRIEF) + ' ' + ' '.join(str(c) for row in BUDGET for c in row))
    ungrounded = [b for b in bullets if len(_words(re.sub(r'\[[^\]]*\]', '', b)) & source) < 2]
    out.append(('Every bullet is grounded in the files', bool(bullets) and not ungrounded,
                f'not traceable to the files: {ungrounded}' if ungrounded else 'each shares at least two content words with the sources'))
    total = re.search(r'(?im)^\W*total budget:\s*\$?\s*([0-9][0-9,]*)', text)
    want = sum(row[1] for row in BUDGET[1:])
    out.append(('Budget total is right', bool(total) and int(total.group(1).replace(',', '')) == want,
                f"found {total.group(1) if total else 'no total line'}, expected {want}"))
    return out


def setup_tracker(work):
    from scripts.doc_acceptance import make_xlsx
    make_xlsx(work / 'purchases.xlsx', {'Purchases': PURCHASES})
    return (f"From `{work / 'purchases.xlsx'}`, list every item whose Status is 'todo' and whose Cost is over 50. "
            f"Write them to `{work / 'tracker.csv'}` as CSV with the header `item,cost`, one row per item, "
            "sorted by cost from highest to lowest. Do not change the spreadsheet or create any other files.")


def check_tracker(work, run, inputs):
    out = []
    path = work / 'tracker.csv'
    rows = list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig')))) if path.exists() else []
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]
    out.append(('Wrote tracker.csv', bool(rows), f'{len(rows)} lines' if rows else 'no file'))
    out.append(('Header is item,cost', bool(rows) and [c.lower() for c in rows[0]] == ['item', 'cost'], f'header {rows[0] if rows else None}'))
    body = [[r[0], r[1].replace('$', '').replace(',', '')] for r in rows[1:] if len(r) >= 2]
    out.append(('Exactly the right rows, in order', body == TRACKER, f'got {body}, expected {TRACKER}'))
    return out


TASKS = [dict(id='brief-to-plan', title='Brief to plan (Word + Excel to a cited plan)', setup=setup_plan, check=check_plan,
              made={'plan.md'}),
         dict(id='sheet-to-tracker', title='Sheet to tracker (filtered rows to CSV, nothing else touched)',
              setup=setup_tracker, check=check_tracker, made={'tracker.csv'})]


def untouched(work, inputs, made):
    """Inputs byte-identical, and no file other than the task's own output."""
    changed = [n for n, h in inputs.items() if not (work / n).exists() or _sha(work / n) != h]
    extra = sorted({p.name for p in work.iterdir()} - set(inputs) - set(made))
    return ('Inputs unchanged, nothing else created', not changed and not extra,
            f'changed {changed}, extra {extra}' if changed or extra else 'inputs byte-identical; no stray files')


# ---------------------------------------------------------------- scripted model (fixture only)

class ScriptedClient:
    """Plays a careful model in fixture mode. It is a stand-in to prove the
    harness, never a measure of any model."""

    def __init__(self, fault=None):
        self.fault = fault
        self.messages = SimpleNamespace(create=self.create)

    @staticmethod
    def _reply(blocks, stop):
        return SimpleNamespace(content=[SimpleNamespace(**b) for b in blocks], stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=300))

    def create(self, **kw):
        if 'Summarize the actual outcome' in kw['system']:
            return self._reply([dict(type='text', text='Done; see the specialist evidence.')], 'end_turn')
        msgs = kw['messages']
        task = json.loads(msgs[0]['content'])['task']
        paths = re.findall(r'`([^`]+)`', task)
        if len(msgs) == 1:
            reads = [p for p in paths if p.endswith(('.docx', '.xlsx'))]
            return self._reply([dict(type='tool_use', id=f'r{i}', name='read_file', input=dict(path=p)) for i, p in enumerate(reads)], 'tool_use')
        if len(msgs) == 3:
            if 'tracker.csv' in task:
                rows = TRACKER + ([['Power strip', '35']] if self.fault == 'wrong-filter' else [])
                content = 'item,cost\n' + ''.join(f'{a},{b}\n' for a, b in rows)
                target = next(p for p in paths if p.endswith('tracker.csv'))
            else:
                items = ['Add a second power strip before the drill press arrives [kickoff.docx]',
                         'Move the 3D printer away from the window [kickoff.docx]',
                         'Decide by Friday where the compressor goes: garage or shed [kickoff.docx]',
                         'Check whether the garage outlet is on its own circuit [kickoff.docx]']
                if self.fault == 'ungrounded':
                    items.append('Book a plumber for the bathroom tiles [kickoff.docx]')
                content = '# Action items\n\n' + ''.join(f'- {i}\n' for i in items) + '\nTotal budget: $173\n'
                target = next(p for p in paths if p.endswith('plan.md'))
            return self._reply([dict(type='tool_use', id='w1', name='write_file', input=dict(path=target, content=content))], 'tool_use')
        return self._reply([dict(type='text', text='Written as asked.')], 'end_turn')


# ---------------------------------------------------------------- one run (child process)

class TaskAgent:
    def _all_tools(self):
        from agent.core import TOOLS
        return TOOLS


def run_one(d, task, mode, fault):
    import config
    from agent import longterm, mcp_policy, provider, team
    if Path(longterm.DB_PATH).resolve().parent != d.resolve():
        raise SystemExit(f'Refusing to run: the database is {longterm.DB_PATH}, not the run folder.')
    longterm.init_db()
    mcp_policy.init_db()
    if mode == 'fixture':
        provider.get_client = lambda model: ScriptedClient(fault)
    work = d / task['id']
    work.mkdir(parents=True, exist_ok=True)
    prompt = task['setup'](work)
    inputs = {p.name: _sha(p) for p in work.iterdir()}
    rid = 'task_' + hashlib.sha256((str(d) + task['id']).encode()).hexdigest()[:24]
    started = time.time()
    model = config.AGENT_MODEL
    team.submit(dict(id=rid, task=prompt, roles=['coder'], models={'coder': model, 'apex': model}, budget_usd=BUDGET_USD), TaskAgent())
    from scripts.apex_demo import _wait
    run = _wait(team, rid, 900)
    seconds = round(time.time() - started, 1)

    checks = [('Task runner finished', run['status'] == 'done', f"status {run['status']}" + (f": {run['error'][:200]}" if run.get('error') else ''))]
    tools = [(e['tool'], e['status'], json.loads(e['input']).get('path', '') if e.get('input', '').startswith('{') else '') for s in run['steps'] for e in s['evidence']]
    read = {Path(p).name for t, _, p in tools if t == 'read_file'}
    checks.append(('Read every input file', set(inputs) <= read, f'read {sorted(read) or "nothing"}'))
    checks += task['check'](work, run, inputs)
    checks.append(untouched(work, inputs, task['made']))

    with longterm._conn() as db:
        ledger = db.execute("SELECT COALESCE(SUM(cost_usd),0), COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
                            "FROM usage_log WHERE call_site LIKE ?", (f'team/{rid}/%',)).fetchone()
    checks.append(('Cost recorded in Apex\'s ledger', ledger[1] == run['calls'] and abs(ledger[0] - run['cost_usd']) < 1e-9,
                   f'{ledger[1]} calls, ${ledger[0]:.4f} in usage_log; runner says {run["calls"]} calls, ${run["cost_usd"]:.4f}'))
    (work / 'receipt.json').write_text(json.dumps(run.get('receipt', {}), indent=1))
    verdict = PASS if all(ok for _, ok, _ in checks) else FAIL
    return dict(task=task['id'], title=task['title'], verdict=verdict, model=model, seconds=seconds,
                cost_usd=round(ledger[0], 6), calls=ledger[1], input_tokens=ledger[2], output_tokens=ledger[3],
                tools=[f'{t}({Path(p).name})' if p else t for t, _, p in tools],
                checks=[dict(check=c, passed=ok, evidence=e) for c, ok, e in checks])


# ---------------------------------------------------------------- driver

def _env(d, mode, model, fault):
    env = dict(os.environ, DB_PATH=str(d / 'apex.db'), VAULT_PATH=str(d / 'vault'), PYTHONIOENCODING='utf-8')
    env.pop('APEX_TASKS_FAULT', None)
    if fault:
        env['APEX_TASKS_FAULT'] = fault
    if mode == 'fixture':
        env['ANTHROPIC_API_KEY'] = env.get('ANTHROPIC_API_KEY') or 'sk-ant-placeholder-for-ci-tests-only'
        env['AGENT_MODEL'] = 'claude-opus-5'
    elif model:
        env['AGENT_MODEL'] = model
    return env


def report(d, mode, results):
    stamp = time.strftime('%Y-%m-%d %H:%M')
    lines = [f'# Apex task suite, {stamp} ({mode})', '',
             'Fixture: a scripted model; this proves the harness and the checks, not a model, and its cost is from scripted token counts.'
             if mode == 'fixture' else 'Live: the model named below, through Apex\'s task runner; cost from Apex\'s usage ledger.', '']
    for r in results:
        lines += [f"## {r['title']}: **{r['verdict'].upper()}**", '',
                  f"Model {r['model']} · {r['seconds']} s · {r['calls']} model calls · {r['input_tokens']:,} in / {r['output_tokens']:,} out tokens · **${r['cost_usd']:.4f}**",
                  '', f"Tools: {', '.join(r['tools']) or 'none'}", '', '| Check | Result | Evidence |', '| --- | --- | --- |']
        lines += [f"| {c['check']} | {'PASS' if c['passed'] else '**FAIL**'} | {str(c['evidence']).replace('|', '/')} |" for c in r['checks']]
        lines.append('')
    passed = sum(r['verdict'] == PASS for r in results)
    lines.append(f"{passed}/{len(results)} tasks pass · total ${sum(r['cost_usd'] for r in results):.4f}")
    (d / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    (d / 'report.json').write_text(json.dumps(dict(mode=mode, at=stamp, results=results), indent=1))
    return '\n'.join(lines)


def history(base=HOME):
    rows = []
    for path in sorted(base.glob('*/report.json')):
        data = json.loads(path.read_text())
        for r in data['results']:
            rows.append(f"{data['at']:<17} {data['mode']:<8} {r['model'][:22]:<22} {r['task']:<17} {r['verdict'].upper():<5} "
                        f"${r['cost_usd']:<8.4f} {r['seconds']:>6}s")
    head = f"{'when':<17} {'mode':<8} {'model':<22} {'task':<17} {'':<5} {'cost':<9} {'time':>7}"
    return '\n'.join([head] + rows) if rows else 'No task runs yet.'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--live', action='store_true')
    p.add_argument('--model', help='with --live: run with this model instead of AGENT_MODEL')
    p.add_argument('--out', help='folder for this run (default ~/.apex/tasks/<time>)')
    p.add_argument('--fault', choices=('ungrounded', 'wrong-filter'), help='fixture only: make the scripted model get one task wrong')
    p.add_argument('--history', action='store_true', help='list every past run')
    p.add_argument('--one', help=argparse.SUPPRESS)
    p.add_argument('--dir', help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.history:
        print(history(Path(a.out).expanduser() if a.out else HOME))
        return 0
    if a.one:
        d = Path(a.dir)
        mode = json.loads((d / 'mode.json').read_text())['mode']
        task = next(t for t in TASKS if t['id'] == a.one)
        try:
            result = run_one(d, task, mode, os.getenv('APEX_TASKS_FAULT'))
        except Exception as exc:
            result = dict(task=task['id'], title=task['title'], verdict=FAIL, model=os.getenv('AGENT_MODEL', '?'), seconds=0,
                          cost_usd=0.0, calls=0, input_tokens=0, output_tokens=0, tools=[],
                          checks=[dict(check='Task ran', passed=False, evidence=f'{type(exc).__name__}: {exc}'[:500])])
        (d / f'{task["id"]}.json').write_text(json.dumps(result))
        return 0
    if a.model and not a.live:
        p.error('--model only works with --live')
    if a.fault and a.live:
        p.error('--fault only works without --live')
    mode = 'live' if a.live else 'fixture'
    d = Path(a.out or HOME / time.strftime('%Y%m%d-%H%M%S')).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    (d / 'mode.json').write_text(json.dumps(dict(mode=mode)))
    print(f'Apex task suite ({mode}) in {d}', flush=True)
    results = []
    for task in TASKS:
        print(f'· {task["id"]}', flush=True)
        subprocess.run([sys.executable, str(Path(__file__).resolve()), '--one', task['id'], '--dir', str(d)],
                       env=_env(d, mode, a.model, a.fault), cwd=str(ROOT), timeout=1200)
        path = d / f'{task["id"]}.json'
        results.append(json.loads(path.read_text()) if path.exists() else dict(
            task=task['id'], title=task['title'], verdict=FAIL, model='?', seconds=0, cost_usd=0.0, calls=0,
            input_tokens=0, output_tokens=0, tools=[], checks=[dict(check='Task ran', passed=False, evidence='the task process wrote no result')]))
    print('\n' + report(d, mode, results) + f'\n\nReport: {d / "report.md"}')
    return 0 if all(r['verdict'] == PASS for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
