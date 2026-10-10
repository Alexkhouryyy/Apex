"""Download pinned Open VSX extensions using the system's proxy and CA settings."""
import hashlib
import json
import os
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / 'plugins'
DOWNLOADS = ROOT / '.downloads'


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    target = ('win32' if sys.platform == 'win32' else 'linux' if sys.platform.startswith('linux') else 'darwin')
    target += '-arm64' if platform.machine().lower() in ('aarch64','arm64') else '-x64'
    locked = json.loads((ROOT / 'plugins.lock.json').read_text())['extensions']
    supported = {p['platform'] for p in locked if p['platform'] != 'universal'}
    if target not in supported:
        raise RuntimeError(f'No pinned debugger bundle for {target}. Supported: {", ".join(sorted(supported))}.')
    selected = [p for p in locked if p['platform'] in ('universal', target)]
    PLUGINS.mkdir(exist_ok=True)
    DOWNLOADS.mkdir(exist_ok=True)
    marker = PLUGINS / '.apex-ready.json'
    marker.unlink(missing_ok=True)
    for plugin in selected:
        destination = DOWNLOADS / f"{plugin['id']}-{plugin['version']}.vsix"
        legacy = PLUGINS / destination.name
        if legacy.exists():
            os.replace(legacy, destination)
        folder = PLUGINS / plugin['id']
        stamp = folder / '.apex-sha256'
        if destination.is_file() and checksum(destination) == plugin['sha256']:
            print(f"Verified {plugin['id']} {plugin['version']}")
        else:
            temporary = destination.with_suffix('.partial')
            print(f"Downloading {plugin['id']} {plugin['version']} ({plugin['platform']})", flush=True)
            try:
                with urllib.request.urlopen(plugin['url'], timeout=60) as source, temporary.open('wb') as output:
                    while chunk := source.read(1024*1024):
                        output.write(chunk)
                if checksum(temporary) != plugin['sha256']:
                    raise RuntimeError(f"Checksum mismatch for {plugin['id']}. Re-run setup; this download was refused.")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        if stamp.exists() and stamp.read_text() == plugin['sha256']:
            continue
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()
        with zipfile.ZipFile(destination) as archive:
            for info in archive.infolist():
                name = PurePosixPath(info.filename)
                if name.is_absolute() or '..' in name.parts or '\\' in info.filename or ':' in info.filename:
                    raise RuntimeError(f"Unsafe extension archive path: {info.filename}")
                archive.extract(info, folder)
        stamp.write_text(plugin['sha256'])
    marker.write_text(json.dumps({'platform':target,'extensions':selected}, indent=2)+'\n', encoding='utf-8')
    print(f'{len(selected)} verified extensions ready for {target}.')


if __name__ == '__main__':
    main()
