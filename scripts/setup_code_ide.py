"""One-time local IDE build, called by Setup-Apex-Code-IDE.cmd on Windows."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    node, npm = shutil.which('node'), shutil.which('npm')
    if not node or not npm:
        raise SystemExit('Install Node.js 22 or 24 LTS, reopen this terminal, then run setup again.')
    version = subprocess.check_output([node, '-p', 'process.versions.node'], text=True).strip()
    if int(version.split('.')[0]) not in (22, 24):
        raise SystemExit(f'Node {version} is unsupported. Use Node.js 22 or 24 LTS.')
    env = {**os.environ, 'PUPPETEER_SKIP_DOWNLOAD': 'true', 'APEX_PYTHON': sys.executable}
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
