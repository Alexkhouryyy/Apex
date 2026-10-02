"""Call Apex while the laptop is off: relay/server.py answers the same Twilio
number (as its fallback), on a real server on a real socket, and
relay/answer.py keeps call answers short and spoken."""
import importlib.util
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN, TWILIO, PUBLIC, ME = 'relay-token', 'twilio-secret', 'https://relay.tail1234.ts.net', '+96170000000'


def _load(name):
    spec = importlib.util.spec_from_file_location(f'relay_{name}', ROOT / 'relay' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def relay(tmp_path):
    mod = _load('server')
    mod.TOKEN, mod.TWILIO_TOKEN, mod.PUBLIC_URL, mod.CALLERS = TOKEN, TWILIO, PUBLIC, ME
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    srv = mod.serve(host='127.0.0.1', port=port, db_path=str(tmp_path / 'relay.db'))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'

    def http(path, method='GET', body=None, headers=None):
        req = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def call(path, signed=True, signed_for=None, **params):
        params = {'From': ME, 'CallSid': 'CA1', **params}
        sig = mod.twilio_signature(TWILIO, PUBLIC + (signed_for or path), params) if signed else 'forged'
        return http(path, 'POST', urllib.parse.urlencode(params).encode(),
                    {'X-Twilio-Signature': sig, 'Content-Type': 'application/x-www-form-urlencoded'})

    def api(path, method='GET', obj=None):
        return http(path, method, json.dumps(obj).encode() if obj is not None else None,
                    {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'})
    yield type('Relay', (), dict(mod=mod, call=staticmethod(call), api=staticmethod(api)))
    srv.shutdown()
    srv.server_close()


def wait_url(twiml):
    return twiml.split('<Redirect method="POST">')[1].split('</Redirect>')[0].replace('&amp;', '&')


def test_a_spoken_question_is_answered_from_the_relay(relay):
    code, hello = relay.call('/twilio/voice')
    assert code == 200 and 'computer is off' in hello and '<Gather input="speech"' in hello
    code, first = relay.call('/twilio/voice', SpeechResult='When is my dentist appointment?')
    assert 'One moment' in first
    url = wait_url(first)
    # The answerer is alive and picks it up (as answer.py --watch would).
    pending = json.loads(relay.api('/questions/pending')[1])['items']
    assert pending[0]['text'] == '[call] When is my dentist appointment?'
    code, waiting = relay.call(url)
    assert '<Pause' in waiting and 'n=1' in wait_url(waiting)
    qid = pending[0]['id']
    relay.api(f'/questions/{qid}/claim', 'POST', {})
    relay.api('/reply', 'POST', {'question_id': qid, 'question': 'x',
                                 'answer': '**Thursday at 10.** See [calendar](https://c.example).',
                                 'requests': [{'tool': 'calendar_add', 'why': 'reminder'}]})
    code, said = relay.call(wait_url(waiting))
    assert 'Thursday at 10. See calendar.' in said and 'queued that for your computer' in said
    assert '**' not in said and 'https://' not in said and 'Anything else?' in said


def test_no_answerer_running_says_so_instead_of_waiting(relay):
    _, first = relay.call('/twilio/voice', SpeechResult='Anything new?')
    _, out = relay.call(wait_url(first))
    assert 'answerer isn&apos;t running' in out and '<Hangup/>' in out


def test_goodbye_strangers_forgeries_and_unconfigured(relay):
    assert '<Hangup/>' in relay.call('/twilio/voice', SpeechResult='bye')[1]
    assert 'not authorized' in relay.call('/twilio/voice', From='+15550001111', SpeechResult='hi')[1]
    assert relay.call('/twilio/voice', signed=False, SpeechResult='hi')[0] == 403
    # The signature covers the check-back link's query: a changed `n` is a forgery.
    _, first = relay.call('/twilio/voice', SpeechResult='hi')
    url = wait_url(first)
    assert relay.call(url)[0] == 200
    assert relay.call(url.replace('n=0', 'n=19'), signed_for=url)[0] == 403
    relay.mod.CALLERS = ''
    assert relay.call('/twilio/voice', SpeechResult='hi')[0] == 403          # closed when unconfigured
    # Calls never open the token-gated API.
    relay.mod.CALLERS = ME
    status, _ = relay.call('/questions')
    assert status in (401, 404)


def test_a_wait_link_cannot_read_a_typed_question(relay):
    _, typed = relay.api('/questions', 'POST', {'text': 'my private typed question'})
    qid = json.loads(typed)['id']
    _, out = relay.call(f'/twilio/voice/wait?q={qid}&n=0')
    assert 'lost that one' in out and 'private' not in out


def test_answerer_speaks_short_for_calls():
    answer = _load('answer')
    answer.API_KEY, answer.PROVIDER = 'sk-test', 'anthropic'
    seen = {}

    def fake(body):
        seen.update(json.loads(body))
        return json.dumps({'content': [{'type': 'text', 'text': '{"answer": "Thursday at 10.", "requests": []}'}]}).encode()
    out = answer.ask_model('[call] When is my dentist?', 'Dentist Thursday 10am', call=fake)
    assert out['answer'] == 'Thursday at 10.'
    assert 'read aloud' in seen['system'] and 'Question: When is my dentist?' in seen['messages'][0]['content']
    answer.ask_model('When is my dentist?', 'ctx', call=fake)
    assert 'read aloud' not in seen['system']                               # typed questions unchanged


def test_calls_go_to_the_pc_while_it_answers_and_to_the_cloud_when_it_does_not(relay, monkeypatch):
    """The whole chain on real sockets: Twilio -> relay (public) -> Apex on the PC
    (private), with the PC checking Twilio's signature against the relay's address."""
    import uvicorn
    import config
    from dashboard import server as apex
    from tools import phone
    monkeypatch.setattr(config, 'TWILIO_AUTH_TOKEN', TWILIO, raising=False)
    monkeypatch.setattr(config, 'TWILIO_PUBLIC_BASE_URL', PUBLIC, raising=False)
    monkeypatch.setattr(config, 'PHONE_ALLOWED_NUMBERS', [ME], raising=False)
    monkeypatch.setattr(phone, '_calls', {})
    monkeypatch.setattr(phone, '_agent_run_fn', lambda text, channel_id: 'Your next meeting is at three.')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    pc = uvicorn.Server(uvicorn.Config(apex.app, host='127.0.0.1', port=port, log_level='error'))
    threading.Thread(target=pc.run, daemon=True).start()
    for _ in range(200):
        try:
            socket.create_connection(('127.0.0.1', port), 0.1).close()
            break
        except OSError:
            time.sleep(0.02)
    relay.mod.PC_URL = f'http://127.0.0.1:{port}'
    try:
        _, hello = relay.call('/twilio/voice')
        assert 'Apex here' in hello and 'computer is off' not in hello          # the PC answered
        _, first = relay.call('/twilio/voice', SpeechResult="What's next?")
        assert 'One moment' in first and 'q=' not in wait_url(first)            # the PC's own wait link
        for _ in range(50):
            _, said = relay.call(wait_url(first))
            if 'three' in said:
                break
            time.sleep(0.05)
        assert 'Your next meeting is at three.' in said
    finally:
        pc.should_exit = True
        time.sleep(0.3)
    # PC gone: the same number is answered from the cloud, even mid-call.
    _, hello = relay.call('/twilio/voice')
    assert 'computer is off' in hello
    _, mid = relay.call('/twilio/voice/wait?n=3')
    assert 'stopped answering' in mid and 'cloud' in mid
