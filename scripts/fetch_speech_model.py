"""Install the browser speech detector for hands-free voice.

    python scripts/fetch_speech_model.py          # download, verify, install
    python scripts/fetch_speech_model.py --check  # say whether it's installed and intact

The companion's hands-free mode can tell speech from noise with the Silero
voice-activity model running in the browser (ricky0123/vad, ISC; ONNX Runtime
Web, MIT). See docs/OPEN_SOURCE_REGISTER.md for why. About 14 MB, so it is
fetched once rather than committed:

- the two npm packages are pinned to exact versions;
- each download must match the sha512 that npm publishes for that version,
  checked before anything is unpacked;
- only the six files the page needs are kept, written to
  dashboard/static/vendor/speech/ with a manifest of their sha256 hashes,
  which the dashboard checks before offering the model.

Without it, hands-free keeps working on loudness, as before.
"""
import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'dashboard' / 'static' / 'vendor' / 'speech'
VERSION = 'vad-web-0.0.31+ort-1.22.0'
PACKAGES = [
    dict(name='@ricky0123/vad-web', version='0.0.31', license='ISC',
         url='https://registry.npmjs.org/@ricky0123/vad-web/-/vad-web-0.0.31.tgz',
         integrity='sha512-ePVq8TvB21sdHSxVMZOEIf9JVa7MAYpaIaNXeGgsPOiMCN8xbPCdF2LS5aYj7VRR3ZxzutedgV9ZplV65IA1Fg==',
         files={'package/dist/bundle.min.js': 'bundle.min.js',
                'package/dist/vad.worklet.bundle.min.js': 'vad.worklet.bundle.min.js',
                'package/dist/silero_vad_v5.onnx': 'silero_vad_v5.onnx'}),
    dict(name='onnxruntime-web', version='1.22.0', license='MIT',
         url='https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.22.0.tgz',
         integrity='sha512-Ud/+EBo6mhuaQWt/OjaOk0iNWjXqJoeeMFr6xQEERZdIZH2OWpGzuujz7lfuOBjUa6TEE/sc4nb7Da5dNL34fg==',
         files={'package/dist/ort.wasm.min.js': 'ort.wasm.min.js',
                'package/dist/ort-wasm-simd-threaded.mjs': 'ort-wasm-simd-threaded.mjs',
                'package/dist/ort-wasm-simd-threaded.wasm': 'ort-wasm-simd-threaded.wasm'}),
]
FILES = tuple(name for p in PACKAGES for name in p['files'].values())


class FetchError(RuntimeError):
    pass


def _download(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def verify_integrity(data, integrity):
    algorithm, _, expected = integrity.partition('-')
    if algorithm != 'sha512':
        raise FetchError(f'Unsupported integrity {algorithm}.')
    actual = base64.b64encode(hashlib.sha512(data).digest()).decode()
    if actual != expected:
        raise FetchError('The download does not match the pinned hash. Nothing was installed.')


def install(target=TARGET, download=_download):
    staged = {}
    for package in PACKAGES:
        data = download(package['url'])
        verify_integrity(data, package['integrity'])           # before unpacking anything
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            for member, name in package['files'].items():
                handle = tar.extractfile(member)
                if handle is None:
                    raise FetchError(f"{package['name']} has no {member}.")
                staged[name] = handle.read()
    target.mkdir(parents=True, exist_ok=True)
    for name, data in staged.items():
        (target / name).write_bytes(data)
    manifest = dict(version=VERSION,
                    packages=[{k: p[k] for k in ('name', 'version', 'license', 'url', 'integrity')} for p in PACKAGES],
                    files={name: hashlib.sha256(data).hexdigest() for name, data in staged.items()})
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest


def status(target=TARGET):
    """{installed, version, problem}. Installed means every file is present
    and matches the manifest, so a half-copied or edited file is caught."""
    manifest_path = target / 'manifest.json'
    if not manifest_path.exists():
        return dict(installed=False, version=None, problem='Not installed. Run Setup-Apex-Speech-Model.cmd.')
    try:
        manifest = json.loads(manifest_path.read_text())
        files = manifest['files']
    except (ValueError, KeyError):
        return dict(installed=False, version=None, problem='The manifest is unreadable. Run the setup again.')
    if set(files) != set(FILES) or manifest.get('version') != VERSION:
        return dict(installed=False, version=manifest.get('version'), problem='A different version is installed. Run the setup again.')
    for name, digest in files.items():
        path = target / name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return dict(installed=False, version=VERSION, problem=f'{name} is missing or changed. Run the setup again.')
    return dict(installed=True, version=VERSION, problem=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--check', action='store_true', help='only report whether it is installed and intact')
    args = parser.parse_args(argv)
    if not args.check:
        try:
            install()
        except (FetchError, OSError) as exc:
            print(f'[fail] {exc}')
            return 1
    state = status()
    print(f"[ok  ] Speech model {state['version']} installed in {TARGET}" if state['installed'] else f"[fail] {state['problem']}")
    return 0 if state['installed'] else 1


if __name__ == '__main__':
    sys.exit(main())
