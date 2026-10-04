import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import apocalypse_model_transfer as transfer
from scripts import setup_apex_apocalypse as setup


def layer(data=b'model bytes'):
    return dict(digest='sha256:'+hashlib.sha256(data).hexdigest(), size=len(data))


def test_manifest_retains_official_model_template_parameters_and_license():
    raw, rows = transfer.pinned_manifest()
    assert hashlib.sha256(raw).hexdigest() == transfer.PIN
    assert len(rows) == 5
    assert sum(row['size'] for row in rows) == 2_497_293_931
    assert any(row['mediaType'].endswith('.license') for row in rows)
    assert any(row['mediaType'].endswith('.template') for row in rows)


def test_curl_resumes_own_partial_without_consuming_ollama_preallocation(tmp_path, monkeypatch):
    row = layer(); blob = tmp_path/'blobs'/row['digest'].replace(':', '-')
    blob.parent.mkdir()
    original = Path(str(blob)+'-partial');original.write_bytes(b'\0'*row['size'])
    partial = Path(str(blob)+'.apex-curl.part');partial.write_bytes(b'model ')
    calls=[]
    def run(args):
        calls.append(args)
        assert '--continue-at' in args and '--retry' not in args
        assert Path(args[args.index('--output')+1]) == partial
        with partial.open('ab') as stream:stream.write(b'bytes')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(transfer.subprocess,'run',run)
    transfer.fetch_blob('curl',tmp_path,row)
    assert blob.read_bytes() == b'model bytes'
    assert original.read_bytes() == b'\0'*row['size']
    assert len(calls) == 1


def test_verified_cached_blob_needs_no_download(tmp_path, monkeypatch):
    row=layer();path=tmp_path/'blobs'/row['digest'].replace(':','-')
    path.parent.mkdir();path.write_bytes(b'model bytes')
    monkeypatch.setattr(transfer.subprocess,'run',lambda _:pytest.fail('Unnecessary network'))
    transfer.fetch_blob('curl',tmp_path,row)


def test_corrupt_complete_partial_is_preserved(tmp_path, monkeypatch):
    row=layer();path=tmp_path/'blobs'/row['digest'].replace(':','-')
    path.parent.mkdir();partial=Path(str(path)+'.apex-curl.part');partial.write_bytes(b'\0'*row['size'])
    monkeypatch.setattr(transfer.subprocess,'run',lambda _:pytest.fail('Unnecessary network'))
    with pytest.raises(RuntimeError,match='checksum'):
        transfer.fetch_blob('curl',tmp_path,row)
    assert not path.exists() and partial.is_file()


def test_failed_transfer_preserves_partial_and_never_writes_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer.shutil,'which',lambda _:'curl')
    monkeypatch.setattr(transfer.time,'sleep',lambda _:None)
    def run(args):
        path=Path(args[args.index('--output')+1]);path.write_bytes(b'part')
        return SimpleNamespace(returncode=28)
    monkeypatch.setattr(transfer.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='Partial files were kept'):
        transfer.prepare_store(tmp_path)
    assert not (tmp_path/'models/manifests').exists()
    assert list((tmp_path/'models/blobs').glob('*.part'))


def test_manifest_is_published_only_after_all_verified_layers(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer.shutil,'which',lambda _:'curl')
    calls=[]
    def fetch(_, root, row):
        assert not (root/'manifests/registry.ollama.ai/library/qwen3/4b').exists()
        calls.append(row)
    monkeypatch.setattr(transfer,'fetch_blob',fetch)
    transfer.prepare_store(tmp_path)
    assert [row['size'] for row in calls] == sorted(row['size'] for row in calls)
    assert (tmp_path/'models/manifests/registry.ollama.ai/library/qwen3/4b').read_bytes() == transfer.pinned_manifest()[0]


def test_curl_setup_requires_local_response_before_claiming_ready(tmp_path, monkeypatch):
    import httpx
    calls=[]
    monkeypatch.setattr(setup.shutil,'disk_usage',lambda _:SimpleNamespace(free=10**12))
    monkeypatch.setattr(setup,'ensure_ollama',lambda *_:None)
    monkeypatch.setattr(transfer,'prepare_store',lambda _:calls.append('store'))
    monkeypatch.setattr(setup.apocalypse,'verify_model',lambda *_:calls.append('verify'))
    real=httpx.Client
    def handle(request):
        import json
        assert request.url.path == '/api/generate'
        assert json.loads(request.content)['options'] == {'num_predict':128,'num_ctx':2048,'num_batch':128}
        return httpx.Response(200,json={'response':''})
    monkeypatch.setattr(setup.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(RuntimeError,match='local test response'):
        setup.prepare_model(tmp_path,'http://127.0.0.1:11435','qwen3:4b',transport='curl')
    assert calls == ['store','verify']
    assert not (tmp_path/'prepared-model.json').exists()
