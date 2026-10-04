"""Call Apex, the PC side (tools/phone.py and the /twilio/voice routes).

On the phone in the car: Twilio gives each webhook about 15 seconds, so a
question is thought about in the background and the call checks back until
the answer is ready, then speaks it short, clean and driving-safe.
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

import config
from dashboard import server, webhook_auth
from tools import phone

TOKEN = 'twilio-test-token'
BASE = 'https://alex-pc.tail1234.ts.net'
ME = '+96170000000'


@pytest.fixture
def call(monkeypatch):
    monkeypatch.setattr(config, 'TWILIO_AUTH_TOKEN', TOKEN, raising=False)
    monkeypatch.setattr(config, 'PUBLIC_BASE_URL', BASE, raising=False)
    monkeypatch.setattr(config, 'PHONE_ALLOWED_NUMBERS', [ME], raising=False)
    monkeypatch.setattr(phone, '_calls', {})
    asked, gate = [], threading.Event()
    reply = {'text': '**Yes.** Your next meeting is at 3 pm with [Sam](https://cal.example). Details:\n- room 4\n- bring the deck'}

    def agent(text, channel_id):
        asked.append((text, channel_id))
        gate.wait(5)
        return reply['text']
    monkeypatch.setattr(phone, '_agent_run_fn', agent)
    texts = []
    monkeypatch.setattr(phone, 'sms_send', lambda to, body: texts.append((to, body)) or 'sent')

    def post(path, **params):
        params = {'From': ME, 'CallSid': 'CA123', **params}
        sig = webhook_auth.twilio_signature(TOKEN, BASE + path, params)
        with TestClient(server.app) as c:
            return c.post(path, data=params, headers={'X-Twilio-Signature': sig}).text
    return type('Call', (), dict(post=staticmethod(post), asked=asked, gate=gate, reply=reply, texts=texts))


def test_greets_listens_thinks_in_the_background_and_answers(call):
    assert 'Apex here' in call.post('/twilio/voice') and '<Gather input="speech"' in call.post('/twilio/voice')
    first = call.post('/twilio/voice', SpeechResult="What's my next meeting?")
    assert 'One moment' in first and '/twilio/voice/wait?n=0' in first      # answered at once, not after thinking
    text, channel = call.asked[0]
    assert channel == f'voice:{ME}' and 'may be driving' in text and text.endswith("What's my next meeting?")
    waiting = call.post('/twilio/voice/wait?n=0')
    assert '<Pause' in waiting and '/twilio/voice/wait?n=1' in waiting      # not ready yet: check back
    call.gate.set()
    for _ in range(100):
        if phone._calls['CA123']['reply']:
            break
        time.sleep(0.01)
    spoken = call.post('/twilio/voice/wait?n=1')
    assert 'Your next meeting is at 3 pm with Sam.' in spoken
    for junk in ('**', 'https://', '[', '- room'):
        assert junk not in spoken, junk
    assert 'Anything else?' in spoken and '<Gather' in spoken


def test_long_thinking_texts_the_answer_instead_of_holding_the_line(call):
    call.post('/twilio/voice', SpeechResult='Summarise my week')
    held = call.post(f'/twilio/voice/wait?n={phone.WAIT_STEPS}')
    assert 'text you the answer' in held and '<Gather' in held
    call.gate.set()
    for _ in range(200):
        if call.texts:
            break
        time.sleep(0.01)
    assert call.texts and call.texts[0][0] == ME and '3 pm' in call.texts[0][1]


@pytest.mark.parametrize('said', ['bye', "That's all.", 'goodbye', 'no thanks'])
def test_goodbye_hangs_up_without_asking_the_model(call, said):
    out = call.post('/twilio/voice', SpeechResult=said)
    assert '<Hangup/>' in out and call.asked == []


def test_strangers_and_forgeries_are_refused(call, monkeypatch):
    assert 'not authorized' in call.post('/twilio/voice', From='+15550000000', SpeechResult='hi')
    assert 'not authorized' in call.post('/twilio/voice/wait?n=0', From='+15550000000')
    with TestClient(server.app) as c:
        bad = c.post('/twilio/voice/wait?n=0', data={'From': ME, 'CallSid': 'CA123'},
                     headers={'X-Twilio-Signature': 'forged'})
    assert bad.status_code == 403
    # The signature covers the query string: a changed `n` is a forgery.
    params = {'From': ME, 'CallSid': 'CA123'}
    sig = webhook_auth.twilio_signature(TOKEN, BASE + '/twilio/voice/wait?n=0', params)
    with TestClient(server.app) as c:
        assert c.post('/twilio/voice/wait?n=19', data=params, headers={'X-Twilio-Signature': sig}).status_code == 403
    assert call.asked == []


def test_another_callers_call_id_is_not_theirs(call):
    call.post('/twilio/voice', SpeechResult='Read my notes')
    call.gate.set()
    phone._calls['CA123']['from'] = '+96171111111'          # the job belongs to someone else
    assert 'lost that one' in call.post('/twilio/voice/wait?n=0')


def test_spoken_text_is_clean_and_bounded():
    assert phone.spoken('# Title\n**bold** `x` see https://a.b/c') == 'Title bold x see a link'
    long = phone.spoken('Sentence one. ' * 300, limit=100)
    assert len(long) <= 100 and long.endswith('.')
