"""Shared pytest fixtures."""
import pytest
from agent import safety, longterm


@pytest.fixture(autouse=True)
def offline_http(monkeypatch):
    """Unit tests may use local test servers or explicit mock transports only.

    A placeholder API key is not a network sandbox: SDKs still transmit a
    request before authentication fails. Fail before sending project/test data.
    This changes test transports only, never Apex's runtime safety settings.
    """
    import httpx
    import requests
    from urllib.parse import urlparse

    def check(url, transport=None):
        if isinstance(transport, (httpx.MockTransport, httpx.ASGITransport)):
            return
        if transport is not None and type(transport).__module__ == 'starlette.testclient':
            return
        if urlparse(str(url)).hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise RuntimeError('External HTTP is disabled in unit tests; mock the provider explicitly.')

    sync_send, async_send = httpx.Client.send, httpx.AsyncClient.send
    requests_send = requests.Session.send

    def send(self, request, *args, **kwargs):
        check(request.url, getattr(self, '_transport', None))
        return sync_send(self, request, *args, **kwargs)

    async def asend(self, request, *args, **kwargs):
        check(request.url, getattr(self, '_transport', None))
        return await async_send(self, request, *args, **kwargs)

    def rsend(self, request, *args, **kwargs):
        check(request.url)
        return requests_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, 'send', send)
    monkeypatch.setattr(httpx.AsyncClient, 'send', asend)
    monkeypatch.setattr(requests.Session, 'send', rsend)


@pytest.fixture(autouse=True)
def block_safety_stdin():
    """Prevent safety.check() from calling input() during the test suite.

    Default is deny-all; individual tests that want to confirm an action call
    safety.set_confirm_fn(lambda _: True) themselves.
    """
    original = safety._confirm_fn
    safety.set_confirm_fn(lambda _prompt: False)
    yield
    safety._confirm_fn = original


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Temporary SQLite DB for tests that touch longterm / turn_log.

    Patches longterm.DB_PATH so the production DB is never read or written.
    All tables are created fresh via init_db().
    """
    db_path = str(tmp_path / "test_memory.db")
    monkeypatch.setattr(longterm, "DB_PATH", db_path)
    longterm.init_db()
    return db_path
