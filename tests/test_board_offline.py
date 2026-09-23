"""/board must work with no internet.

three.js used to come from a CDN, so on a machine that was offline (or behind a
firewall that blocks jsdelivr) the board rendered nothing at all. It is now
vendored under dashboard/static/vendor/three/. These tests keep it that way:

  * board.html imports nothing from http(s) — a new CDN import fails here;
  * every module the page can reach exists on disk (the import graph is closed);
  * .js is served as JavaScript even when the OS says text/plain — ES modules
    refuse to run otherwise, and Windows registries do say that;
  * stripping ?token= from the address bar keeps the other params (?diag=1).
"""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "dashboard" / "static"
BOARD = STATIC / "board.html"
VENDOR = STATIC / "vendor" / "three"

_IMPORT_RE = re.compile(r"""(?:import|export)\s[^;]*?from\s*["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']""")


def _board_html() -> str:
    return BOARD.read_text(encoding="utf-8")


def _import_map() -> dict[str, str]:
    m = re.search(r'<script type="importmap">(.*?)</script>', _board_html(), re.S)
    assert m, "board.html has no import map"
    return json.loads(m.group(1))["imports"]


def _specifiers(src: str) -> list[str]:
    return [a or b for a, b in _IMPORT_RE.findall(src)]


def _static_path(url: str) -> Path:
    assert url.startswith("/static/"), f"{url!r} is not served from /static"
    return STATIC / url[len("/static/"):]


def _resolve(spec: str, importer: Path | None) -> Path:
    """Resolve a module specifier the way the browser would, with the page's
    import map. Anything that would leave this machine fails the test."""
    assert not re.match(r"^(https?:)?//", spec), f"network import: {spec!r}"
    imports = _import_map()
    if spec in imports:
        return _static_path(imports[spec])
    for prefix, target in imports.items():
        if prefix.endswith("/") and spec.startswith(prefix):
            return _static_path(target + spec[len(prefix):])
    if spec.startswith("/"):
        return _static_path(spec)
    assert importer is not None and spec.startswith("."), f"unresolvable bare import {spec!r}"
    return (importer.parent / spec).resolve()


class TestNothingLeavesTheMachine:
    def test_no_script_or_stylesheet_is_fetched_from_the_network(self):
        html = _board_html()
        for tag in re.findall(r"<(?:script|link)\b[^>]*>", html):
            assert not re.search(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", tag), tag

    def test_the_import_map_points_at_local_files(self):
        for spec, url in _import_map().items():
            assert url.startswith("/static/vendor/"), f"{spec} -> {url}"

    def test_the_whole_import_graph_is_on_disk(self):
        """Walk every import from the page's module script down. One missing
        file (say, a GLTFLoader dependency nobody copied) kills the page as
        dead as the CDN did."""
        seen: set[Path] = set()
        todo = [_resolve(s, None) for s in _specifiers(_board_html())]
        assert todo, "board.html imports nothing — the regex is broken"
        while todo:
            path = todo.pop()
            if path in seen:
                continue
            assert path.is_file(), f"import target missing: {path.relative_to(ROOT)}"
            seen.add(path)
            src = path.read_text(encoding="utf-8")
            todo.extend(_resolve(s, path) for s in _specifiers(src))
        names = {p.name for p in seen}
        # The walk has to actually reach the files — a walker that visits
        # nothing would pass the loop above.
        assert {"three.module.min.js", "GLTFLoader.js", "BufferGeometryUtils.js"} <= names

    def test_the_licence_ships_with_the_code(self):
        assert "MIT" in (VENDOR / "LICENSE").read_text(encoding="utf-8")


class TestServedAsJavaScript:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        import config
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
        return TestClient(server.app), server

    @pytest.fixture
    def poisoned_registry(self):
        """Simulate the Windows registry mapping .js to text/plain."""
        saved = {ext: mimetypes.types_map.get(ext) for ext in (".js", ".mjs", ".css")}
        mimetypes.add_type("text/plain", ".js")
        mimetypes.add_type("text/plain", ".mjs")
        mimetypes.add_type("text/plain", ".css")
        yield
        for ext, typ in saved.items():
            if typ:
                mimetypes.add_type(typ, ext)

    def test_poison_is_real(self, poisoned_registry):
        """Without this, the next test could pass because add_type did nothing."""
        assert mimetypes.guess_type("x.js")[0] == "text/plain"

    def test_the_server_pins_the_types_itself_at_startup(self):
        """In a fresh process: poison the registry FIRST, then import the
        server, then fetch. Calling the pin from the test (as the test below
        does) would pass even if server.py stopped calling it — this cannot."""
        import subprocess
        import sys
        code = (
            "import mimetypes; mimetypes.init()\n"
            "for e in ('.js', '.mjs', '.css'): mimetypes.add_type('text/plain', e)\n"
            "import config; config.DASHBOARD_TOKEN = ''\n"
            "from dashboard import server\n"
            "from fastapi.testclient import TestClient\n"
            "r = TestClient(server.app).get("
            "'/static/vendor/three/examples/jsm/loaders/GLTFLoader.js')\n"
            "print(r.status_code, r.headers['content-type'])\n"
        )
        env = dict(__import__("os").environ,
                   ANTHROPIC_API_KEY="sk-ant-placeholder-for-ci-tests-only")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        status, ctype = out.stdout.strip().splitlines()[-1].split(" ", 1)
        assert status == "200"
        assert ctype.split(";")[0] in ("text/javascript", "application/javascript"), ctype

    def test_vendored_modules_are_javascript_even_when_the_os_says_text(self, client, poisoned_registry):
        c, server = client
        server._pin_script_mime_types()
        urls = [u for u in _import_map().values() if not u.endswith("/")]
        urls.append("/static/vendor/three/examples/jsm/loaders/GLTFLoader.js")
        urls.append("/static/vendor/three/examples/jsm/utils/BufferGeometryUtils.js")
        for url in urls:
            r = c.get(url)
            assert r.status_code == 200, url
            assert r.headers["content-type"].split(";")[0] in ("text/javascript", "application/javascript"), \
                (url, r.headers["content-type"])


class TestTokenStrip:
    def test_only_the_token_is_removed_from_the_address_bar(self):
        """Opening /board?token=…&diag=1 used to rewrite the URL to bare
        /board, so the diag panel vanished on the next reload."""
        html = _board_html()
        strip = re.search(r"if \(q\.has\('token'\)\) \{(.*?)\n  \}", html, re.S)
        assert strip, "token strip block not found"
        body = strip.group(1)
        assert "q.delete('token')" in body
        assert "q.toString()" in body, "the remaining params must be written back"
