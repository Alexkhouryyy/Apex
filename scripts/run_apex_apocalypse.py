"""Start a separate local-only Apex dashboard with downloaded Ollama weights.

No automatic model downloads, cloud fallback, MCP discovery or telemetry jobs.
The existing main/resident launcher can also use the same child environment.
"""
from pathlib import Path
import argparse
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resident', action='store_true', help='Use full resident mode instead of the quiet offline dashboard')
    args = parser.parse_args()
    from agent.apocalypse import launch_environment
    env = launch_environment(ROOT)
    for key in list(os.environ):
        if key.lower() in {'http_proxy', 'https_proxy', 'all_proxy'}:
            os.environ.pop(key)
    os.environ.update(env)
    import config
    from agent import apocalypse, schema
    apocalypse.install_network_guard()
    from scripts.setup_apex_apocalypse import ensure_ollama
    daemon = ensure_ollama(apocalypse.library_root(), apocalypse.ollama_url(), offline=True)
    try:
        try:
            apocalypse.verify_model(config.AGENT_MODEL, config.OLLAMA_BASE_URL)
            print('Apex Apocalypse: local weights verified. Cloud model access is paused.', flush=True)
        except apocalypse.OfflineUnavailable as exc:
            print(f'Local brain needs preparation: {exc}\nThe readiness screen remains available.', flush=True)
        if args.resident:
            from app.resident import run_resident
            run_resident(model_override=config.AGENT_MODEL)
            return
        import socket
        with socket.socket() as sock:
            try:
                sock.bind((config.DASHBOARD_HOST, config.DASHBOARD_PORT))
            except OSError:
                raise RuntimeError('Apocalypse port is already in use. Close the previous offline session first.')
        schema.init_all()
        from agent.core import AgentCore
        from dashboard import server
        server.set_agent(AgentCore())
        print(f'Open http://127.0.0.1:{config.DASHBOARD_PORT}/apocalypse', flush=True)
        print('Keep this console open. Ctrl+C stops this offline session.', flush=True)
        import uvicorn
        uvicorn.run(server.app, host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, log_level='warning')
    finally:
        if daemon is not None:
            daemon.terminate()
            try:
                daemon.wait(timeout=10)
            except Exception:
                daemon.kill()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Apex Apocalypse stopped.')
    except Exception as exc:
        print(f'Apocalypse stopped: {exc}', file=sys.stderr)
        raise SystemExit(1)
