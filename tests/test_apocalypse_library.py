import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scripts import prepare_apocalypse_library as library
from scripts import setup_apex_apocalypse as setup


def archive(data=b'complete archive'):
    return {'id':'book', 'title':'Book', 'bytes':len(data), 'url':'https://mirror.test/book.zim',
            'path':'nomad/storage/zim/book.zim', 'hash_algorithm':'sha256',
            'hash':hashlib.sha256(data).hexdigest(), 'status':'pending'}


def test_small_batch_excludes_large_wikipedia_and_maps():
    jobs = [dict(id='worldwide-map', bytes=138_000_000_000),
            dict(id='wiki', bytes=127_000_000_000),
            dict(id='reference', bytes=700_000_000),
            dict(id='guide', bytes=200_000_000),
            dict(id='extra', bytes=150_000_000)]
    selected = library.select_jobs(jobs, max_download_gb=1)
    assert {row['id'] for row in selected} == {'guide', 'extra'}
    assert sum(row['bytes'] for row in selected) <= 1_000_000_000
    assert len(jobs) == 5


@pytest.mark.parametrize('limit', [0, -1, float('nan'), float('inf')])
def test_small_batch_refuses_invalid_limits(limit):
    with pytest.raises(RuntimeError):
        library.select_jobs([archive()], max_download_gb=limit)


def test_selection_refuses_unknown_id_and_too_small_budget():
    with pytest.raises(RuntimeError, match='Unknown archive'):
        library.select_jobs([archive()], ['wrong'])
    with pytest.raises(RuntimeError, match='No complete archive'):
        library.select_jobs([archive()], max_download_gb=1e-9)
    assert library.select_jobs([archive()], ['book']) == [archive()]


@pytest.fixture(autouse=True)
def space(monkeypatch):
    monkeypatch.setattr(library.shutil,'disk_usage',lambda _:SimpleNamespace(free=10**12))


def test_english_profile_deduplicates_inherited_books_and_flavours():
    def tier(slug, rows, parent=None):
        return dict(slug=slug,resources=[{'id':row} for row in rows],includesTier=parent)
    spec={'categories':[
        {'slug':'one','language':'en','tiers':[tier('basic',['shared','wikibooks_en_all_nopic']),
            tier('one-comprehensive',['full','wikibooks_en_all_maxi'],'basic')]},
        {'slug':'two','language':'en','tiers':[tier('two-comprehensive',['shared'])]},
        {'slug':'french','language':'fr','tiers':[tier('fr-comprehensive',['fr'])]}]}
    ids=[r['id'] for r in library.english_resources(spec)]
    assert ids==['shared','full','wikibooks_en_all_maxi','wikipedia_en_all_maxi']


def test_tier_cycle_is_rejected():
    spec={'categories':[{'slug':'bad','language':'en','tiers':[{'slug':'bad-comprehensive',
          'includesTier':'bad-comprehensive','resources':[]}]}]}
    with pytest.raises(RuntimeError,match='inheritance'):library.english_resources(spec)


def test_catalog_uses_exact_filename_and_publisher_size_and_hash():
    calls=[]
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('entries'):
            if request.url.params['name']=='book_en_all_maxi':
                return httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom"/>')
            return httpx.Response(200,text='''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
              <link type="application/x-zim" href="https://mirror.test/other_2026-10.zim.meta4" length="999"/>
              <link type="application/x-zim" href="https://mirror.test/book_en_all_maxi_2026-08.zim.meta4" length="1000"/>
              </entry></feed>''')
        return httpx.Response(200,text='''<metalink xmlns="urn:ietf:params:xml:ns:metalink"><file>
             <size>999</size><hash type="sha-256">abc</hash></file></metalink>''')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        row=library.latest_zim(client,{'id':'book_en_all_maxi'})
    assert row['bytes']==999 and row['hash']=='abc'
    assert row['url'].endswith('book_en_all_maxi_2026-08.zim')
    assert len(calls)==3


def test_interrupted_download_resumes_at_actual_partial_length(tmp_path):
    data=b'complete archive';job=archive(data);target=library.destination(tmp_path,job)
    target.parent.mkdir(parents=True);partial=target.with_suffix('.zim.part');partial.write_bytes(data[:5])
    job['etag']='"version"';saved=[]
    def handle(request):
        assert request.headers['range']=='bytes=5-'
        assert request.headers['if-range']=='"version"'
        return httpx.Response(206,content=data[5:],headers={'Content-Range':f'bytes 5-{len(data)-1}/{len(data)}'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        library.download_archive(client,tmp_path,job,lambda:saved.append(dict(job)))
    assert target.read_bytes()==data and not partial.exists()
    assert saved[-1]['status']=='downloaded' and saved[-1]['checksum_verified'] is True


def test_mirror_ignoring_range_restarts_without_appending(tmp_path):
    data=b'complete archive';job=archive(data);target=library.destination(tmp_path,job)
    target.parent.mkdir(parents=True);target.with_suffix('.zim.part').write_bytes(data[:5])
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=data))) as client:
        library.download_archive(client,tmp_path,job,lambda:None)
    assert target.read_bytes()==data


@pytest.mark.parametrize('response',[httpx.Response(206,content=b'wrong',headers={'Content-Range':'bytes 4-8/99'}),
                                    httpx.Response(200,content=b'too short')])
def test_changed_archive_or_wrong_range_preserves_partial(tmp_path,response):
    job=archive();target=library.destination(tmp_path,job);target.parent.mkdir(parents=True)
    partial=target.with_suffix('.zim.part');partial.write_bytes(b'part')
    with httpx.Client(transport=httpx.MockTransport(lambda _:response)) as client:
        with pytest.raises(RuntimeError):library.download_archive(client,tmp_path,job,lambda:None)
    assert partial.read_bytes()==b'part' and not target.exists()
    assert job['status']!='downloaded'


def test_wrong_checksum_never_marks_downloaded(tmp_path):
    job=archive(b'correct');target=library.destination(tmp_path,job)
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'corrupt'))) as client:
        with pytest.raises(RuntimeError,match='checksum'):library.download_archive(client,tmp_path,job,lambda:None)
    assert not target.exists() and target.with_suffix('.zim.part').read_bytes()==b'corrupt'
    assert job['status']!='downloaded'


def test_completed_archive_is_reverified_without_network(tmp_path):
    data=b'cached';job=archive(data);target=library.destination(tmp_path,job)
    target.parent.mkdir(parents=True);target.write_bytes(data)
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('Unnecessary download'))) as client:
        library.download_archive(client,tmp_path,job,lambda:None)
    assert job['status']=='downloaded' and job['checksum_verified']
    target.write_bytes(b'broken')
    with httpx.Client() as client:
        with pytest.raises(RuntimeError,match='preserved'):library.download_archive(client,tmp_path,job,lambda:None)
    assert target.read_bytes()==b'broken'


def test_full_partial_can_finish_without_redownload(tmp_path):
    job=archive();target=library.destination(tmp_path,job);target.parent.mkdir(parents=True)
    target.with_suffix('.zim.part').write_bytes(b'complete archive')
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('Unnecessary download'))) as client:
        library.download_archive(client,tmp_path,job,lambda:None)
    assert target.is_file() and job['checksum_verified']


def test_disk_headroom_stops_before_network(tmp_path,monkeypatch):
    monkeypatch.setattr(library.shutil,'disk_usage',lambda _:SimpleNamespace(free=library.HEADROOM))
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('Download started without space'))) as client:
        with pytest.raises(RuntimeError,match='space'):library.download_archive(client,tmp_path,archive(),lambda:None)


def test_destination_escape_rejected_and_partial_count_uses_disk(tmp_path):
    job=archive();job['path']='../../other.zim'
    with pytest.raises(ValueError):library.destination(tmp_path,job)
    job=archive();target=library.destination(tmp_path,job);target.parent.mkdir(parents=True)
    target.with_suffix('.zim.part').write_bytes(b'first')
    assert library.remaining_bytes(tmp_path,[job])==job['bytes']-5


def test_saved_metadata_does_not_claim_offline_verification(tmp_path):
    plan={'jobs':[archive()], 'offline_verified':False}
    path=tmp_path/'plan.json';library.save_plan(path,plan)
    saved=json.loads(path.read_text());assert saved['updated_at']>0
    assert not saved['offline_verified'] and saved['jobs'][0]['status']=='pending'


def test_nomad_readers_use_original_endpoints_and_leave_existing_ai_alone(monkeypatch):
    calls=[];real=httpx.Client
    def handle(request):
        payload=json.loads(request.content) if request.content else None
        calls.append((request.method,request.url.path,payload))
        assert request.url.host=='127.0.0.1' and 'authorization' not in request.headers
        if request.url.path=='/api/health':return httpx.Response(200,json={'status':'ok'})
        if request.url.path=='/api/system/services':return httpx.Response(200,json=[
            {'service_name':'nomad_kiwix_server','installed':True,'status':'running'},
            {'service_name':'nomad_flatnotes','installed':True,'status':'exited'}])
        return httpx.Response(200,json={'success':True})
    monkeypatch.delenv('APEX_APOCALYPSE',raising=False)
    monkeypatch.setattr(setup.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    setup.prepare_nomad_readers()
    installed=[p['service_name'] for _,path,p in calls if path.endswith('/install')]
    assert installed==['nomad_kolibri_2','nomad_cyberchef']
    assert ('POST','/api/system/services/affect',{'service_name':'nomad_flatnotes','action':'start'}) in calls
    assert any(path=='/api/zim/rescan-library' for _,path,_ in calls)
    assert any(path=='/api/maps/setup-world-basemap' for _,path,_ in calls)
    assert not any('ollama' in path or (p and p.get('service_name')=='nomad_ollama') for _,path,p in calls)


def test_reader_installation_error_is_not_success(monkeypatch):
    real=httpx.Client
    def handle(request):
        if request.url.path=='/api/health':return httpx.Response(200,json={'status':'ok'})
        if request.url.path=='/api/system/services':return httpx.Response(200,json=[])
        return httpx.Response(200,json={'success':False,'message':'Docker disk full'})
    monkeypatch.delenv('APEX_APOCALYPSE',raising=False)
    monkeypatch.setattr(setup.httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(RuntimeError,match='disk full'):setup.prepare_nomad_readers()


def test_offline_mode_cannot_install_readers(monkeypatch):
    monkeypatch.setenv('APEX_APOCALYPSE','1')
    with pytest.raises(RuntimeError,match='online'):setup.prepare_nomad_readers()


def test_progress_is_snapshot_and_does_not_claim_ready_readers(monkeypatch,tmp_path):
    from dashboard import apocalypse as api
    monkeypatch.setenv('APEX_APOCALYPSE_HOME',str(tmp_path))
    row=archive();row.update(status='downloaded',checksum_verified=True)
    plan={'profile':'english-worldwide','jobs':[row],'remaining_setup':[{'id':'courses','reason':'Import first'}]}
    library.save_plan(tmp_path/library.PLAN_NAME,plan)
    report=api.preparation()
    assert report['downloaded']==1 and report['total']==1 and not report['offline_verified']
    assert report['remaining_setup'][0]['id']=='courses'
    (tmp_path/library.PLAN_NAME).write_text('{broken')
    assert 'error' in api.preparation()
