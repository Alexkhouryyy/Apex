"""The task suite (scripts/apex_tasks.py): two representative tasks through
Apex's task runner, judged by rules, with cost from Apex's own ledger.

Fixture mode must pass end to end; each injected fault must fail exactly its
own check; and the rule checks must reject near misses on their own.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import apex_tasks

ROOT = Path(__file__).resolve().parents[1]


def run(tmp_path, *extra):
    out = tmp_path / 'run'
    env = dict(os.environ, ANTHROPIC_API_KEY='sk-ant-placeholder-for-ci-tests-only', DB_PATH=str(tmp_path / 'real.db'))
    proc = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'apex_tasks.py'), '--out', str(out), *extra],
                          env=env, capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    path = out / 'report.json'
    return proc, (json.loads(path.read_text()) if path.exists() else None)


def failing(report):
    return [(r['task'], c['check']) for r in report['results'] for c in r['checks'] if not c['passed']]


def test_fixture_suite_passes_with_receipts_and_ledger_cost(tmp_path):
    proc, report = run(tmp_path)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert [r['verdict'] for r in report['results']] == ['pass', 'pass'] and failing(report) == []
    plan = report['results'][0]
    assert plan['tools'] == ['read_file(kickoff.docx)', 'read_file(budget.xlsx)', 'write_file(plan.md)']
    assert plan['calls'] == 4 and plan['cost_usd'] > 0
    assert (tmp_path / 'run' / 'brief-to-plan' / 'receipt.json').exists()
    assert not (tmp_path / 'real.db').exists()                       # its own database, never yours
    assert 'brief-to-plan' in apex_tasks.history(tmp_path)


@pytest.mark.parametrize('fault,expected', [
    ('ungrounded', [('brief-to-plan', 'Every bullet is grounded in the files')]),
    ('wrong-filter', [('sheet-to-tracker', 'Exactly the right rows, in order')]),
])
def test_each_fault_fails_only_its_check(tmp_path, fault, expected):
    proc, report = run(tmp_path, '--fault', fault)
    assert proc.returncode == 1 and failing(report) == expected


def test_flags_that_make_no_sense_are_refused(tmp_path):
    proc, _ = run(tmp_path, '--live', '--fault', 'ungrounded')
    assert proc.returncode == 2 and 'only works without --live' in proc.stderr
    proc, _ = run(tmp_path, '--model', 'deepseek-flash')
    assert proc.returncode == 2 and 'only works with --live' in proc.stderr


def plan_checks(tmp_path, text):
    (tmp_path / 'plan.md').write_text(text)
    return {name: ok for name, ok, _ in apex_tasks.check_plan(tmp_path, None, {})}


GOOD = ('# Action items\n- Add a second power strip before the drill press [kickoff.docx]\n'
        '- Move the 3D printer away from the window [kickoff.docx]\n- Shelving costs 120 [budget.xlsx]\n\nTotal budget: $173\n')


def test_plan_rules_catch_near_misses(tmp_path):
    assert all(plan_checks(tmp_path, GOOD).values())
    assert not plan_checks(tmp_path, GOOD.replace('$173', '$155'))['Budget total is right']
    assert not plan_checks(tmp_path, GOOD.replace(' [budget.xlsx]', ''))['Every bullet cites its source file']
    assert not plan_checks(tmp_path, GOOD.replace('# Action items', '# Plan'))['An "Action items" section with 3–6 bullets']
    two = '# Action items\n- Add a second power strip [kickoff.docx]\n- Shelving 120 [budget.xlsx]\nTotal budget: $173\n'
    assert not plan_checks(tmp_path, two)['An "Action items" section with 3–6 bullets']


def test_tracker_rules_catch_order_and_header(tmp_path):
    def checks(text):
        (tmp_path / 'tracker.csv').write_text(text)
        return {name: ok for name, ok, _ in apex_tasks.check_tracker(tmp_path, None, {})}
    assert all(checks('item,cost\nDrill press,240\nClamps,64\n').values())
    assert all(checks('item,cost\nDrill press,$240\nClamps,64\n').values())         # a currency sign is fine
    assert not checks('item,cost\nClamps,64\nDrill press,240\n')['Exactly the right rows, in order']
    assert not checks('name,price\nDrill press,240\nClamps,64\n')['Header is item,cost']


def test_untouched_catches_edits_and_stray_files(tmp_path):
    (tmp_path / 'in.xlsx').write_bytes(b'original')
    inputs = {'in.xlsx': apex_tasks._sha(tmp_path / 'in.xlsx')}
    assert apex_tasks.untouched(tmp_path, inputs, {'out.csv'})[1]
    (tmp_path / 'notes.txt').write_text('stray')
    assert not apex_tasks.untouched(tmp_path, inputs, {'out.csv'})[1]
    (tmp_path / 'notes.txt').unlink()
    (tmp_path / 'in.xlsx').write_bytes(b'edited')
    assert not apex_tasks.untouched(tmp_path, inputs, {'out.csv'})[1]
