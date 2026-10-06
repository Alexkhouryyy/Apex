"""Reasoning-only output must not poison the next offline conversation turn."""
import asyncio
import json
from types import SimpleNamespace as NS

import httpx
from openai import OpenAI, APIConnectionError, APITimeoutError, BadRequestError, InternalServerError
import pytest
from fastapi.testclient import TestClient

import config
from agent import apocalypse, provider
from dashboard import apocalypse as api, server

OWNER = {'Authorization': 'Bearer local-test-owner', 'Origin': 'http://testserver'}


def response(text='', reason='length', tools=None):
    return {'id': 'chat-local', 'object': 'chat.completion', 'created': 1, 'model': 'qwen3:4b',
            'choices': [{'index': 0, 'finish_reason': reason,
                         'message': {'role': 'assistant', 'content': text,
                                     'reasoning_content': 'PRIVATE REASONING', 'tool_calls': tools}}],
            'usage': {'prompt_tokens': 2396, 'completion_tokens': 2048, 'total_tokens': 4444}}


@pytest.fixture
def local_adapter(monkeypatch):
    monkeypatch.setenv('APEX_APOCALYPSE', '1')
    verified = []
    monkeypatch.setattr(apocalypse, 'verify_model', lambda model, base: verified.append((model, base)))
    def make(handler):
        sdk = OpenAI(api_key='ollama', base_url='http://127.0.0.1:11435/v1', max_retries=0,
                     http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        return provider._Messages(sdk, strip_prefix='ollama/'), verified
    return make


def test_old_empty_reply_is_valid_and_tool_pair_survives_translation():
    history = [{'role': 'user', 'content': 'First question'}, {'role': 'assistant', 'content': []},
               {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call-1',
                                                'name': 'read_file', 'input': {'path': 'note.txt'}}]},
               {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call-1',
                                           'content': 'Local evidence'}]},
               {'role': 'user', 'content': 'Next question'}]
    before = json.dumps(history)
    translated = provider._translate_messages(history)
    assert translated[1] == {'role': 'assistant', 'content': ''}
    assert translated[2]['tool_calls'][0]['id'] == translated[3]['tool_call_id'] == 'call-1'
    assert translated[3]['content'] == 'Local evidence'
    assert json.dumps(history) == before


@pytest.mark.parametrize('text,reason', [('', 'length'), ('   ', 'stop'), ('', 'stop')])
def test_no_final_answer_raises_instead_of_becoming_history(local_adapter, text, reason):
    messages, _ = local_adapter(lambda r: httpx.Response(200, json=response(text, reason)))
    with pytest.raises(provider.LocalResponseIncomplete) as exc:
        messages.create(model='ollama/qwen3:4b', messages=[{'role': 'user', 'content': 'Explain'}])
    assert 'PRIVATE REASONING' not in str(exc.value)
    if reason == 'length':
        assert 'reply limit' in str(exc.value)


def test_tool_only_reply_remains_a_tool_call(local_adapter):
    tool = {'id': 'call-1', 'type': 'function',
            'function': {'name': 'read_file', 'arguments': '{"path":"note.txt"}'}}
    messages, _ = local_adapter(lambda r: httpx.Response(200, json=response('', 'tool_calls', [tool])))
    result = messages.create(model='ollama/qwen3:4b', messages=[{'role': 'user', 'content': 'Read'}])
    assert result.stop_reason == 'tool_use'
    assert result.content[0].name == 'read_file'


def make_core(monkeypatch, adapter):
    from agent import core
    monkeypatch.setattr(core.longterm, 'load_memory_files', lambda: {})
    monkeypatch.setattr(core._budget, 'check', lambda: None)
    monkeypatch.setattr(config, 'SMART_ROUTING_ENABLED', False)
    agent = core.AgentCore()
    agent._model = 'ollama/qwen3:4b'
    agent._provider_clients['ollama'] = NS(messages=adapter)
    monkeypatch.setattr(agent, '_effective_system_prompt', lambda *a: [{'type': 'text', 'text': 'Local assistant'}])
    monkeypatch.setattr(agent, '_all_tools', lambda: [])
    monkeypatch.setattr(agent, '_rerank_eligible', lambda *a: False)
    return agent


def test_failed_thinking_turn_does_not_break_the_next_turn(local_adapter, monkeypatch, test_db):
    sent = []
    def handler(request):
        data = json.loads(request.content)
        sent.append(data)
        assert request.url.host == '127.0.0.1'
        assert all(m.get('content') is not None or m.get('tool_calls') for m in data['messages'])
        return httpx.Response(200, json=response('' if len(sent) == 1 else 'A final answer',
                                                'length' if len(sent) == 1 else 'stop'))
    adapter, verified = local_adapter(handler)
    agent = make_core(monkeypatch, adapter)
    with pytest.raises(provider.LocalResponseIncomplete):
        agent.run('A difficult question', include_screenshot=False, channel_id='apocalypse')
    assert agent.run('Explain the first part', include_screenshot=False, channel_id='apocalypse') == 'A final answer'
    assert all(item['content'] for item in agent._channel_memories['apocalypse'].messages)
    assert len(sent) == len(verified) == 2  # no automatic replay or alternate provider
    assert all(item['max_tokens'] == 8192 for item in sent)


def test_partial_answer_is_marked_as_incomplete(local_adapter, monkeypatch, test_db):
    adapter, _ = local_adapter(lambda r: httpx.Response(200, json=response('The first part.')))
    agent = make_core(monkeypatch, adapter)
    text = agent.run('Explain', include_screenshot=False, channel_id='apocalypse')
    assert text.startswith('The first part.') and 'reached its length limit' in text


def test_incomplete_summary_keeps_history_and_does_not_abort_later_chat(local_adapter, monkeypatch, test_db):
    from agent.memory import Memory
    adapter, verified = local_adapter(lambda r: httpx.Response(200, json=response()))
    monkeypatch.setattr(config, 'PROACTIVE_MODEL', 'ollama/qwen3:4b')
    monkeypatch.setattr(config, 'BACKGROUND_MODEL', 'ollama/qwen3:4b')
    monkeypatch.setattr(provider, 'get_client', lambda model: NS(messages=adapter))
    memory = Memory()
    for index in range(6):
        memory.add_user(f'Question {index}')
        memory.add_assistant([{'type': 'text', 'text': f'Answer {index}'}])
    original = json.dumps(memory.messages)
    memory.maybe_summarize(NS(messages=adapter))
    assert memory.summary == '' and json.dumps(memory.messages) == original
    assert len(verified) == 1


@pytest.mark.parametrize('text,raises', [('', True), ('Partial answer', False)])
def test_streaming_preserves_length_limit_without_exposing_reasoning(local_adapter, text, raises):
    chunks = [{'id': 's', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'qwen3:4b',
               'choices': [{'index': 0, 'delta': {'reasoning_content': 'PRIVATE REASONING'},
                            'finish_reason': None}]},
              {'id': 's', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'qwen3:4b',
               'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': 'length'}]}]
    wire = ''.join('data: '+json.dumps(item)+'\n\n' for item in chunks)+'data: [DONE]\n\n'
    adapter, _ = local_adapter(lambda r: httpx.Response(200, text=wire,
                                                       headers={'Content-Type': 'text/event-stream'}))
    stream = adapter.stream(model='ollama/qwen3:4b', messages=[{'role': 'user', 'content': 'Explain'}])
    if raises:
        with pytest.raises(provider.LocalResponseIncomplete):
            list(stream)
    else:
        assert ''.join(event.delta.text for event in stream) == text
        assert stream.get_final_message().stop_reason == 'max_tokens'


@pytest.fixture
def client(monkeypatch, tmp_path, test_db):
    monkeypatch.setenv('APEX_APOCALYPSE', '1')
    monkeypatch.setenv('APEX_APOCALYPSE_HOME', str(tmp_path))
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'local-test-owner')
    monkeypatch.setattr(api, '_chat_lock', asyncio.Lock())
    server._throttle.reset('testclient')
    return TestClient(server.app)


@pytest.mark.parametrize('failure,expected', [
    ('incomplete', 'reply limit'), ('empty', 'no final answer'), ('bad_request', 'rejected'),
    ('timeout', 'took too long'), ('connection', 'connection stopped'),
    ('server', 'server failed'), ('unexpected', 'Local chat failed')])
def test_chat_errors_are_actionable_and_do_not_leak_provider_data(client, monkeypatch, caplog, failure, expected):
    request = httpx.Request('POST', 'http://127.0.0.1:11435/v1/chat/completions')
    private = 'PRIVATE PROMPT AND TOKEN'
    def run(*args, **kwargs):
        if failure == 'empty': return ''
        if failure == 'incomplete': raise provider.LocalResponseIncomplete('The local model reached its reply limit.')
        if failure == 'bad_request': raise BadRequestError(private, response=httpx.Response(400, request=request), body={'error': private})
        if failure == 'timeout': raise APITimeoutError(request=request)
        if failure == 'connection': raise APIConnectionError(message=private, request=request)
        if failure == 'server': raise InternalServerError(private, response=httpx.Response(500, request=request), body={'error': private})
        raise RuntimeError(private)
    monkeypatch.setattr(server, '_agent_ref', NS(run=run))
    result = client.post('/api/apocalypse/chat', json={'message': 'Explain'}, headers=OWNER)
    assert result.status_code == 503 and expected in result.json()['detail']
    assert private not in result.text and private not in caplog.text
    assert 'Local chat' in caplog.text


def test_missing_agent_does_not_pretend_a_one_shot_completion_is_ready(client, monkeypatch):
    monkeypatch.setattr(server, '_agent_ref', None)
    calls = []
    def complete(*args, **kwargs):
        calls.append((args, kwargs))
        return 'A local answer'
    monkeypatch.setattr(provider, 'complete', complete)
    result = client.post('/api/apocalypse/chat', json={'message': 'Explain'}, headers=OWNER)
    assert result.status_code == 503 and 'agent is not ready' in result.json()['detail']
    assert calls == []
