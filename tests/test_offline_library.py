"""Real tool dispatch and bounded local retrieval, without live model/network calls."""
import json
from types import SimpleNamespace as NS
from xml.sax.saxutils import escape

import httpx
from openai import OpenAI
import pytest

from agent import offline_library as library

BOOK = 'devdocs_en_python_2026-08'
BASE = 'http://127.0.0.1:9090'
ARTICLE = BASE + '/content/' + BOOK + '/howto/sorting'


def catalog(names=(BOOK,)):
    entries = ''.join(f'<entry><id>{name}</id><title>Python Docs</title><language>eng</language>'
                      f'<updated>2026-08-01T00:00:00Z</updated>'
                      f'<link type="text/html" href="/content/{name}"/></entry>' for name in names)
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


def search_feed(url=ARTICLE, description='<b>sorted()</b> returns a new list.'):
    return ('<rss><channel><item><title>Sorting Techniques</title><link>' + escape(url) +
            '</link><description>' + escape(description) + '</description></item></channel></rss>')


@pytest.fixture
def reader(monkeypatch):
    calls = []
    pages = {
        '/api/system/services': httpx.Response(200, json=[{'service_name': 'nomad_kiwix_server',
            'installed': 1, 'status': 'running', 'ui_location': '9090'}]),
        '/catalog/v2/entries': httpx.Response(200, text=catalog()),
        '/search': httpx.Response(200, text=search_feed()),
        '/content/' + BOOK + '/howto/sorting': httpx.Response(200, text=(
            '<html><head><title>Sorting Techniques</title><script>HEAD SECRET</script></head>'
            '<body><nav>NAV SECRET</nav><main><h1>Sorting Techniques</h1>'
            '<p>sorted() returns a new list. list.sort() modifies the original list.</p>'
            '<script>SCRIPT SECRET</script><style>STYLE SECRET</style>'
            '<iframe src="https://example.com">FRAME SECRET</iframe></main></body></html>'),
            headers={'Content-Type': 'text/html; charset=utf-8'}),
    }
    def handle(request):
        assert request.method == 'GET' and request.url.host == '127.0.0.1'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        calls.append(request)
        result = pages[request.url.path]
        return result(request) if callable(result) else result
    original = httpx.Client
    class Client(original):
        def __init__(self, **kwargs):
            assert kwargs.get('trust_env') is False and kwargs.get('follow_redirects') is False
            transport = kwargs.pop('transport', None) or httpx.MockTransport(handle)
            super().__init__(transport=transport, **kwargs)
    monkeypatch.setattr(library.httpx, 'Client', Client)
    return calls, pages


def test_list_and_search_use_current_catalog_and_owner_port_without_writes(reader):
    calls, _ = reader
    books = json.loads(library.call('library_list', {}))['books']
    assert books[0]['id'] == BOOK
    result = json.loads(library.call('library_search', {'query': 'sort', 'book': 'Python'}))
    assert result['searched_books'] == 1 and result['results'][0]['url'] == ARTICLE
    assert result['results'][0]['preview'] == 'sorted() returns a new list.'
    request = calls[-1]
    assert request.url.port == 9090 and request.url.params['books.name'] == BOOK
    assert request.url.params['format'] == 'xml' and request.url.params['pageLength'] == '3'


def test_read_returns_article_text_and_citation_without_embedded_resources(reader):
    calls, _ = reader
    result = json.loads(library.call('library_read', {'url': ARTICLE}))
    assert 'sorted() returns a new list' in result['text']
    assert not any(word in result['text'] for word in ('HEAD SECRET', 'SCRIPT SECRET', 'NAV SECRET', 'FRAME SECRET', 'STYLE SECRET'))
    assert result['cite'] == f'[Sorting Techniques]({ARTICLE})'
    assert result['archive_date'] == '2026-08-01' and result['next_offset'] is None
    assert len(calls) == 3


def test_long_article_continuation_does_not_overload_context(reader):
    _, pages = reader
    pages['/content/' + BOOK + '/howto/sorting'] = httpx.Response(
        200, text='a' * library.EXCERPT_CHARS + 'NEXT SECTION', headers={'content-type': 'text/plain'})
    first = json.loads(library.call('library_read', {'url': ARTICLE}))
    assert len(first['text']) == library.EXCERPT_CHARS and 'NEXT SECTION' not in first['text']
    second = json.loads(library.call('library_read', {'url': ARTICLE, 'offset': first['next_offset']}))
    assert second['text'] == 'NEXT SECTION' and second['next_offset'] is None


@pytest.mark.parametrize('url', [
    'https://example.com/article', 'http://192.168.1.1/article',
    'http://localhost:9090/content/' + BOOK + '/howto/sorting',
    'http://owner:secret@127.0.0.1:9090/content/' + BOOK + '/howto/sorting',
    'http://127.0.0.1:8080/api/system/services/install', BASE + '/api/settings',
    BASE + '/content/missing-book/article', ARTICLE + '?target=https://example.com',
    BASE + '/content/' + BOOK + '/%2e%2e/%2e%2e/api/settings',
    BASE + '/content/' + BOOK + '/%252e%252e/api/settings',
    BASE + '/content/' + BOOK + '/%5c..%5cfile',
])
def test_invalid_urls_never_reach_an_article_transport(reader, url):
    calls, _ = reader
    result = json.loads(library.call('library_read', {'url': url}))
    assert 'error' in result and len(calls) == 2


def test_remote_redirect_is_refused_before_second_request(reader):
    calls, pages = reader
    pages['/content/' + BOOK + '/howto/sorting'] = httpx.Response(302, headers={'location': 'https://example.com/steal'})
    assert 'error' in json.loads(library.call('library_read', {'url': ARTICLE}))
    assert len(calls) == 3


def test_local_content_redirect_is_validated_and_cited_at_final_location(reader):
    _, pages = reader
    pages['/content/' + BOOK + '/start'] = httpx.Response(302, headers={'location': '/content/' + BOOK + '/howto/sorting'})
    result = json.loads(library.call('library_read', {'url': BASE + '/content/' + BOOK + '/start'}))
    assert result['url'] == ARTICLE and 'sorted()' in result['text']


def test_forged_search_links_are_not_offered_to_model(reader):
    _, pages = reader
    pages['/search'] = httpx.Response(200, text=search_feed('https://example.com/steal'))
    result = json.loads(library.call('library_search', {'query': 'sort'}))
    assert result['results'] == []


def test_reader_xml_highlights_preserve_the_entire_preview(reader):
    _, pages = reader
    pages['/search'] = httpx.Response(200, text=(
        '<rss><channel><item><title>Sorting</title><link>' + ARTICLE + '</link>'
        '<description>list.<b>sort</b>() changes the original; <b>sorted</b>() returns a copy.</description>'
        '</item></channel></rss>'))
    result = json.loads(library.call('library_search', {'query':'sort'}))
    assert result['results'][0]['preview'] == 'list.sort() changes the original; sorted() returns a copy.'


def test_pagination_does_not_hide_books_after_first_fifty(reader):
    calls, pages = reader
    names = tuple('book-' + str(i) for i in range(51))
    pages['/catalog/v2/entries'] = lambda request: httpx.Response(
        200, text=catalog(names[:50] if request.url.params['start'] == '0' else names[50:]))
    result = json.loads(library.call('library_list', {}))
    assert len(result['books']) == 51
    assert [r.url.params['start'] for r in calls if r.url.path == '/catalog/v2/entries'] == ['0', '50']


@pytest.mark.parametrize('payload', [[], [{'service_name':'nomad_kiwix_server','installed':1,'status':'exited','ui_location':'9090'}],
    [{'service_name':'nomad_kiwix_server','installed':1,'status':'running','ui_location':'https://example.com'}]])
def test_unavailable_reader_does_not_trigger_setup_or_external_fallback(reader, payload):
    calls, pages = reader
    pages['/api/system/services'] = httpx.Response(200, json=payload)
    assert 'error' in json.loads(library.call('library_search', {'query':'sort'}))
    assert len(calls) == 1


def test_no_match_and_oversized_or_binary_articles_are_reported_honestly(reader):
    _, pages = reader
    pages['/search'] = httpx.Response(200, text='<rss><channel/></rss>')
    assert json.loads(library.call('library_search', {'query':'notfound'}))['results'] == []
    path = '/content/' + BOOK + '/howto/sorting'
    pages[path] = httpx.Response(200, content=b'x' * (library.MAX_BYTES + 1), headers={'content-type':'text/html'})
    assert 'too large' in json.loads(library.call('library_read', {'url':ARTICLE}))['error']
    pages[path] = httpx.Response(200, content=b'PNG', headers={'content-type':'image/png'})
    assert 'error' in json.loads(library.call('library_read', {'url':ARTICLE}))


@pytest.mark.parametrize('inputs', [{'query':''}, {'query':'x'*241}, {'query':'sort','limit':True},
                                  {'query':'sort','limit':50}, {'query':'sort','book':'missing'}])
def test_bad_search_inputs_do_not_reach_search_endpoint(reader, inputs):
    calls, _ = reader
    assert 'error' in json.loads(library.call('library_search', inputs))
    assert len(calls) == 2


def test_tools_are_offered_and_dispatched_in_apocalypse(reader, monkeypatch):
    from agent import apocalypse, core
    monkeypatch.setenv('APEX_APOCALYPSE','1')
    agent = object.__new__(core.AgentCore)
    names = {row['name'] for row in agent._all_tools()}
    assert {'library_list','library_search','library_read'} <= names
    assert not names & {'web_search','bash','telegram_send'}
    assert {'library_list','library_search','library_read'} <= apocalypse.LOCAL_TOOLS
    assert 'sorted()' in json.loads(core._execute_tool_inner('library_read', {'url':ARTICLE}))['text']


def test_actual_agent_tool_loop_reads_then_cites_local_article(reader, monkeypatch, test_db):
    import config
    from agent import apocalypse, core, provider
    core.goals.init_db()
    monkeypatch.setenv('APEX_APOCALYPSE','1')
    monkeypatch.setattr(apocalypse,'verify_model',lambda *args:None)
    monkeypatch.setattr(core.longterm,'load_memory_files',lambda:{})
    monkeypatch.setattr(core._budget,'check',lambda:None)
    monkeypatch.setattr(config,'SMART_ROUTING_ENABLED',False)
    steps = []
    def answer(request):
        data = json.loads(request.content)
        steps.append(data)
        offered = {tool['function']['name'] for tool in data['tools']}
        assert {'library_list','library_search','library_read'} <= offered
        if len(steps) < 3:
            name = 'library_search' if len(steps) == 1 else 'library_read'
            inputs = {'query':'sort','book':'Python'} if len(steps) == 1 else {'url':ARTICLE}
            message = {'role':'assistant','content':'','tool_calls':[{'id':f'call-{len(steps)}','type':'function',
                       'function':{'name':name,'arguments':json.dumps(inputs)}}]}
            stop = 'tool_calls'
        else:
            tool_texts = [m.get('content','') for m in data['messages'] if m['role']=='tool']
            assert any('list.sort() modifies the original' in text for text in tool_texts)
            message = {'role':'assistant','content':f'sorted() makes a new list; list.sort() changes the original. [Sorting Techniques]({ARTICLE})'}
            stop = 'stop'
        return httpx.Response(200,json={'id':'local-chat','object':'chat.completion','created':1,'model':'qwen3:4b',
            'choices':[{'index':0,'message':message,'finish_reason':stop}],
            'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
    # Exercise the real provider adapter with a separate mock model transport.
    with httpx.Client(transport=httpx.MockTransport(answer),trust_env=False,follow_redirects=False) as transport:
        sdk = OpenAI(api_key='local-test-only',base_url='http://127.0.0.1:11435/v1',http_client=transport,max_retries=0)
        agent = core.AgentCore()
        agent._model = 'ollama/qwen3:4b'
        agent._provider_clients['ollama'] = NS(messages=provider._Messages(sdk,strip_prefix='ollama/'))
        monkeypatch.setattr(agent,'_rerank_eligible',lambda *args:False)
        result = agent.run('Read the offline Python sorting article and explain both methods with a source.',
                           include_screenshot=False,channel_id='apocalypse-library-fixture',max_iterations=5)
    assert ARTICLE in result and len(steps) == 3
