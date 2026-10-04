"""Offline readiness and Python network boundary for Apex Apocalypse.

This is an application mode, not an operating-system firewall. Native programs,
browser tabs and Docker containers have their own networking. Test with internet
disconnected before depending on the prepared machine.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

_guard_installed = False

LOCAL_TOOLS = frozenset({
    'read_file', 'write_file', 'append_file', 'list_dir', 'find_files',
    'current_time', 'remember', 'recall', 'kb_search', 'apex_note',
    'list_skills', 'document_read', 'document_list', 'document_write',
    'list_goals', 'set_goal', 'update_goal', 'skill_manage',
    'project_checkpoint',
})


class OfflineUnavailable(RuntimeError):
    pass


def enabled():
    return os.getenv('APEX_APOCALYPSE', '').lower() in {'1', 'true', 'yes'}


def local_host(host):
    if isinstance(host, bytes):
        host = host.decode('ascii', errors='replace')
    if host == 'localhost':
        return True
    try:
        address = ipaddress.ip_address(str(host))
        return address.is_loopback or bool(getattr(address, 'ipv4_mapped', None)
                                           and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def loopback_url(value):
    parts = urlsplit(value)
    if parts.scheme != 'http' or not local_host(parts.hostname) or parts.username or parts.password:
        raise OfflineUnavailable('Apocalypse mode requires a local HTTP service on this computer.')
    if parts.query or parts.fragment:
        raise OfflineUnavailable('Local service addresses cannot contain a query or fragment.')
    return value.rstrip('/')


def _audit(event, args):
    if not enabled():
        return
    if event == 'socket.getaddrinfo' and not local_host(args[0]):
        raise OfflineUnavailable('Internet name lookups are paused in Apex Apocalypse.')
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and not local_host(address[0]):
            raise OfflineUnavailable('Internet connections are paused in Apex Apocalypse.')


def install_network_guard():
    global _guard_installed
    if enabled() and not _guard_installed:
        sys.addaudithook(_audit)
        _guard_installed = True


def library_root():
    return Path(os.getenv('APEX_APOCALYPSE_HOME', r'D:\Apex-Apocalypse')).expanduser().resolve()


def ollama_url():
    return loopback_url(os.getenv('APEX_APOCALYPSE_OLLAMA', 'http://127.0.0.1:11435'))


def model_name():
    return os.getenv('APEX_APOCALYPSE_MODEL', 'qwen3:4b').removeprefix('ollama/')


def is_local_model(row):
    name = str(row.get('name') or row.get('model') or '')
    return bool(name) and ':cloud' not in name.lower() and not row.get('remote_model') and not row.get('remote_host')


def verify_model(model, base):
    """Reject cloud aliases even when the Ollama daemon itself is local."""
    import httpx
    name = model.removeprefix('ollama/')
    if not model.startswith('ollama/') or ':cloud' in name.lower():
        raise OfflineUnavailable('Choose a downloaded local Ollama model in Apocalypse mode.')
    base = loopback_url(base).removesuffix('/v1')
    try:
        with httpx.Client(trust_env=False, timeout=5, follow_redirects=False) as client:
            response = client.post(base + '/api/show', json={'model': name})
            response.raise_for_status()
            data = response.json()
        if data.get('remote_model') or data.get('remote_host') or not data.get('model_info'):
            raise OfflineUnavailable('This model does not have verified local weights. Prepare a local model first.')
    except (httpx.HTTPError, ValueError) as exc:
        raise OfflineUnavailable('Local model unavailable. Run Setup-Apex-Apocalypse.cmd while online first.') from exc


def launch_environment(root, environ=None):
    """Child-only settings. The normal .env and system settings stay intact."""
    from dotenv import dotenv_values
    env = {k: v for k, v in dotenv_values(Path(root) / '.env').items() if v is not None}
    env.update(os.environ if environ is None else environ)
    model = 'ollama/' + env.get('APEX_APOCALYPSE_MODEL', 'qwen3:4b').removeprefix('ollama/')
    base = loopback_url(env.get('APEX_APOCALYPSE_OLLAMA', 'http://127.0.0.1:11435'))
    for key in list(env):
        if key.endswith('_API_KEY') or key.endswith('_TOKEN') and key != 'DASHBOARD_TOKEN':
            # Preserve the owner login; cloud providers/apps are not used in this child.
            env[key] = ''
        if key.lower() in {'http_proxy', 'https_proxy', 'all_proxy'}:
            env.pop(key)
    for key in ('AGENT_MODEL', 'BACKGROUND_MODEL', 'PROACTIVE_MODEL', 'CONSTELLATION_PLANET_MODEL',
                'CONSTELLATION_SYNTH_MODEL', 'CONSTELLATION_MEMORY_MODEL', 'TIME_CAPSULE_MODEL',
                'SAFETY_REVIEW_MODEL', 'ROUTING_SIMPLE_MODEL'):
        env[key] = model
    env.update(APEX_APOCALYPSE='1', OLLAMA_BASE_URL=base+'/v1',
               APEX_APOCALYPSE_MODEL=model.removeprefix('ollama/'), APEX_APOCALYPSE_OLLAMA=base,
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               APEX_APOCALYPSE_HOME=env.get('APEX_APOCALYPSE_HOME', r'D:\Apex-Apocalypse'),
               DASHBOARD_HOST='127.0.0.1', DASHBOARD_PORT=env.get('APEX_APOCALYPSE_PORT', '7862'),
               OPENAI_STT_ENGINE='local', RELAY_ENABLED='false', NODE_WORKER_ENABLED='false',
               SUBSCRIPTION_ENABLED='false', CONSTELLATION_AUTO='false')
    return env


def read_local_file(path, offset=0, page=1):
    """Bounded local text/PDF extraction so a book cannot crowd out the model."""
    if str(path).startswith(('\\\\', '//')):
        return 'Network file shares are paused in Apocalypse mode.'
    target = Path(path).expanduser()
    start = max(0, int(offset))
    try:
        if target.suffix.lower() == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(target)
            index = max(1, int(page)) - 1
            if index >= len(reader.pages):
                return 'PDF page is out of range.'
            text = reader.pages[index].extract_text() or ''
            if not text.strip():
                return f'{target.name}, page {index + 1}: no extractable text. Scanned pages need prepared OCR.'
            excerpt = text[start:start + 4000]
            label = f'{target}, PDF page {index + 1}/{len(reader.pages)}'
            more = start + len(excerpt) < len(text)
        else:
            with target.open('r', encoding='utf-8', errors='replace') as source:
                # Character offsets (rather than byte seeks) preserve UTF-8 text.
                remaining = start
                while remaining:
                    part = source.read(min(remaining, 65536))
                    if not part:
                        break
                    remaining -= len(part)
                excerpt = source.read(4000)
                more = bool(source.read(1))
            label = str(target)
        return (f'[{label}; characters {start}-{start + len(excerpt)}; '
                f'more text: {more}. Use offset/page to continue.]\n' + excerpt)
    except Exception as exc:
        return f'Could not read local file: {type(exc).__name__}. Check its path and format.'
