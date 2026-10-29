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
    """A service's /health, or {} for anything that isn't a proper JSON object."""
    try:
        with opener.open(url, timeout=2) as response:
            data = json.loads(response.read(65536))
        return data if isinstance(data, dict) else {}
    except Exception:   # http.client errors are not OSError; none of them may stop the platform
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
    # Celine's voice port is not checked here: the Apex + Celine launcher reuses
    # an Apex voice server already on it, or starts without the local voice.
    checks = [(offline_port, '127.0.0.1')]
    if not args.offline_only:
        checks += [(port, host)]
    if any(launcher.port_in_use(p, h) for p, h in checks):
        raise RuntimeError('An Apex service is already running. Stop its previous launcher with Ctrl+C, then retry. Nothing was stopped.')
    platform_id = uuid.uuid4().hex
    normal['APEX_PLATFORM_ID'] = offline['APEX_PLATFORM_ID'] = platform_id
    offline['APEX_LAUNCH_ID'] = uuid.uuid4().hex
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    children = []          # (name, process) still running
    ready = []             # names that became healthy
    nomad = None

    def alive(name):
        return any(n == name and p.poll() is None for n, p in children)

    def drop(name, process):
        if (name, process) in children:
            children.remove((name, process))
        stop_owned(process)

    try:
        if not args.no_nomad:
            nomad = start_cached_nomad(Path(offline['APEX_APOCALYPSE_HOME']))
        offline_url = f'http://127.0.0.1:{offline_port}'
        # Each service starts and fails on its own: Celine's voice model failing
        # to load on the GPU must not take the working Apocalypse library down
        # with it, and the other way round. Both start now; neither waits for
        # the other to become healthy.
        apocalypse_child = spawn([str(ROOT / 'scripts/run_apex_apocalypse.py')], offline)
        children.append(('Apocalypse', apocalypse_child))
        apex_child = None
        if not args.offline_only:
            apex_child = spawn([str(ROOT / 'scripts/run_apex_qwen.py'), '--fast', '--resident'], normal)
            children.append(('Apex + Celine', apex_child))
        try:
            launcher.wait_healthy(opener, offline_url+'/health',
                lambda h: h.get('service') == 'apex' and h.get('launch_id') == offline['APEX_LAUNCH_ID']
                and h.get('platform_id') == platform_id and h.get('agent_ready') is True,
                [('Apocalypse', apocalypse_child)], 'Apocalypse dashboard', 240)
            ready.append('Apocalypse')
            print('Apocalypse: ready. '+offline_url+'/apocalypse', flush=True)
        except Exception as exc:
            print(f'Apocalypse: did not start ({exc}).'
                  + ('' if args.offline_only else ' Apex continues without it.'), flush=True)
            drop('Apocalypse', apocalypse_child)
        if apex_child is not None:
            try:
                launcher.wait_healthy(opener, normal_url+'/health',
                    lambda h: h.get('service') == 'apex' and h.get('platform_id') == platform_id
                    and h.get('agent_ready') is True, [('Apex + Celine', apex_child)], 'Apex + Celine', 1200)
                ready.append('Apex + Celine')
                print('Apex + Celine: ready. '+normal_url+'/home', flush=True)
                print('Companion: '+normal_url+'/companion · enable Voice mode for browser audio access.', flush=True)
            except Exception as exc:
                drop('Apex + Celine', apex_child)
                print(f'Apex + Celine: did not start ({exc}).'
                      + (' Apocalypse remains available.' if alive('Apocalypse') else ''), flush=True)
        ready = [name for name in ready if alive(name)]   # one may have died while the other was starting
        if not ready:
            raise RuntimeError('No Apex service started. See the messages above.')
        if args.open:
            if 'Apocalypse' in ready:
                webbrowser.open(offline_url+'/apocalypse')
            if 'Apex + Celine' in ready:
                webbrowser.open(normal_url+'/home')
        print('Ready: '+', '.join(ready)+'. Account authorization and offline readers have their own readiness checks.', flush=True)
        print('No model, book, course or map downloads were started. Keep this console open; Ctrl+C stops its Apex services.', flush=True)
        nomad_announced = args.no_nomad or nomad is None
        while children:
            dead = [(n, p) for n, p in children if p.poll() is not None]
            for item in dead:
                children.remove(item)
            for name, process in dead:
                others = ', '.join(n for n, _ in children)
                print(f'{name} stopped (exit {process.returncode}).'
                      + (f' {others} keeps running.' if others else ''), flush=True)
            if not children:
                break
            if not nomad_announced:
                if nomad.poll() not in (None, 0):
                    print('NOMAD: startup failed. Apex remains available; inspect the Docker output above.', flush=True)
                    nomad_announced = True
                elif nomad.poll() == 0 and health(opener, 'http://127.0.0.1:8080/api/health').get('status') == 'ok':
                    print('NOMAD Command Center: ready. http://127.0.0.1:8080 · check each reader and its content inside.', flush=True)
                    nomad_announced = True
            time.sleep(1)
        raise RuntimeError('Every Apex service has stopped.')
    finally:
        # Each stop is protected on its own: a second Ctrl+C (or a failed stop)
        # while one service is stopping must not leave the others running.
        for _, process in reversed(list(children)):
            for _attempt in range(2):
                try:
                    stop_owned(process)
                    break
                except BaseException:
                    continue
        if nomad is not None:
            try:
                if nomad.poll() is None:
                    launcher.stop(nomad)  # Stops only this compose client; never removes volumes.
            except BaseException:
                pass
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
