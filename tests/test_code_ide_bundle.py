"""Install tested runtime files without compilation or touching project/source files."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts import code_ide_bundle as bundle, setup_code_ide as setup


@pytest.fixture
def built(tmp_path, monkeypatch):
    app = tmp_path / 'ide'
    app.mkdir()
    for name in (*bundle.SOURCES, *bundle.REQUIRED):
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('content of ' + name + '\n').encode())
    (app / 'plugins/.apex-ready.json').write_text(json.dumps({'platform': 'win32-x64'}))
    environment = {'platform': 'win32', 'arch': 'x64', 'node': '24.14.0'}
    monkeypatch.setattr(bundle, 'node_environment', lambda: environment)
    monkeypatch.setattr(setup, 'ROOT', app.parent)
    archive = tmp_path / 'ide.zip'
    bundle.package(archive, app)
    return app, archive


def rewrite(archive, edit):
    with zipfile.ZipFile(archive) as source:
        files = {info.filename: source.read(info) for info in source.infolist()}
    metadata = json.loads(files['bundle.json'])
    edit(files, metadata)
    files['bundle.json'] = json.dumps(metadata).encode()
    with zipfile.ZipFile(archive, 'w') as destination:
        for name, data in files.items():
            destination.writestr(name, data)


def test_bundle_installs_generated_files_preserving_sources_and_user_files(built):
    app, archive = built
    (app / 'lib/old-build.txt').write_text('old')
    (app / '.env').write_text('private account configuration')
    (app / 'user-settings.json').write_text('my settings')
    sources = {name: (app / name).read_bytes() for name in bundle.SOURCES}
    metadata = bundle.install(archive, app)
    assert metadata['platform'] == 'win32'
    for name, checksum in metadata['files'].items():
        assert bundle.digest(app / name) == checksum
    assert not (app / 'lib/old-build.txt').exists()
    assert (app / '.env').read_text() == 'private account configuration'
    assert (app / 'user-settings.json').read_text() == 'my settings'
    assert {name: (app / name).read_bytes() for name in sources} == sources
    assert (app / bundle.MARKER).is_file()
    assert not list(app.glob('.apex-install-*'))


def test_source_fingerprints_allow_windows_checkout_line_endings(built):
    app, archive = built
    for name in bundle.SOURCES:
        path = app / name
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    bundle.install(archive, app)


@pytest.mark.parametrize('changes, message', [
    ({'platform': 'linux'}, 'operating system'),
    ({'arch': 'arm64'}, 'operating system'),
    ({'node': '22.1.0'}, 'Node.js 22'),
    ({'sources': {}}, 'does not match'),
])
def test_incompatible_bundles_preserve_existing_installation(built, changes, message):
    app, archive = built
    before = (app / 'src-gen/backend/main.js').read_bytes()
    rewrite(archive, lambda files, metadata: metadata.update(changes))
    with pytest.raises(ValueError, match=message):
        bundle.install(archive, app)
    assert (app / 'src-gen/backend/main.js').read_bytes() == before
    assert not list(app.glob('.apex-install-*'))


def test_checksum_failure_cannot_replace_existing_files(built):
    app, archive = built
    target = app / 'src-gen/backend/main.js'
    before = target.read_bytes()
    rewrite(archive, lambda files, metadata: files.update({'runtime/src-gen/backend/main.js': b'tampered'}))
    with pytest.raises(ValueError, match='checksum failed'):
        bundle.install(archive, app)
    assert target.read_bytes() == before
    assert not (app / bundle.MARKER).exists()


@pytest.mark.parametrize('name', ['../outside.py', 'lib/../../outside.py', 'lib/CON.txt',
                                 'lib/a:stream', 'lib/alias.', 'lib\\outside.py', '/tmp/outside.py'])
def test_unsafe_archive_paths_cannot_escape_staging(built, name):
    app, archive = built
    def add(files, metadata):
        files['runtime/' + name] = b'bad'
        metadata['files'][name] = hashlib.sha256(b'bad').hexdigest()
    rewrite(archive, add)
    with pytest.raises(ValueError, match='Unsafe bundle path'):
        bundle.install(archive, app)
    assert not (app.parent / 'outside.py').exists()
    assert not list(app.glob('.apex-install-*'))


def test_locked_runtime_directory_rolls_back_already_replaced_directories(built, monkeypatch):
    app, archive = built
    old = app / 'lib/old-build.txt'
    old.write_text('keep this build')
    original = Path.rename
    def rename(source, target):
        if source == app / 'node_modules':
            raise PermissionError('directory is busy')
        return original(source, target)
    monkeypatch.setattr(Path, 'rename', rename)
    with pytest.raises(ValueError, match='Close Apex'):
        bundle.install(archive, app)
    assert old.read_text() == 'keep this build'
    assert not (app / bundle.MARKER).exists()
    assert not list(app.glob('.apex-install-*'))


def test_package_materializes_local_npm_links_and_excludes_local_configuration(built):
    app, archive = built
    link = app / 'node_modules/apex-bridge'
    try:
        link.symlink_to(app / 'apex-extension', target_is_directory=True)
    except OSError:
        pytest.skip('OS denies symlink creation')
    (app / '.env').write_text('private data')
    bundle.package(archive, app)
    with zipfile.ZipFile(archive) as saved:
        assert 'runtime/node_modules/apex-bridge/backend.js' in saved.namelist()
        assert all('.env' not in name for name in saved.namelist())
    bundle.install(archive, app)
    assert not (app / 'node_modules/apex-bridge').is_symlink()
    assert (app / 'node_modules/apex-bridge/backend.js').read_bytes() == (app / 'apex-extension/backend.js').read_bytes()


def test_prebuilt_setup_works_without_npm_or_compiler(built, monkeypatch, capsys):
    _, archive = built
    monkeypatch.setattr(setup.sys, 'argv', ['setup_code_ide.py', '--from-build', str(archive)])
    monkeypatch.setattr(setup.shutil, 'which', lambda name: '/fake/node' if name == 'node' else None)
    monkeypatch.setattr(setup.subprocess, 'check_output', lambda *a, **k: '24.14.0')
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('Installer ran a build'))
    setup.main()
    assert 'Prebuilt Apex Code IDE installed' in capsys.readouterr().out


def test_default_windows_setup_requests_bundle_without_starting_source_build(built, monkeypatch):
    monkeypatch.setattr(setup.sys, 'argv', ['setup_code_ide.py'])
    monkeypatch.setattr(setup, 'os', SimpleNamespace(name='nt', environ={}))
    monkeypatch.setattr(setup.shutil, 'which', lambda name: '/fake/node' if name == 'node' else None)
    monkeypatch.setattr(setup.subprocess, 'check_output', lambda *a, **k: '24.14.0')
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('Installer ran a build'))
    with pytest.raises(SystemExit, match='prebuilt Windows IDE'):
        setup.main()
