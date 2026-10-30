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
    """A service's /health, or {} for anything that isn't a proper JSON object,
    within a total deadline (a trickling peer must not freeze crash reporting)."""
    try:
        return launcher.probe_json(opener, url, 3.0)
    except Exception:   # none of them may stop the platform
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


def _stop_all(children, nomad):
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
    # The Apex + Celine launcher gets the real process environment plus this
    # launcher's own overrides, NOT the values read from .env: it reads .env
    # itself on every restart, and inherited copies would beat a setting saved
    # on the dashboard before a Restart (load_dotenv never overrides).
    apex_env = dict(os.environ)
    for key in ('APEX_APOCALYPSE', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'APEX_PLATFORM_ID'):
        apex_env[key] = normal[key]

    def fresh_offline():
        """The Apocalypse child's environment from the .env as it is now."""
        env = launcher.launch_environment()
        env['APEX_APOCALYPSE'] = '0'
        env = apocalypse.launch_environment(ROOT, env)
        env['APEX_PLATFORM_ID'], env['APEX_LAUNCH_ID'] = platform_id, uuid.uuid4().hex
        return env
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    children = []          # (name, process) still running
    ready = []             # names that became healthy
    nomad = None

    def alive(name):
        return any(n == name and p.poll() is None for n, p in children)

    def drop(name, process):
        # Stop first, forget after: a Ctrl+C in the middle leaves it in `children`,
        # so the final cleanup still stops it.
        stop_owned(process)
        if (name, process) in children:
            children.remove((name, process))

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
            apex_child = spawn([str(ROOT / 'scripts/run_apex_qwen.py'), '--fast', '--resident'], apex_env)
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
                print(f'Apex + Celine: did not start ({exc}).'
                      + (' Apocalypse remains available.' if alive('Apocalypse') else ''), flush=True)
                drop('Apex + Celine', apex_child)
        for name in ready:
            if not alive(name):                    # became ready, then died while the other was starting
                process = next((p for n, p in children if n == name), None)
                print(f'{name} stopped' + (f' (exit {process.returncode})' if process is not None else '') + '.',
                      flush=True)
                if process is not None:
                    children.remove((name, process))   # reported here, not again by the monitor loop
        ready = [name for name in ready if alive(name)]
        if not ready:
            raise RuntimeError('No Apex service is running. See the messages above.')
        if args.open:
            if 'Apocalypse' in ready:
                webbrowser.open(offline_url+'/apocalypse')
            if 'Apex + Celine' in ready:
                webbrowser.open(normal_url+'/home')
        print('Ready: '+', '.join(ready)+'. Account authorization and offline readers have their own readiness checks.', flush=True)
        print('No model, book, course or map downloads were started. Keep this console open; Ctrl+C stops its Apex services.', flush=True)
        nomad_announced = args.no_nomad or nomad is None
        restarts = []
        while children:
            dead = [(n, p) for n, p in children if p.poll() is not None]
            for item in dead:
                children.remove(item)
            for name, process in dead:
                now = time.monotonic()
                restarts = [t for t in restarts if now - t < 120]
                if name == 'Apocalypse' and process.returncode == 42 and len(restarts) < 5:
                    # Restart pressed on the Apocalypse dashboard: start it again
                    # with the settings saved since (Start-Apex-All does the same for Apex).
                    restarts.append(now)
                    print('Apocalypse: restarting as requested.', flush=True)
                    children.append(('Apocalypse', spawn([str(ROOT / 'scripts/run_apex_apocalypse.py')], fresh_offline())))
                    continue
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
        # while one service is stopping must not leave the others running. Once
        # shutdown has begun, further Ctrl+C presses are ignored until it is done.
        previous_sigint = None
        try:
            previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        except (ValueError, OSError):
            pass                                   # not the main thread
        try:
            _stop_all(children, nomad)
        finally:
            if previous_sigint is not None:        # Ctrl+C works again even if the cleanup itself failed
                try:
                    signal.signal(signal.SIGINT, previous_sigint)
                except (ValueError, OSError):
                    pass
    return 0


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('Platform launcher stopped: '+str(exc), file=sys.stderr)
        raise SystemExit(1)
