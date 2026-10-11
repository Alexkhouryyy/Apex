"""Install a prebuilt IDE, or explicitly build it on a development machine."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile

if __package__:
    from . import code_ide_bundle
else:
    import code_ide_bundle

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--from-build', type=Path, metavar='ZIP', help='Install a Windows CI bundle without npm or C++ tools')
    mode.add_argument('--build', action='store_true', help='Compile the IDE locally (Windows requires C++ build tools)')
    args = parser.parse_args()
    node, npm = shutil.which('node'), shutil.which('npm')
    if not node:
        raise SystemExit('Install Node.js 22 or 24 LTS, reopen this terminal, then run setup again.')
    version = subprocess.check_output([node, '-p', 'process.versions.node'], text=True).strip()
    if int(version.split('.')[0]) not in (22, 24):
        raise SystemExit(f'Node {version} is unsupported. Use Node.js 22 or 24 LTS.')
    if args.from_build:
        code_ide_bundle.install(args.from_build, ROOT / 'ide')
        print('Prebuilt Apex Code IDE installed. Open a workspace in /code, then choose Full IDE.')
        return
    marker = ROOT / 'ide' / code_ide_bundle.MARKER
    if not args.build and marker.is_file():
        installed = json.loads(marker.read_text(encoding='utf-8'))
        if (installed.get('sources') == code_ide_bundle.fingerprints(ROOT / 'ide') and
                (installed.get('platform'), installed.get('arch')) ==
                (sys.platform, code_ide_bundle.node_environment()['arch']) and
                installed.get('node', '').split('.')[0] == version.split('.')[0] and
                all((ROOT / 'ide' / name).is_file() for name in code_ide_bundle.REQUIRED)):
            print('The prebuilt Apex Code IDE is already installed. Open Full IDE in /code.')
            return
    if os.name == 'nt' and not args.build:
        raise SystemExit('Install the prebuilt Windows IDE from the successful GitHub Tests run:\n'
            'python scripts/setup_code_ide.py --from-build <apex-code-ide-windows-node24.zip>\n'
            'Use --build only to compile locally with Visual Studio C++ Build Tools.')
    if not npm:
        raise SystemExit('Install npm with Node.js, then run setup again.')
    env = {**os.environ, 'PUPPETEER_SKIP_DOWNLOAD': 'true', 'APEX_PYTHON': sys.executable,
           'npm_config_python': sys.executable, 'NODE_GYP_FORCE_PYTHON': sys.executable}
    for arguments in (['ci', '--no-audit', '--no-fund'], ['run', 'plugins:download'], ['run', 'build']):
        print('npm ' + ' '.join(arguments), flush=True)
        # npm.cmd is a batch file; invoke its CLI with Node and argument arrays.
        cli = Path(npm).resolve().parent / 'node_modules/npm/bin/npm-cli.js'
        command = [node, str(cli)] if cli.exists() else [npm]
        subprocess.run(command + arguments, cwd=ROOT/'ide', env=env, check=True)
    print('Apex Code IDE is ready. Open a workspace in /code, then choose Full IDE.')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f'Setup stopped at {exc.cmd}. See the npm error above. Native builds on Windows may need Visual Studio Build Tools with Desktop development with C++.', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
