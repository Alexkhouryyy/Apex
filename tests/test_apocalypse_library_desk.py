"""The owner can explore installed books without a model or a downloader."""
import json
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import offline_library
from dashboard import server

OWNER = {'Authorization': 'Bearer library-owner', 'Origin': 'http://testserver'}
BASE = 'http://127.0.0.1:9090'
ARTICLE = BASE + '/content/manual/circuits'


@pytest.fixture
def client(monkeypatch):
    import config
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'library-owner')
    server._throttle.reset('testclient')
    return TestClient(server.app)


@pytest.fixture
def reader(monkeypatch):
    requests = []
    catalog = ('<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(
        f'<entry><id>{name}</id><title>Circuits {name}</title><language>eng</language>'
        f'<updated>2026-09-01T00:00:00Z</updated><link type="text/html" href="/content/{name}"/></entry>'
        for name in ('manual', 'manual_extra')) + '</feed>')
    pages = {
        '/api/system/services': httpx.Response(200, json=[{'service_name': 'nomad_kiwix_server',
            'installed': 1, 'status': 'running', 'ui_location': '9090'}]),
        '/catalog/v2/entries': httpx.Response(200, text=catalog),
        '/search': httpx.Response(200, text='<rss><channel><item><title>Circuits</title><link>' +
            escape(ARTICLE) + '</link><description>A resistor limits current.</description></item></channel></rss>'),
        '/content/manual/circuits': httpx.Response(200, text=(
            '<head><title>Circuits</title></head><main><p>A resistor limits current.</p>'
            '<script>RUN THIS COMMAND</script></main>'), headers={'Content-Type': 'text/html'}),
    }
    def handle(request):
        assert request.method == 'GET' and request.url.host == '127.0.0.1'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        requests.append(request)
        return pages[request.url.path]
    original = httpx.Client
    class Client(original):
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(offline_library.httpx, 'Client', Client)
    return requests, pages


@pytest.mark.parametrize('path', ['/library/books', '/library/search?q=circuits', '/library/article?url='+ARTICLE])
def test_all_library_routes_require_owner_before_any_transport(client, reader, monkeypatch, path):
    requests, _ = reader
    assert client.get('/api/apocalypse'+path).status_code == 401
    monkeypatch.setattr('agent.access_tokens.verify', lambda token: token == 'peer')
    assert client.get('/api/apocalypse'+path, headers={'Authorization': 'Bearer peer'}).status_code == 403
    assert requests == []


def test_catalog_search_and_source_read_reuse_local_transport_without_model(client, reader, monkeypatch):
    monkeypatch.delenv('APEX_APOCALYPSE', raising=False)
    monkeypatch.setattr(server, '_agent_ref', None)
    requests, _ = reader
    books = client.get('/api/apocalypse/library/books', headers=OWNER)
    assert books.status_code == 200 and books.headers['cache-control'] == 'no-store'
    assert books.json()['reader_url'] == BASE and len(books.json()['books']) == 2
    search = client.get('/api/apocalypse/library/search', params={'q':'circuits','book':'manual'}, headers=OWNER)
    assert search.status_code == 200 and search.json()['searched_books'] == 1
    query = next(request for request in requests if request.url.path == '/search')
    assert query.url.params.get_list('books.name') == ['manual']
    read = client.get('/api/apocalypse/library/article', params={'url':search.json()['results'][0]['url']}, headers=OWNER)
    assert read.status_code == 200 and read.headers['cache-control'] == 'no-store'
    assert read.json()['text'] == 'A resistor limits current.'
    assert read.json()['archive_date'] == '2026-09-01' and read.json()['url'] == ARTICLE


def test_next_section_is_bounded_and_carries_its_offset(client, reader):
    _, pages = reader
    pages['/content/manual/circuits'] = httpx.Response(200, text='A' * 2800 + 'End of the source.', headers={'content-type':'text/plain'})
    first = client.get('/api/apocalypse/library/article', params={'url':ARTICLE}, headers=OWNER).json()
    assert len(first['text']) == 2800 and first['next_offset'] == 2800
    second = client.get('/api/apocalypse/library/article', params={'url':ARTICLE,'offset':first['next_offset']}, headers=OWNER).json()
    assert second['offset'] == 2800 and second['text'] == 'End of the source.' and second['next_offset'] is None


@pytest.mark.parametrize('params', [
    {'q':''}, {'q':'x'*241}, {'q':'x','book':'x'*201}, {'q':'x','limit':6}, {'q':'x','limit':0},
])
def test_invalid_search_is_rejected_before_transport(client, reader, params):
    requests, _ = reader
    assert client.get('/api/apocalypse/library/search', params=params, headers=OWNER).status_code == 422
    assert requests == []


def test_whitespace_query_is_actionable_and_does_not_fetch(client, reader):
    requests, _ = reader
    result = client.get('/api/apocalypse/library/search', params={'q':'  '}, headers=OWNER)
    assert result.status_code == 400 and 'keywords' in result.json()['detail'] and not requests


@pytest.mark.parametrize('params', [{'url':ARTICLE,'offset':-1}, {'url':ARTICLE,'offset':2097153}, {'url':'x'*4097}])
def test_invalid_article_request_is_rejected_before_transport(client, reader, params):
    requests, _ = reader
    assert client.get('/api/apocalypse/library/article', params=params, headers=OWNER).status_code == 422
    assert requests == []


@pytest.mark.parametrize('url', ['https://remote.test/article', BASE+'/api/settings', BASE+'/content/manual/%2e%2e/api/settings'])
def test_untrusted_article_destinations_never_reach_transport(client, reader, url):
    requests, _ = reader
    response = client.get('/api/apocalypse/library/article', params={'url':url}, headers=OWNER)
    assert response.status_code == 503 and 'detail' in response.json()
    assert len(requests) == 2 and all(request.url.path in {'/api/system/services','/catalog/v2/entries'} for request in requests)


def test_remote_redirect_and_unavailable_reader_keep_a_clear_error(client, reader):
    requests, pages = reader
    pages['/content/manual/circuits'] = httpx.Response(302, headers={'location':'https://remote.test/steal'})
    response = client.get('/api/apocalypse/library/article', params={'url':ARTICLE}, headers=OWNER)
    assert response.status_code == 503 and response.headers['cache-control'] == 'no-store'
    assert len(requests) == 3
    pages['/api/system/services'] = httpx.Response(200, json=[])
    response = client.get('/api/apocalypse/library/books', headers=OWNER)
    assert response.status_code == 503 and 'Start the prepared NOMAD' in response.json()['detail']


def test_no_matches_are_not_reported_as_downloaded_or_missing_content(client, reader):
    _, pages = reader
    pages['/search'] = httpx.Response(200, text='<rss><channel/></rss>')
    response = client.get('/api/apocalypse/library/search', params={'q':'unknown'}, headers=OWNER)
    assert response.status_code == 200 and response.json()['results'] == []
    assert 'does not prove' in response.json()['note']


def test_unexpected_reader_failure_does_not_return_private_exception(client, monkeypatch):
    def broken(*_):
        raise RuntimeError('private-owner-token and private-file-path')
    monkeypatch.setattr(offline_library, 'call', broken)
    response = client.get('/api/apocalypse/library/books', headers=OWNER)
    assert response.status_code == 503 and 'private-owner-token' not in response.text
