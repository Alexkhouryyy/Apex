"""Reader preparation must not resume content or map downloads implicitly."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import httpx
import pytest

from agent import nomad_readers
from scripts import setup_apex_apocalypse as setup

FEED = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>fixture-book</id><title>Linux documentation</title></entry></feed>'''


def service(key='library', **changes):
    return dict(service_name=nomad_readers.READERS[key][1], installed=True,
                status='running', ui_location='8090', **changes)


@pytest.fixture
def prepared(monkeypatch):
    calls = []
    rows = []
    real = httpx.Client
    def handle(request):
        assert request.url.host == '127.0.0.1'
        assert 'authorization' not in request.headers
        calls.append((request.method, request.url.path, json.loads(request.content or b'null')))
        if request.url.path == '/api/health':
            return httpx.Response(200, json={'status':'ok'})
        if request.url.path == '/api/system/services':
            return httpx.Response(200, json=rows)
        if request.url.path == '/api/system/services/install':
            payload = json.loads(request.content)
            rows.append(dict(service_name=payload['service_name'], installed=True,
                             status='running', ui_location='8090'))
        if request.url.path == '/api/system/services/affect':
            rows[0]['status'] = 'running'
        if request.url.path == '/catalog/v2/entries':
            return httpx.Response(200, content=FEED)
        return httpx.Response(200, json={'success':True})
    monkeypatch.delenv('APEX_APOCALYPSE', raising=False)
    monkeypatch.setattr(setup.httpx,'Client',lambda **kwargs:real(transport=httpx.MockTransport(handle), **kwargs))
    return calls, rows


def test_default_prepares_only_library_and_checks_its_catalog(prepared, capsys):
    calls, _ = prepared
    setup.prepare_nomad_readers()
    posts = [(path, body) for method, path, body in calls if method == 'POST']
    assert posts == [('/api/system/services/install', {'service_name':'nomad_kiwix_server'}),
                     ('/api/zim/rescan-library', {})]
    assert calls[-1][1] == '/catalog/v2/entries'
    assert '1 titles listed' in capsys.readouterr().out


@pytest.mark.parametrize('flag',[True,1])
def test_existing_library_reuses_installation_and_remapped_port(prepared, capsys, flag):
    calls, rows = prepared
    rows.append(service())
    rows[0]['installed'] = flag
    rows[0]['ui_location'] = '9090'
    setup.prepare_nomad_readers()
    assert [path for method,path,_ in calls if method=='POST'] == ['/api/zim/rescan-library']
    assert 'http://127.0.0.1:9090' in capsys.readouterr().out


def test_stopped_library_is_started_without_reinstalling(prepared):
    calls, rows = prepared
    rows.append(service())
    rows[0]['status'] = 'exited'
    setup.prepare_nomad_readers()
    assert calls[2] == ('POST','/api/system/services/affect',{'service_name':'nomad_kiwix_server','action':'start'})
    assert not any(path.endswith('/install') for _,path,_ in calls)


@pytest.mark.parametrize('key', ['courses', 'notes', 'tools'])
def test_other_readers_do_not_rescan_library_or_prepare_maps(prepared, key):
    calls, _ = prepared
    setup.prepare_nomad_readers([key, key])
    assert [(path, body) for method,path,body in calls if method=='POST'] == [
        ('/api/system/services/install', {'service_name':nomad_readers.READERS[key][1]})]


def test_maps_require_explicit_selection_and_do_not_install_readers(prepared):
    calls, _ = prepared
    setup.prepare_nomad_readers([], maps=True)
    assert [path for method,path,_ in calls if method=='POST'] == [
        '/api/maps/download-base-assets','/api/maps/setup-world-basemap']


def test_accepted_install_request_is_not_claimed_as_running(monkeypatch, capsys):
    calls=[]; real=httpx.Client
    def handle(request):
        calls.append(request.url.path)
        data = {'status':'ok'} if request.url.path=='/api/health' else [] if request.method=='GET' else {'success':True}
        return httpx.Response(200, json=data)
    monkeypatch.delenv('APEX_APOCALYPSE',raising=False)
    monkeypatch.setattr(setup.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    monkeypatch.setattr(setup.time,'monotonic',Mock(side_effect=[0,601]))
    with pytest.raises(RuntimeError,match='not running yet'):
        setup.prepare_nomad_readers()
    assert '/api/zim/rescan-library' not in calls
    assert 'setup finished' not in capsys.readouterr().out


def test_offline_mode_cannot_prepare_readers(monkeypatch):
    monkeypatch.setenv('APEX_APOCALYPSE','1')
    monkeypatch.setattr(setup.httpx,'Client',lambda **kw:pytest.fail('Offline setup reached a client'))
    with pytest.raises(RuntimeError,match='online preparation'):
        setup.prepare_nomad_readers()


@pytest.mark.parametrize('body,state,ready',[(FEED,'available',True),
    (b'<feed xmlns="http://www.w3.org/2005/Atom"/>','empty',True),
    (b'<html>Other service</html>','not_answering',False)])
def test_reader_readiness_checks_real_catalog_shape_without_writes(body,state,ready):
    requests=[]
    def handle(request):
        assert request.method=='GET' and request.url.host=='127.0.0.1'
        assert 'authorization' not in request.headers
        requests.append(request)
        row=service();row['installed']=1
        return httpx.Response(200,json=[row]) if request.url.port==8080 else httpx.Response(200,content=body)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),follow_redirects=False) as client:
            return await nomad_readers.status(client,True)
    result=asyncio.run(run())
    library=result[0]
    assert library['state']==state and library['ready'] is ready
    assert library['content_ready'] is (state=='available')
    assert all(row['state']=='not_installed' for row in result[1:])
    assert len(requests)==2


@pytest.mark.parametrize('location',['https://example.com','http://127.0.0.1:8090','99999','0','../8090'])
def test_invalid_reader_addresses_are_never_probed(location):
    async def run():
        def handle(request):
            assert request.url.port==8080
            row=service();row['ui_location']=location
            return httpx.Response(200,json=[row])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await nomad_readers.status(client,True)
    assert asyncio.run(run())[0]['state']=='address_unavailable'


def test_unavailable_nomad_does_not_probe_any_readers():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:pytest.fail('Unexpected probe'))) as client:
            return await nomad_readers.status(client,False)
    assert all(row['state']=='nomad_unavailable' and not row['ready'] for row in asyncio.run(run()))


def test_reader_probe_does_not_follow_remote_redirect():
    async def run():
        def handle(request):
            assert request.url.host=='127.0.0.1'
            return httpx.Response(200,json=[service()]) if request.url.port==8080 else httpx.Response(302,headers={'Location':'https://example.com'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),follow_redirects=False) as client:
            return await nomad_readers.status(client,True)
    assert asyncio.run(run())[0]['state']=='not_answering'


@pytest.mark.parametrize('flags,readers,maps',[
    (['--nomad-readers'],['library'],False),
    (['--nomad-readers','--reader','courses'],['courses'],False),
    (['--nomad-apps'],list(nomad_readers.READERS),False),
    (['--nomad-maps'],[],True),
])
def test_cli_preparation_scope(monkeypatch,flags,readers,maps):
    import sys
    prepare=Mock();monkeypatch.setattr(setup,'prepare_nomad_readers',prepare)
    monkeypatch.setattr(sys,'argv',['setup',*flags])
    setup.main()
    prepare.assert_called_once_with(readers,maps=maps)


def test_reader_page_behavior():
    node=shutil.which('node')
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    if not node and bundled.is_file():node=str(bundled)
    if not node:pytest.skip('Node is required for the reader page behavior check')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([node,str(root/'scripts/check_apocalypse_readers.mjs')],cwd=root,
                          capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'no remote links or setup/download requests' in result.stdout
