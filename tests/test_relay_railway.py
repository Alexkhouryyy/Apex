"""The relay on Railway (relay/Dockerfile, relay/start.sh, relay/railway.toml):
it listens on the PORT the host gives it, joins your tailnet with an auth
key, and hands calls to the PC through tailscaled's HTTP proxy."""
import http.server
import importlib.util
import json
import os
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location('relay_server_rw', ROOT / 'relay' / 'server.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_port_comes_from_the_host(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.delenv('RELAY_SERVER_PORT', raising=False)
    port = _port()
    monkeypatch.setenv('PORT', str(port))
    srv = mod.serve(host='127.0.0.1', db_path=str(tmp_path / 'r.db'))
    try:
        assert srv.server_address[1] == port
    finally:
        srv.server_close()


def test_calls_reach_the_pc_through_the_tailscale_proxy(tmp_path):
    seen = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_POST(self):                     # an HTTP proxy gets the absolute URL
            seen.append((self.path, self.headers.get('X-Twilio-Signature')))
            body = b'<Response><Say>Apex on the PC, via the tailnet.</Say></Response>'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    proxy = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    mod = _load()
    mod.TOKEN, mod.TWILIO_TOKEN, mod.CALLERS = 't', 'twilio', '+96170000000'
    mod.PUBLIC_URL = 'https://apex-relay.up.railway.app'
    mod.PC_URL = 'http://alex-pc.tail1234.ts.net'           # only reachable through the proxy
    mod.PC_PROXY = f'http://127.0.0.1:{proxy.server_address[1]}'
    srv = mod.serve(host='127.0.0.1', port=0, db_path=str(tmp_path / 'r.db'))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        params = {'From': '+96170000000', 'CallSid': 'CA1'}
        sig = mod.twilio_signature('twilio', mod.PUBLIC_URL + '/twilio/voice', params)
        req = urllib.request.Request(f'http://127.0.0.1:{srv.server_address[1]}/twilio/voice',
                                     data=urllib.parse.urlencode(params).encode(), method='POST',
                                     headers={'X-Twilio-Signature': sig})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
            twiml = r.read().decode()
        assert 'via the tailnet' in twiml
        assert seen == [('http://alex-pc.tail1234.ts.net/twilio/voice', sig)]   # same request, same signature
    finally:
        srv.shutdown()
        proxy.shutdown()


def test_start_script_joins_the_tailnet_and_serves(tmp_path):
    """start.sh for real, with stand-in tailscale binaries that record their arguments."""
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    log = tmp_path / 'ts.log'
    for name in ('tailscaled', 'tailscale'):
        f = bin_ / name
        f.write_text(f'#!/bin/sh\necho "{name} $*" >> {log}\n' + ('sleep 30\n' if name == 'tailscaled' else ''))
        f.chmod(0o755)
    work = tmp_path / 'relay'
    work.mkdir()
    for name in ('server.py', 'answer.py', 'start.sh'):
        (work / name).write_text((ROOT / 'relay' / name).read_text())
    port = _port()
    env = dict(os.environ, PATH=f'{bin_}:{os.environ["PATH"]}', TS_AUTHKEY='tskey-auth-test', PORT=str(port),
               RELAY_SERVER_TOKEN='t', RELAY_ANSWER='off', RELAY_SERVER_DB=str(tmp_path / 'r.db'),
               RELAY_SERVER_HOST='127.0.0.1')
    proc = subprocess.Popen(['sh', str(work / 'start.sh')], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        f'http://127.0.0.1:{port}/health', timeout=1) as r:
                    assert json.loads(r.read()) == {'ok': True}
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError(proc.stdout.read1(4000).decode() if proc.stdout else 'no output')
        calls = log.read_text()
        assert '--tun=userspace-networking' in calls and '--outbound-http-proxy-listen=127.0.0.1:1055' in calls
        assert 'tailscale up --auth-key=tskey-auth-test --hostname=apex-relay' in calls
    finally:
        proc.terminate()
        proc.wait(5)


def test_railway_config_points_at_the_relay_image():
    toml = (ROOT / 'relay' / 'railway.toml').read_text()
    assert 'dockerfilePath = "relay/Dockerfile"' in toml and 'healthcheckPath = "/health"' in toml
    docker = (ROOT / 'relay' / 'Dockerfile').read_text()
    assert 'tailscale/tailscale:stable' in docker and 'COPY relay/server.py relay/answer.py relay/start.sh' in docker
    assert 'RELAY_SERVER_DB=/data/relay.db' in docker
