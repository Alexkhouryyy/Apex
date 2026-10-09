"""Usage accounting and public, read-only CLI protocol, without live inference."""
import json
import sys
from pathlib import Path

import pytest

from agent import code_engines, code_usage, work_engines


def test_codex_cache_is_a_subset_and_unknown_is_not_zero():
    usage = code_usage.turn_usage('chatgpt', {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 20})
    assert usage['total_tokens'] == 120 and usage['uncached_input_tokens'] == 20
    assert usage['cached_input_tokens'] == 80 and usage['cache_write_input_tokens'] is None
    assert usage['reasoning_output_tokens'] is None and usage['cost_usd'] is None
    unknown = code_usage.turn_usage('chatgpt')
    assert unknown['input_tokens'] is None and unknown['total_tokens'] is None
    zeros = code_usage.turn_usage('chatgpt', {'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0})
    assert zeros['total_tokens'] == 0 and zeros['cached_input_tokens'] == 0


def test_claude_cache_reads_and_writes_count_once():
    usage = code_usage.turn_usage('claude', {'input_tokens': 10, 'cache_read_input_tokens': 80,
        'cache_creation_input_tokens': 15, 'output_tokens': 20}, cost=0.1, model_usage={'claude-test': {'contextWindow': 200000}})
    assert usage['input_tokens'] == 105 and usage['total_tokens'] == 125
    assert usage['uncached_input_tokens'] == 10 and usage['cache_write_input_tokens'] == 15
    assert usage['model'] == 'claude-test' and usage['context_window_tokens'] == 200000
    assert usage['cost_usd'] == 0.1 and usage['cost_is_estimate']
    missing_cache = code_usage.turn_usage('claude', {'input_tokens': 10, 'output_tokens': 20})
    assert missing_cache['total_tokens'] is None and missing_cache['uncached_input_tokens'] == 10


@pytest.mark.parametrize('bad', [-1, True, '12', 1.2, float('nan'), float('inf'), 10**400])
def test_usage_rejects_invalid_counters(bad):
    assert code_usage.number(bad) is None


def test_final_events_are_not_replaced_by_cumulative_live_usage(tmp_path):
    state = {}
    assert code_engines.parse('chatgpt', json.dumps({'type': 'thread/tokenUsage/updated',
        'tokenUsage': {'total': {'totalTokens': 90000}}}), tmp_path, state) == []
    done = code_engines.parse('chatgpt', json.dumps({'type': 'turn.completed',
        'usage': {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 20}}), tmp_path, state)[0]
    assert done['tokens'] == 120
    assert done['usage']['context_window_tokens'] is None
    failed = code_engines.parse('chatgpt', json.dumps({'type': 'turn.failed',
        'error': {'message': 'failed'}}), tmp_path, state)[0]
    assert failed['tokens'] is None and failed['usage']['total_tokens'] is None


@pytest.mark.parametrize('engine', ['claude', 'chatgpt'])
def test_full_multiline_command_is_available_beside_short_title(tmp_path, engine):
    command = 'python - <<\'PY\'\n' + 'print("example")\n' * 40 + 'PY'
    event = {'type': 'item.started', 'item': {'type': 'command_execution', 'id': 'cmd', 'command': command}}
    if engine == 'claude':
        event = {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'cmd',
            'name': 'Bash', 'input': {'command': command, 'description': 'Run example'}}]}}
    parsed = code_engines.parse(engine, json.dumps(event), tmp_path, {})[0]
    assert parsed['command'] == command and len(parsed['title']) <= 200
    assert parsed['id'] == 'cmd'


def test_generic_mcp_completion_pairs_with_start_and_bounds_result(tmp_path):
    item = {'type': 'mcp_tool_call', 'id': 'tool1', 'server': 'other', 'tool': 'example', 'status': 'completed',
            'result': {'content': [{'type': 'image', 'data': 'must-not-expose'}, {'type': 'text', 'text': 'x'*5000}]}}
    start = code_engines.parse('chatgpt', json.dumps({'type': 'item.started', 'item': item}), tmp_path, {})[0]
    done = code_engines.parse('chatgpt', json.dumps({'type': 'item.completed', 'item': item}), tmp_path, {})[0]
    assert start['id'] == done['id'] and done['ok'] and done['status'] == 'completed'
    assert len(done['output']) <= code_engines.OUTPUT_TAIL+1 and 'must-not-expose' not in done['output']
    item['status'], item['error'] = 'failed', {'message': 'tool failed'}
    failed = code_engines.parse('chatgpt', json.dumps({'type': 'item.completed', 'item': item}), tmp_path, {})[0]
    assert not failed['ok'] and failed['output'] == 'tool failed'


def test_web_search_completion_resolves_the_started_row_and_preserves_failures(tmp_path):
    item = {'id': 'search1', 'type': 'web_search', 'query': 'official reference', 'status': 'in_progress'}
    start = code_engines.parse('chatgpt', json.dumps({'type': 'item.started', 'item': item}), tmp_path, {})[0]
    success = code_engines.parse('chatgpt', json.dumps({'type': 'item.completed', 'item': {
        **item, 'status': 'completed'}}), tmp_path, {})[0]
    assert start['tool'] == 'web' and start['id'] == success['id']
    assert success['kind'] == 'result' and success['ok'] and success['status'] == 'completed'
    for status in ('failed', 'declined'):
        failure = code_engines.parse('chatgpt', json.dumps({'type': 'item.completed', 'item': {
            **item, 'status': status, 'error': {'message': 'api_key=' + 'privatevalue'*500}}}), tmp_path, {})[0]
        assert failure['id'] == start['id'] and not failure['ok'] and failure['status'] == 'failed'
        assert 'privatevalue' not in failure['output'] and '[redacted]' in failure['output']
        assert len(failure['output']) <= code_engines.OUTPUT_TAIL+1
    failed_error = code_engines.parse('chatgpt', json.dumps({'type': 'item.completed', 'item': {
        **item, 'status': 'completed', 'error': {'message': 'search provider failed'}}}), tmp_path, {})[0]
    assert not failed_error['ok'] and failed_error['output'] == 'search provider failed'


def test_command_detail_and_generic_tool_output_redact_credentials(tmp_path):
    key = 'sk-test-secret-value-abcdefghijklmno1234567890'
    command = 'OPENAI_API_KEY=' + key + ' python app.py'
    started = code_engines.parse('chatgpt', json.dumps({'type': 'item.started', 'item': {
        'id': 'a', 'type': 'command_execution', 'command': command}}), tmp_path, {})
    assert key not in json.dumps(started)
    result = code_engines.parse('chatgpt', json.dumps({'type': 'item.completed', 'item': {
        'id': 'a', 'type': 'mcp_tool_call', 'server': 'other', 'status': 'completed',
        'result': {'content': [{'type': 'text', 'text': 'OPENAI_API_KEY=' + key}]}}}), tmp_path, {})
    assert key not in json.dumps(result)


@pytest.mark.parametrize('as_error', [False, True])
def test_mcp_redacts_full_value_before_tail_loses_credential_label(tmp_path, as_error):
    secret = 'credentialvalue' * 500
    item = {'id': 'tool1', 'type': 'mcp_tool_call', 'server': 'other', 'status': 'completed',
            'result': {'content': [{'type': 'text', 'text': 'api_key=' + secret}]}}
    if as_error:
        item['error'] = {'message': 'api_key=' + secret}
    parsed = code_engines.parse('chatgpt', json.dumps({'type': 'item.completed', 'item': item}), tmp_path, {})[0]
    assert 'credentialvalue' not in parsed['output'] and '[redacted]' in parsed['output']
    assert parsed['ok'] is not as_error


@pytest.mark.parametrize('flag', ['isError', 'is_error'])
def test_mcp_tool_result_error_flag_overrides_completed_transport(tmp_path, flag):
    item = {'id': 'tool1', 'type': 'mcp_tool_call', 'server': 'other', 'status': 'completed',
            'result': {flag: True, 'content': [{'type': 'text', 'text': 'application failed'}]}}
    parsed = code_engines.parse('chatgpt', json.dumps({'type': 'item.completed', 'item': item}), tmp_path, {})[0]
    assert parsed['ok'] is False and parsed['status'] == 'failed'
    assert parsed['output'] == 'application failed'


def test_session_totals_idempotent_across_repeated_reads_and_duplicate_run_ids():
    a = {'run_id': 'a', 'usage': code_usage.turn_usage('chatgpt',
        {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 20})}
    b = {'run_id': 'b', 'usage': code_usage.turn_usage('chatgpt',
        {'input_tokens': 200, 'cached_input_tokens': 180, 'output_tokens': 30})}
    summary = code_usage.session_usage([a, b, b])
    assert summary == code_usage.session_usage([a, b])
    assert summary['total_tokens'] == 350 and summary['observed_turns'] == 2 and summary['complete']
    partial = code_usage.session_usage([a, {'tokens': 1000}])
    assert partial['total_tokens'] == 120 and partial['unknown_turns'] == 1 and not partial['complete']
    assert partial['field_complete']['input_tokens'] is False
    assert code_usage.session_usage([])['total_tokens'] is None


def test_session_usage_partitions_review_and_coding_without_moving_last_coding_turn():
    coding_usage = code_usage.turn_usage('chatgpt', {'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 20})
    review_usage = code_usage.turn_usage('chatgpt', {'input_tokens': 200, 'cached_input_tokens': 180, 'output_tokens': 30})
    rows = [{'run_id': 'coding1', 'activity': 'coding', 'usage': coding_usage},
            {'run_id': 'review1', 'activity': 'review', 'usage': review_usage}]
    result = code_usage.session_usage([*rows, rows[-1]])
    assert result['total_tokens'] == 350 and result['last_turn'] == coding_usage
    assert result['last_activity'] == result['last_review'] == review_usage
    assert result['by_activity']['coding']['total_tokens'] == 120
    assert result['by_activity']['review']['total_tokens'] == 230
    assert result['by_activity']['review']['observed_turns'] == 1


def test_provider_responses_are_allowlisted_and_null_values_preserved():
    data = {'rateLimitsByLimitId': {'codex': {'limitId': 'codex', 'planType': 'plus',
        'primary': {'usedPercent': 0, 'resetsAt': None, 'windowDurationMins': 300},
        'secondary': {'usedPercent': 104, 'resetsAt': 123}, 'access_token': 'should-never-appear'}},
        'accountId': 'private-id', 'accessToken': 'secret'}
    rows = code_usage.limits(data)
    assert rows[0]['primary']['remaining_percent'] == 100
    assert rows[0]['secondary']['remaining_percent'] == 0
    assert rows[0]['primary']['resets_at'] is None
    assert 'secret' not in json.dumps(rows) and 'private-id' not in json.dumps(rows)
    assert code_usage.activity({'summary': {'lifetimeTokens': None}})['lifetime_tokens'] is None


def test_fractional_rate_percentages_remain_numeric_and_preserve_remaining():
    rows = code_usage.limits({'rateLimits': {'primary': {'usedPercent': 37.5}, 'secondary': {'usedPercent': 100.25}}})
    assert rows[0]['primary']['used_percent'] == 37.5
    assert rows[0]['primary']['remaining_percent'] == 62.5
    assert rows[0]['secondary']['used_percent'] == 100.25
    assert rows[0]['secondary']['remaining_percent'] == 0


def test_real_stdio_snapshot_never_starts_a_thread_or_inference(tmp_path, monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('POSIX executable fixture')
    log, exe = tmp_path / 'protocol.jsonl', tmp_path / 'client'
    exe.write_text('#!' + sys.executable + '\n' + '''import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    with open(LOG, 'a') as f: f.write(json.dumps(msg)+'\\n')
    if msg['method'] == 'initialized': continue
    result = {}
    if msg['method'] == 'account/rateLimits/read':
        result = {'rateLimits': {'limitId':'codex', 'primary':{'usedPercent':25, 'resetsAt':123}}, 'accountId':'secret-account'}
    if msg['method'] == 'account/usage/read':
        result = {'summary': {'lifetimeTokens':12345}, 'dailyUsageBuckets':[{'startDate':'2026-10-09','tokens':10}]}
    print(json.dumps({'id':msg['id'], 'result':result}), flush=True)
'''.replace('LOG', repr(str(log))))
    exe.chmod(0o755)
    monkeypatch.setattr(work_engines, 'binary', lambda _: str(exe))
    monkeypatch.setattr(work_engines, 'check', lambda _: {'ok': True})
    result = code_usage.discover('chatgpt')
    assert result['limits_available'] and result['activity_available'] and result['available']
    assert result['activity']['lifetime_tokens'] == 12345
    assert result['limits'][0]['primary']['remaining_percent'] == 75
    assert 'secret-account' not in json.dumps(result)
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert [m['method'] for m in messages] == ['initialize', 'initialized', 'account/rateLimits/read', 'account/usage/read']


def test_partial_capability_failure_keeps_successful_limits(monkeypatch):
    from agent import code_catalog
    class Connection:
        def __init__(self, *a, **kw): self.closed = False
        def send(self, msg): self.msg = msg
        def receive(self, predicate):
            ident = self.msg['id']
            return {'id': ident, **({'error': {'message': 'a-secret-error'}} if ident == 2 else
                {'result': {'rateLimits': {'primary': {'usedPercent': 1}}}})}
        def close(self): self.closed = True
    monkeypatch.setattr(code_catalog, 'CatalogConnection', Connection)
    monkeypatch.setattr(work_engines, 'check', lambda _: {'ok': True})
    monkeypatch.setattr(work_engines, 'binary', lambda _: '/fake')
    result = code_usage.discover('chatgpt')
    assert result['limits_available'] and not result['activity_available']
    assert 'a-secret-error' not in json.dumps(result)


def test_cache_returns_copies_and_failure_is_explicit(monkeypatch):
    code_usage._cache.clear()
    monkeypatch.setattr(work_engines, 'binary', lambda _: '/fake')
    monkeypatch.setattr(work_engines, 'check', lambda _: {'ok': False})
    first = code_usage.snapshot('chatgpt')
    first['limits'].append({'fake': 1})
    assert code_usage.snapshot('chatgpt')['limits'] == []
    assert not code_usage.snapshot('chatgpt')['available']
    code_usage._cache.clear()
