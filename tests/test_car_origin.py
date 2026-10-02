"""The car reaches Apex through `tailscale serve` (HTTPS on your tailnet,
proxied to Apex on loopback). The page's origin is then https://pc.ts.net
while Apex sees http on 127.0.0.1; the same-origin check must accept exactly
that and nothing wider."""
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from dashboard.companion import _check_origin


def req(origin, host='pc.tail1234.ts.net', client='127.0.0.1', forwarded=None, scheme='http'):
    headers = [(b'host', host.encode())]
    if origin:
        headers.append((b'origin', origin.encode()))
    if forwarded:
        headers.append((b'x-forwarded-host', forwarded.encode()))
    return Request({'type': 'http', 'method': 'POST', 'path': '/api/companion/jobs', 'headers': headers,
                    'scheme': scheme, 'server': (host, 80), 'client': (client, 5000), 'query_string': b''})


def test_same_origin_and_no_origin_pass():
    _check_origin(req(None))
    _check_origin(req('http://127.0.0.1:7860', host='127.0.0.1:7860'))


def test_tailscale_serve_https_page_reaching_loopback_passes():
    _check_origin(req('https://pc.tail1234.ts.net'))
    _check_origin(req('https://pc.tail1234.ts.net', host='127.0.0.1:7860', forwarded='pc.tail1234.ts.net'))


@pytest.mark.parametrize('kwargs', [
    dict(origin='https://evil.example'),                                   # another site
    dict(origin='https://pc.tail1234.ts.net', client='100.101.102.103'),  # not through the local proxy
    dict(origin='http://pc.tail1234.ts.net', host='127.0.0.1:7860', forwarded='pc.tail1234.ts.net'),  # not https
    dict(origin='https://pc.tail1234.ts.net.evil.example'),
])
def test_everything_else_is_still_refused(kwargs):
    with pytest.raises(HTTPException):
        _check_origin(req(**kwargs))
