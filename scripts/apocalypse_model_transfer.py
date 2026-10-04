"""Pinned Qwen3:4b registry transfer for Windows connections where Ollama returns EOF.

Keeps the official template, parameters and license alongside the weights. Uses
the Ollama 0.35.1 blob/manifest layout; the caller still verifies local inference.
No existing partial files are deleted or treated as complete based on their size.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PIN = '359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7'
REGISTRY = 'https://registry.ollama.ai/v2/library/qwen3/blobs/'


def pinned_manifest():
    raw = (ROOT/'integrations/project-nomad/qwen3-4b-manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN:
        raise RuntimeError('Pinned Qwen manifest changed; review it before downloading.')
    data = json.loads(raw)
    layers = [data['config'], *data['layers']]
    for row in layers:
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', row['digest']) or not 0 < row['size'] <= 3_000_000_000:
            raise RuntimeError('Invalid pinned model layer.')
    return raw, layers


def checked_path(root, relative):
    path = root / relative
    path.resolve().relative_to(root.resolve())
    if path.is_symlink():
        raise RuntimeError('Linked model files are not supported.')
    return path


def verified(path, row):
    if not path.is_file() or path.stat().st_size != row['size']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(block)
    return digest.hexdigest() == row['digest'].split(':')[1]


def fetch_blob(curl, root, row):
    target = checked_path(root, 'blobs/'+row['digest'].replace(':', '-'))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not verified(target, row):
            raise RuntimeError('Existing model blob failed verification; preserved: '+str(target))
        return
    # Ollama's original .partial files can be preallocated and contain holes.
    # A separate sequential transfer can be resumed by its actual file length.
    part = checked_path(root, 'blobs/'+target.name+'.apex-curl.part')
    for attempt in range(3):
        if part.exists() and part.stat().st_size >= row['size']:
            if not verified(part, row):
                raise RuntimeError('Model transfer failed checksum; partial preserved for review.')
            part.replace(target)
            return
        print(f'Model layer {row["digest"][7:19]}: resumable transfer, attempt {attempt+1}/3', flush=True)
        result = subprocess.run([
            curl, '--http1.1', '--location', '--proto', '=https', '--proto-redir', '=https',
            '--fail', '--show-error', '--connect-timeout', '30', '--max-time', '1800',
            '--speed-limit', '1024', '--speed-time', '120', '--max-filesize', str(row['size']),
            '--continue-at', '-', '--output', str(part), REGISTRY+row['digest'],
        ])
        if result.returncode == 0:
            if not verified(part, row):
                raise RuntimeError('Model transfer was incomplete or failed checksum; partial preserved.')
            part.replace(target)
            return
        if attempt < 2:
            time.sleep(2)
    raise RuntimeError('Windows model transfer stopped. Partial files were kept; rerun with --model-transport curl to resume.')


def prepare_store(home):
    raw, layers = pinned_manifest()
    root = Path(home)/'models'
    root.resolve().relative_to(Path(home).resolve())
    root.mkdir(parents=True, exist_ok=True)
    curl = shutil.which('curl.exe') or shutil.which('curl')
    if not curl:
        raise RuntimeError('Windows curl is unavailable; use the normal Ollama transfer.')
    manifest = checked_path(root, 'manifests/registry.ollama.ai/library/qwen3/4b')
    if manifest.exists() and manifest.read_bytes() != raw:
        raise RuntimeError('A different Qwen revision is installed. Preserved; review before replacing it.')
    # Small files first demonstrate reachability before transferring weights.
    for row in sorted(layers, key=lambda row: row['size']):
        fetch_blob(curl, root, row)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = checked_path(root, 'manifests/registry.ollama.ai/library/qwen3/4b.apex.tmp')
    temporary.write_bytes(raw)
    temporary.replace(manifest)
    print('Pinned Qwen weights and metadata checksum verified. Testing local inference next.', flush=True)
