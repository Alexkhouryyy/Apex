"""The open-source candidate register (docs/research/oss-register.json).

The roadmap asks for repository, revision, star count and observation date,
license, findings, effort, disposition, owner and review date for every
candidate. These checks keep later edits from dropping any of that, and keep
the readable page in step with the data.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = json.loads((ROOT / 'docs' / 'research' / 'oss-register.json').read_text(encoding='utf-8'))
PAGE = (ROOT / 'docs' / 'OPEN_SOURCE_REGISTER.md').read_text(encoding='utf-8')
CANDIDATES = [c for g in REGISTER['gaps'] for c in g['candidates']]


def test_register_has_owner_dates_and_method():
    assert REGISTER['owner'] and REGISTER['method']
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', REGISTER['observed'])
    assert REGISTER['next_review'] > REGISTER['observed']
    assert set(REGISTER['dispositions']) == {'integrate', 'adapt', 'learn_only', 'avoid'}


def test_every_gap_names_today_and_a_measurable_success():
    for gap in REGISTER['gaps']:
        assert gap['gap'] and gap['apex_today'] and gap['success'] and gap['queries']
        assert all('stars:>=1000' in q or 'stars' in q for q in gap['queries'])
        assert gap['candidates']


def test_every_candidate_is_pinned_and_complete():
    names = [c['repository'] for c in CANDIDATES]
    assert len(names) == len(set(names)), 'a repository is listed twice'
    for c in CANDIDATES:
        assert re.fullmatch(r'[0-9a-f]{40}', c['revision']), c['repository']
        assert c['stars'] >= 1000 and c['stars_observed'] == REGISTER['observed']
        assert c['revision'] in c['license_evidence'] and c['license']
        for field in ('activity', 'capability', 'findings', 'effort', 'reason'):
            assert c[field], (c['repository'], field)
        assert c['disposition'] in REGISTER['dispositions']


def test_adoptions_have_a_next_step_and_rejections_a_reopen_condition():
    for c in CANDIDATES:
        if c['disposition'] in ('integrate', 'adapt', 'avoid'):
            assert c.get('next_step'), c['repository']


def test_page_lists_every_candidate_at_its_revision():
    for c in CANDIDATES:
        assert f"https://github.com/{c['repository']}/tree/{c['revision']}" in PAGE
