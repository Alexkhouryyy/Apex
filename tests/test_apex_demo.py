"""The minimum complete demonstration (scripts/apex_demo.py).

Fixture mode runs the whole acceptance test with a scripted model and app:
every step passes except the one Apex hasn't built (documents per project),
which must say unknown rather than pass. Each injected fault must turn its
own check to fail, or the check proves nothing.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'apex_demo.py'


def run(tmp_path, *extra, real_db=None):
    out = tmp_path / 'demo'
    env = dict(os.environ, ANTHROPIC_API_KEY='sk-ant-placeholder-for-ci-tests-only',
               DB_PATH=str(real_db or tmp_path / 'real.db'))
    proc = subprocess.run([sys.executable, str(SCRIPT), '--out', str(out), *extra], env=env,
                          capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    path = out / 'report.json'
    report = json.loads(path.read_text()) if path.exists() else None
    return proc, report, {s['step']: s for s in (report or {}).get('steps', [])}


def test_fixture_demonstration_passes_end_to_end(tmp_path):
    proc, report, steps = run(tmp_path)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert report['counts'] == {'pass': 15, 'fail': 0, 'unknown': 1, 'skipped': 0}
    assert steps['Documents are per project']['status'] == 'unknown'
    assert 'New process' in steps['Restart and resume project A']['evidence']
    assert "unknown outcome: 1 (append_file)" in steps['Interrupted action is flagged, not assumed']['evidence']
    assert "tools used: ['read_file']" in steps['Recover without repeating the action']['evidence']
    assert steps['Unauthorized app write is refused']['status'] == 'pass'
    assert (tmp_path / 'demo' / 'ledger.txt').read_text().count('INVOICE-42 sent') == 1
    assert '| **PASS** |' in (tmp_path / 'demo' / 'report.md').read_text()


def test_it_never_touches_the_real_database(tmp_path):
    real = tmp_path / 'real.db'
    run(tmp_path, real_db=real)
    assert not real.exists()
    assert (tmp_path / 'demo' / 'apex.db').exists()


@pytest.mark.parametrize('fault,failing', [
    ('leak', 'Projects stay separate'),
    ('repeat-write', 'Recover without repeating the action'),
])
def test_each_check_can_fail(tmp_path, fault, failing):
    proc, report, steps = run(tmp_path, '--fault', fault)
    assert proc.returncode == 1
    assert [s for s, r in steps.items() if r['status'] == 'fail'] == [failing]


def test_faults_are_fixture_only(tmp_path):
    proc, _, _ = run(tmp_path, '--live', '--fault', 'leak')
    assert proc.returncode == 2 and 'only works without --live' in proc.stderr
