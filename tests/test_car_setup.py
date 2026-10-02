"""Setup-Apex-Car.cmd (scripts/car_setup.py) with a fake Tailscale."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('car_setup', ROOT / 'scripts' / 'car_setup.py')
car = importlib.util.module_from_spec(spec)
spec.loader.exec_module(car)


class FakeTailscale:
    def __init__(self, state='Running', dns='alex-pc.tail1234.ts.net.', serve_rc=0, serve_err=''):
        self.state, self.dns, self.serve_rc, self.serve_err, self.calls = state, dns, serve_rc, serve_err, []

    def __call__(self, args, **kw):
        self.calls.append(args[1:])
        if args[1] == 'status':
            return subprocess.CompletedProcess(args, 0, json.dumps({'BackendState': self.state, 'Self': {'DNSName': self.dns}}), '')
        return subprocess.CompletedProcess(args, self.serve_rc, '', self.serve_err)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(car, 'ROOT', tmp_path)
    monkeypatch.setattr(car, 'find_tailscale', lambda: 'tailscale')
    monkeypatch.delenv('DASHBOARD_TOKEN', raising=False)
    return tmp_path


def test_serves_apex_and_prints_the_car_address(home, capsys):
    (home / '.env').write_text('DASHBOARD_TOKEN=abc123\n')
    ts = FakeTailscale()
    assert car.main([], run=ts) == 0
    assert ['serve', '--bg', '7860'] in ts.calls
    assert 'https://alex-pc.tail1234.ts.net/drive' in capsys.readouterr().out


def test_refuses_without_a_token_unless_you_let_it_make_one(home, capsys):
    ts = FakeTailscale()
    assert car.main([], run=ts, ask=lambda q: 'n') == 1
    assert not any(c[0] == 'serve' for c in ts.calls)                       # never exposed without a password
    (home / '.env').write_text('OTHER=1')
    assert car.main([], run=ts, ask=lambda q: 'y') == 0
    text = (home / '.env').read_text()
    assert text.startswith('OTHER=1\nDASHBOARD_TOKEN=') and len(car.env_token(home / '.env')) >= 20
    # An existing token is never replaced.
    before = car.env_token(home / '.env')
    car.main([], run=ts, ask=lambda q: 'y')
    assert car.env_token(home / '.env') == before


def test_explains_what_is_missing(home, capsys, monkeypatch):
    (home / '.env').write_text('DASHBOARD_TOKEN=abc\n')
    assert car.main([], run=FakeTailscale(state='NeedsLogin')) == 1
    assert 'not signed in' in capsys.readouterr().out
    assert car.main([], run=FakeTailscale(serve_rc=1, serve_err='HTTPS certificates are not enabled')) == 1
    assert 'admin/dns' in capsys.readouterr().out
    monkeypatch.setattr(car, 'find_tailscale', lambda: None)
    assert car.main([]) == 1 and 'tailscale.com/download' in capsys.readouterr().out


def test_off_turns_it_off(home):
    ts = FakeTailscale()
    assert car.main(['--off'], run=ts) == 0 and ts.calls == [['serve', '--https=443', 'off']]
