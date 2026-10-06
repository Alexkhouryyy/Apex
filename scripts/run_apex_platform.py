"""One console for prepared Apex, Celine and Apocalypse; never prepares downloads."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_apex_qwen as launcher
from agent import apocalypse


def health(opener, url):
    try:
        with opener.open(url, timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


def spawn(args, env):
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if launcher.WINDOWS else 0
    return subprocess.Popen([sys.executable, '-u', *args], cwd=ROOT, env=env, creationflags=flags)


def stop_owned(process):
    if process is None or process.poll() is not None:
        return
    if launcher.WINDOWS:
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=20)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    launcher.stop(process)


def start_cached_nomad(home):
    """Do not regenerate compose, pull missing images, build or download content."""
    compose = home / 'nomad' / 'compose.json'
    docker = Path(os.getenv('LOCALAPPDATA', '')) / 'Programs/DockerDesktop/resources/bin/docker.exe'
    if not compose.is_file() or not docker.is_file():
        print('NOMAD: skipped; cached installation or Docker is missing.', flush=True)
        return None
    base = [str(docker), 'compose', '--project-directory', str(compose.parent), '-f', str(compose)]
    try:
        opts = dict(capture_output=True, text=True, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if launcher.WINDOWS else 0)
        info = subprocess.run([str(docker), 'info', '--format', '{{.OSType}}'], **opts)
        if info.returncode or info.stdout.strip() != 'linux':
            print('NOMAD: skipped; open Docker Desktop with Linux containers first.', flush=True)
            return None
        images = subprocess.run(base + ['config', '--images'], **opts)
        names = list(dict.fromkeys(images.stdout.split()))
        if images.returncode or not names or subprocess.run([str(docker), 'image', 'inspect', *names], **opts).returncode:
            print('NOMAD: skipped; required images are not cached. No downloads started.', flush=True)
            return None
        print('NOMAD: starting cached containers. The database can take several minutes on the external drive.', flush=True)
        return subprocess.Popen(base + ['up', '-d', '--pull', 'never', '--no-build'], cwd=compose.parent)
    except (OSError, subprocess.TimeoutExpired):
        print('NOMAD: Docker did not answer. Its cached files were kept.', flush=True)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline-only', action='store_true', help='Apocalypse and cached NOMAD only')
    parser.add_argument('--no-nomad', action='store_true', help='Skip Docker entirely')
    parser.add_argument('--open', action='store_true', help='Open the ready dashboards')
    args = parser.parse_args(argv)
    normal = launcher.launch_environment()
    normal['APEX_APOCALYPSE'] = '0'
    # Celine's model loader otherwise fetches missing weights during startup.
    # This entry point runs prepared models only; cloud API access stays normal.
    normal['HF_HUB_OFFLINE'] = normal['TRANSFORMERS_OFFLINE'] = '1'
    if not args.offline_only and normal.get('DASHBOARD_ENABLED', 'true').lower() not in {'1', 'true', 'yes'}:
        raise RuntimeError('Enable the Apex dashboard before using the platform launcher, or use --offline-only.')
    offline = apocalypse.launch_environment(ROOT, normal)
    host, port, normal_url = launcher.dashboard_address(normal)
    offline_port = int(offline['DASHBOARD_PORT'])
    if offline_port == 17494 or (not args.offline_only and offline_port == port):
        raise RuntimeError('Apex, Apocalypse and Celine need separate ports.')
    checks = [(offline_port, '127.0.0.1')]
    if not args.offline_only:
        checks += [(port, host), (17494, '127.0.0.1')]
    if any(launcher.port_in_use(p, h) for p, h in checks):
        raise RuntimeError('An Apex service is already running. Stop its previous launcher with Ctrl+C, then retry. Nothing was stopped.')
    platform_id = uuid.uuid4().hex
    normal['APEX_PLATFORM_ID'] = offline['APEX_PLATFORM_ID'] = platform_id
    offline['APEX_LAUNCH_ID'] = uuid.uuid4().hex
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    children = []
    nomad = None
    try:
        if not args.no_nomad:
            nomad = start_cached_nomad(Path(offline['APEX_APOCALYPSE_HOME']))
        child = spawn([str(ROOT / 'scripts/run_apex_apocalypse.py')], offline)
        children.append(('Apocalypse', child))
        offline_url = f'http://127.0.0.1:{offline_port}'
        launcher.wait_healthy(opener, offline_url+'/health',
            lambda h: h.get('service') == 'apex' and h.get('launch_id') == offline['APEX_LAUNCH_ID']
            and h.get('platform_id') == platform_id and h.get('agent_ready') is True,
            children, 'Apocalypse dashboard', 240)
        print('Apocalypse: ready. '+offline_url+'/apocalypse', flush=True)
        if not args.offline_only:
            child = spawn([str(ROOT / 'scripts/run_apex_qwen.py'), '--fast', '--resident'], normal)
            children.append(('Apex + Celine', child))
            launcher.wait_healthy(opener, normal_url+'/health',
                lambda h: h.get('service') == 'apex' and h.get('platform_id') == platform_id
                and h.get('agent_ready') is True, children, 'Apex + Celine', 1200)
            print('Apex + Celine: ready. '+normal_url+'/home', flush=True)
            print('Companion: '+normal_url+'/companion · enable Voice mode for browser audio access.', flush=True)
        if args.open:
            webbrowser.open(offline_url+'/apocalypse')
            if not args.offline_only:
                webbrowser.open(normal_url+'/home')
        print('Prepared dashboards are ready. Account authorization and offline readers have their own readiness checks.', flush=True)
        print('No model, book, course or map downloads were started. Keep this console open; Ctrl+C stops its Apex services.', flush=True)
        nomad_announced = args.no_nomad or nomad is None
        while True:
            for name, process in children:
                if process.poll() is not None:
                    raise RuntimeError(f'{name} stopped (exit {process.returncode}). Other owned Apex services will stop too.')
            if not nomad_announced:
                if nomad.poll() not in (None, 0):
                    print('NOMAD: startup failed. Apex remains available; inspect the Docker output above.', flush=True)
                    nomad_announced = True
                elif nomad.poll() == 0 and health(opener, 'http://127.0.0.1:8080/api/health').get('status') == 'ok':
                    print('NOMAD Command Center: ready. http://127.0.0.1:8080 · check each reader and its content inside.', flush=True)
                    nomad_announced = True
            time.sleep(1)
    finally:
        for _, process in reversed(children):
            stop_owned(process)
        if nomad is not None and nomad.poll() is None:
            launcher.stop(nomad)  # Stops only this compose client; never removes volumes.
        print('Owned Apex services stopped. Cached NOMAD containers, if started, remain managed by Docker Desktop.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('Platform launcher stopped: '+str(exc), file=sys.stderr)
        raise SystemExit(1)
