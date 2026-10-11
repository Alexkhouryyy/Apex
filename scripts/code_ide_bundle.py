"""Package and install an already-built IDE without running npm or a compiler."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import zipfile

APP = Path(__file__).resolve().parents[1] / 'ide'
DIRECTORIES = ('lib', 'node_modules', 'plugins', 'src-gen')
MARKER = '.apex-bundle.json'
SOURCES = ('package.json', 'package-lock.json', 'plugins.lock.json',
           'apex-extension/package.json', 'apex-extension/backend.js',
           'apex-extension/frontend.js', 'archive-adapter/package.json',
           'archive-adapter/index.cjs')
REQUIRED = ('lib/frontend/index.html', 'src-gen/backend/main.js',
            'plugins/.apex-ready.json', 'node_modules/@theia/core/package.json')


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fingerprints(app: Path) -> dict:
    # Git checkouts can use LF or CRLF without changing JavaScript/JSON behavior.
    return {name: hashlib.sha256((app / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for name in SOURCES}


def node_environment() -> dict:
    node = shutil.which('node')
    if not node:
        raise ValueError('Install Node.js 24 LTS and reopen PowerShell first.')
    return json.loads(subprocess.check_output([node, '-p',
        'JSON.stringify({platform:process.platform,arch:process.arch,node:process.versions.node})'], text=True))


def package(output: Path, app: Path = APP) -> dict:
    for name in REQUIRED:
        if not (app / name).is_file():
            raise ValueError('Build and test the IDE first; missing ' + name)
    environment = node_environment()
    marker = json.loads((app / 'plugins/.apex-ready.json').read_text(encoding='utf-8'))
    if marker['platform'] != environment['platform'] + '-' + environment['arch']:
        raise ValueError('Extensions were downloaded for a different platform.')
    metadata = {'format': 1, **environment, 'sources': fingerprints(app), 'files': {},
                'commit': os.environ.get('GITHUB_SHA', '')}
    output = output.resolve()
    if output.is_relative_to(app.resolve()):
        raise ValueError('Save the bundle outside the IDE folder.')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for directory in DIRECTORIES:
                for base, dirs, files in os.walk(app / directory, followlinks=True):
                    parent = Path(base)
                    for name in dirs + files:
                        target = (parent / name).resolve()
                        if not target.is_relative_to(app.resolve()):
                            raise ValueError('Runtime link escapes the IDE folder: ' + str(parent / name))
                        if name in dirs and (target == parent.resolve() or target in parent.resolve().parents):
                            raise ValueError('Runtime directory link forms a loop: ' + str(parent / name))
                    for name in sorted(files):
                        source = parent / name
                        if not source.is_file():
                            raise ValueError('Runtime contains a non-file: ' + str(source))
                        relative = source.relative_to(app).as_posix()
                        # Follow local npm package links so the bundle remains relocatable.
                        archive.write(source, 'runtime/' + relative)
                        metadata['files'][relative] = digest(source)
            archive.writestr('bundle.json', json.dumps(metadata, sort_keys=True) + '\n')
    except Exception:
        output.unlink(missing_ok=True)
        raise
    output.with_suffix(output.suffix + '.sha256').write_text(digest(output) + '\n', encoding='ascii')
    return metadata


def _relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    devices = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
               *(f'LPT{i}' for i in range(1, 10))}
    if (not name or path.is_absolute() or '\\' in name or ':' in name or
            any(part in ('', '.', '..') or part != part.rstrip(' .') or
                part.split('.')[0].upper() in devices for part in name.split('/'))):
        raise ValueError('Unsafe bundle path: ' + name)
    return path


def _validate(archive: zipfile.ZipFile, app: Path, environment: dict) -> dict:
    entries = archive.infolist()
    names = [info.filename for info in entries]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Bundle contains duplicate paths.')
    if 'bundle.json' not in names:
        raise ValueError('Select the IDE zip inside the downloaded GitHub artifact.')
    if archive.getinfo('bundle.json').file_size > 32_000_000:
        raise ValueError('Bundle metadata is too large.')
    metadata = json.loads(archive.read('bundle.json'))
    if metadata.get('format') != 1:
        raise ValueError('Unsupported IDE bundle format.')
    if (metadata.get('platform'), metadata.get('arch')) != (environment['platform'], environment['arch']):
        raise ValueError('Download the IDE bundle for this operating system and CPU.')
    if metadata.get('node', '').split('.')[0] != environment['node'].split('.')[0]:
        raise ValueError('Use Node.js ' + metadata.get('node', '').split('.')[0] + ' LTS for this bundle.')
    if metadata.get('sources') != fingerprints(app):
        raise ValueError('The bundle does not match this IDE source. Download the build for your update branch.')
    files = metadata.get('files', {})
    if not isinstance(files, dict) or any(name not in files for name in REQUIRED):
        raise ValueError('Bundle is missing required IDE files.')
    # Windows' ZIP reader normalizes backslashes; validate the raw manifest too.
    for name in files:
        _relative('runtime/' + name)
    if set(names) != {'bundle.json', *('runtime/' + name for name in files)}:
        raise ValueError('Bundle contents do not match its file manifest.')
    if sum(info.file_size for info in entries) > 4_000_000_000:
        raise ValueError('Bundle exceeds the 4 GB installed size limit.')
    for info in entries:
        path = _relative(info.filename)
        if info.filename != 'bundle.json' and (path.parts[0] != 'runtime' or
                len(path.parts) < 3 or path.parts[1] not in DIRECTORIES):
            raise ValueError('Bundle contains a file outside the generated runtime.')
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError('Bundle must contain files, not symbolic links.')
    return metadata


def install(bundle: Path, app: Path = APP) -> dict:
    environment = node_environment()
    with zipfile.ZipFile(bundle) as archive:
        metadata = _validate(archive, app, environment)
        # Use the IDE folder, avoiding Windows' sometimes inaccessible pytest/temp roots.
        with tempfile.TemporaryDirectory(prefix='.apex-install-', dir=app) as temporary:
            stage = Path(temporary)
            runtime = stage / 'runtime'
            previous = stage / 'previous'
            previous.mkdir()
            for name, checksum in metadata['files'].items():
                info = archive.getinfo('runtime/' + name)
                target = runtime / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open('wb') as destination:
                    shutil.copyfileobj(source, destination)
                if digest(target) != checksum:
                    raise ValueError('Bundle checksum failed: ' + name)
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    target.chmod(mode)
            plugins = json.loads((runtime / 'plugins/.apex-ready.json').read_text(encoding='utf-8'))
            if plugins['platform'] != environment['platform'] + '-' + environment['arch']:
                raise ValueError('Bundle extensions target a different platform.')
            (runtime / MARKER).write_text(json.dumps(metadata) + '\n', encoding='utf-8')
            moved, installed = [], []
            try:
                for name in (*DIRECTORIES, MARKER):
                    target = app / name
                    if target.exists() or target.is_symlink():
                        target.rename(previous / name)
                        moved.append(name)
                    (runtime / name).rename(target)
                    installed.append(name)
            except OSError as exc:
                for name in reversed(installed):
                    (app / name).rename(runtime / name)
                for name in reversed(moved):
                    (previous / name).rename(app / name)
                raise ValueError('Close Apex and its Full IDE windows, then retry installing the bundle.') from exc
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = package(args.output)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, str(exc) + '\n')
    print(f"Packaged {len(result['files'])} runtime files for {result['platform']}-{result['arch']}.")
