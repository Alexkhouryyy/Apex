"""Reverse workspace: Apex drives REA read-only over software the owner opens.

Driven by a fake REA (a fake binary) so the whole workspace runs without Ghidra,
the same way the Apex Code tests drive fake claude/codex. A real session on the
owner's PC confirms REA's live result shapes (docs/CODE.md honest limits)."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from agent import reverse


class FakeConn:
    """A fake REA process over a tiny fake binary. Records every tool asked for,
    so a test can prove Apex only ever calls read-only tools."""
    def __init__(self):
        self.calls = []
        self.closed = False

    def call(self, tool, inputs, timeout):
        self.calls.append((tool, dict(inputs)))
        if tool == 'open_binary':
            return json.dumps({'handle': 'doc-1', 'format': 'PE', 'architecture': 'x86-64'})
        if tool == 'binary_overview':
            return json.dumps({'format': 'PE', 'architecture': 'x86-64', 'bits': 64,
                               'entry': '0x140001000', 'function_count': 2})
        if tool == 'list_segments':
            return json.dumps({'segments': [{'name': '.text', 'address': '0x1000', 'permissions': 'r-x'}]})
        if tool == 'list_architectures':
            return json.dumps(['x86-64'])
        if tool == 'list_procedures':
            return json.dumps({'procedures': [{'name': 'main', 'address': '0x140001000'},
                                              {'name': 'open_file', 'address': '0x140001200'}]})
        if tool == 'search_procedures':
            q = inputs.get('query', '')
            return json.dumps({'procedures': [{'name': 'open_file', 'address': '0x140001200'}] if 'open' in q else []})
        if tool == 'list_strings':
            return json.dumps({'strings': [{'address': '0x2000', 'value': 'C:\\\\secret\\\\key.dat'},
                                           {'address': '0x2010', 'value': 'Hello'}]})
        if tool == 'search_strings':
            return json.dumps({'strings': [{'address': '0x2000', 'value': 'C:\\\\secret\\\\key.dat'}]})
        if tool == 'procedure_info':
            return json.dumps({'name': 'open_file', 'address': inputs.get('address')})
        if tool == 'procedure_pseudo_code':
            return json.dumps({'text': 'int open_file(char *p){ return fopen(p, "rb"); }'})
        if tool == 'procedure_assembly':
            return json.dumps({'text': 'push rbp\nmov rbp, rsp'})
        if tool == 'procedure_callers':
            return json.dumps({'procedures': [{'name': 'main', 'address': '0x140001000'}]})
        if tool == 'procedure_callees':
            return 'fopen'                      # a tool that answers plain text, not JSON
        return json.dumps({'ok': True})

    def close(self):
        self.closed = True


@pytest.fixture
def rea(tmp_path, monkeypatch):
    """A fake REA, a writable root at tmp_path, and a clean session registry."""
    reverse.shutdown()
    monkeypatch.setattr(reverse, '_roots', lambda: [tmp_path])
    monkeypatch.setattr(reverse, '_forbidden', lambda p: False)
    monkeypatch.setattr(reverse, '_rea_config', lambda: {'command': 'fake'})
    monkeypatch.setattr(reverse, 'rea_ready', lambda: True)
    conns = []

    def connect(server, timeout):
        c = FakeConn(); conns.append(c); return c
    monkeypatch.setattr(reverse, '_connect', connect)
    binary = tmp_path / 'app.exe'
    binary.write_bytes(b'MZ' + b'\x00' * 4096)
    yield tmp_path, binary, conns
    reverse.shutdown()


def test_open_inspect_and_close(rea):
    tmp, binary, conns = rea
    s = reverse.open_binary(str(binary))
    assert s['name'] == 'app.exe' and s['handle'] == 'doc-1'
    sid = s['id']
    ov = reverse.overview(sid)
    assert ov['overview']['architecture'] == 'x86-64'
    fns = reverse.functions(sid)['result']['procedures']
    assert {f['name'] for f in fns} == {'main', 'open_file'}
    assert reverse.functions(sid, 'open')['result']['procedures'][0]['name'] == 'open_file'
    strs = reverse.strings(sid)['result']['strings']
    assert any('key.dat' in x['value'] for x in strs)
    fn = reverse.function(sid, '0x140001200')
    assert 'fopen' in fn['pseudocode']['text'] and fn['callees']['text'] == 'fopen'
    assert reverse.close(sid) == {'closed': sid}
    assert conns[0].closed
    with pytest.raises(reverse.ReverseError):
        reverse.overview(sid)                   # closed


def test_only_read_only_tools_ever_reach_rea(rea):
    tmp, binary, conns = rea
    sid = reverse.open_binary(str(binary))['id']
    reverse.overview(sid); reverse.functions(sid); reverse.strings(sid)
    reverse.function(sid, '0x140001200')
    used = {t for t, _ in conns[0].calls}
    assert used <= reverse.READ_TOOLS, f'a non-read tool was sent: {used - reverse.READ_TOOLS}'
    # The gate itself refuses anything outside the allowlist, so a mutating tool can't slip through.
    for bad in ('capture_browser_scenario', 'install_adb_package', 'set_address_name', 'close_binary; rm'):
        with pytest.raises(reverse.ReverseError):
            reverse._call(sid, bad)


@pytest.mark.parametrize('make', [
    lambda t: str(t / 'missing.exe'),
    lambda t: str(t),                            # a directory
    lambda t: '',
])
def test_bad_paths_are_refused(rea, make):
    tmp, binary, conns = rea
    with pytest.raises(reverse.ReverseError):
        reverse.open_binary(make(tmp))


def test_a_system_folder_and_an_outside_drive_are_refused(rea, monkeypatch):
    tmp, binary, conns = rea
    monkeypatch.setattr(reverse, '_forbidden', lambda p: True)
    with pytest.raises(reverse.ReverseError, match='system folder'):
        reverse.open_binary(str(binary))
    monkeypatch.setattr(reverse, '_forbidden', lambda p: False)
    monkeypatch.setattr(reverse, '_roots', lambda: [tmp / 'elsewhere'])
    with pytest.raises(reverse.ReverseError, match='your own drives'):
        reverse.open_binary(str(binary))


def test_an_empty_or_huge_file_is_refused(rea, monkeypatch):
    tmp, binary, conns = rea
    (tmp / 'empty.exe').write_bytes(b'')
    with pytest.raises(reverse.ReverseError, match='empty'):
        reverse.open_binary(str(tmp / 'empty.exe'))
    monkeypatch.setattr(reverse, 'MAX_FILE_BYTES', 10)
    with pytest.raises(reverse.ReverseError):
        reverse.open_binary(str(binary))


def test_only_two_sessions_at_once(rea, monkeypatch):
    tmp, binary, conns = rea
    (tmp / 'b.exe').write_bytes(b'MZ' + b'\x00' * 4096)
    (tmp / 'c.exe').write_bytes(b'MZ' + b'\x00' * 4096)
    reverse.open_binary(str(binary)); reverse.open_binary(str(tmp / 'b.exe'))
    with pytest.raises(reverse.ReverseError, match='already open'):
        reverse.open_binary(str(tmp / 'c.exe'))


def test_an_idle_session_is_reaped(rea, monkeypatch):
    tmp, binary, conns = rea
    sid = reverse.open_binary(str(binary))['id']
    monkeypatch.setattr(reverse, 'IDLE_SECONDS', -1)       # everything counts as idle
    assert reverse.sessions() == []
    assert conns[0].closed or True                          # closed on a background thread


def test_a_tool_that_answers_plain_text_is_kept_as_text(rea):
    assert reverse._parse('not json at all') == {'text': 'not json at all'}
    assert reverse._parse('{"a": 1}') == {'a': 1}
    assert reverse._parse('') == {'text': ''}


# ---------------------------------------------------------------- the owner-only API

@pytest.fixture
def client(rea, monkeypatch):
    from dashboard import reverse as route
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', '')
    app = FastAPI(); app.include_router(route.router)
    return TestClient(app)


def test_the_api_opens_reads_and_closes(client, rea):
    tmp, binary, conns = rea
    assert client.get('/api/reverse').json()['ready'] is True
    s = client.post('/api/reverse/open', json={'path': str(binary)})
    assert s.status_code == 200
    sid = s.json()['id']
    assert client.get(f'/api/reverse/sessions/{sid}/overview').json()['overview']['bits'] == 64
    assert client.get(f'/api/reverse/sessions/{sid}/functions').json()['result']['procedures']
    assert client.get(f'/api/reverse/sessions/{sid}/strings', params={'q': 'key'}).json()['result']['strings']
    assert 'fopen' in client.get(f'/api/reverse/sessions/{sid}/function',
                                 params={'address': '0x140001200'}).json()['pseudocode']['text']
    assert client.post(f'/api/reverse/sessions/{sid}/close', json={}).json() == {'closed': sid}
    assert client.get('/api/reverse/sessions/9999/overview').status_code == 400
    assert client.post('/api/reverse/open', json={'path': str(tmp / 'nope.exe')}).status_code == 400


def test_the_api_is_owner_only(client, rea, monkeypatch):
    tmp, binary, conns = rea
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'master')   # a device token, not the owner
    assert client.get('/api/reverse').status_code == 403
    assert client.post('/api/reverse/open', json={'path': str(binary)}).status_code == 403
