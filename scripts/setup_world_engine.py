"""Verify the pinned source, install locked packages, and build World View."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'integrations/gods-eye-view'


def verify_source():
    records = json.loads((ENGINE / 'APEX_INVENTORY.json').read_text(encoding='utf-8'))
    for record in records:
        relative = Path(record['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Invalid source inventory path.')
        raw = (ENGINE / relative).read_bytes()
        digest = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
        if digest != record['sha']:
            raise RuntimeError(f'Pinned upstream file changed: {relative}. Preserve that edit before updating.')
    print(f'Verified {len(records)} original God\'s Eye View files.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    verify_source()
    if args.verify_only:
        return
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Install Node.js 24.14+ (24.x) or 26.x, then retry.')
    version = subprocess.check_output([node, '--version'], text=True).strip().lstrip('v')
    parts = tuple(int(p) for p in version.split('.'))
    if not ((parts[0] == 24 and parts >= (24,14,0)) or parts[0] == 26):
        raise RuntimeError('World View needs Node.js 24.14+ (24.x) or 26.x.')
    npm = shutil.which('npm')
    candidates = [Path(node).parent / 'node_modules/npm/bin/npm-cli.js']
    if npm:
        candidates.append(Path(npm).parent / 'node_modules/npm/bin/npm-cli.js')
    cli = next((path for path in candidates if path.exists()), None)
    if cli is None:
        raise RuntimeError('npm is missing. Repair your Node.js installation and retry.')
    print('Installing the locked world engine dependencies…', flush=True)
    subprocess.run([node,str(cli),'ci','--ignore-scripts','--no-audit','--no-fund',
                    '--cache',str(ROOT / '.mcp-runtime/world-npm-cache')],cwd=ENGINE,check=True)
    print('Building the complete World View…', flush=True)
    subprocess.run([node,str(ENGINE / 'apex-build.mjs')],cwd=ENGINE,check=True)
    print('World View ready. Start Apex normally and open Earth.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'World View setup stopped: {exc}',file=sys.stderr)
        raise SystemExit(1)
