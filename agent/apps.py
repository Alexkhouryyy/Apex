"""Owner-scoped app catalog and managed accounts, using Composio's v3.1 API.

The provider handles OAuth/token refresh. Apex owns discovery, permissions and
execution. No provider meta-tool, sandbox, batch executor or proxy is exposed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlparse
import uuid

import httpx
import jsonschema

BASE = 'https://backend.composio.dev/api/v3.1'
ROOT = Path(__file__).resolve().parent.parent
_lock = threading.RLock()
_tool_cache = {}


class AppError(ValueError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _key():
    return os.getenv('COMPOSIO_API_KEY', '').strip()


def _path():
    return ROOT / '.mcp-runtime' / 'apps.json'


def _state():
    fingerprint = hashlib.sha256(_key().encode()).hexdigest()
    if _path().exists():
        try:
            data = json.loads(_path().read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise AppError('App settings could not be read. Restore the local apps.json file before changing connections.') from exc
        if data.get('key_fingerprint') == fingerprint:
            return data
    return {'key_fingerprint': fingerprint, 'owner': 'apex-' + str(uuid.uuid4()),
            'session_id': None, 'apps': {}}


def _save(data, *, invalidate=True):
    with _lock:
        path = _path()
        temp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # A private sibling file keeps replacement atomic and prevents two
            # processes from overwriting the same apps.tmp. Close it before the
            # rename: Windows cannot replace a file with an open writer handle.
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=path.parent, prefix='.apps-', suffix='.tmp', delete=False) as stream:
                temp = Path(stream.name)
                json.dump(data, stream, indent=2)
            for attempt in range(6):
                try:
                    temp.replace(path)
                    break
                except OSError as exc:
                    # Scanners/readers can briefly deny replacement on Windows.
                    # Retry only the local rename, never the provider operation.
                    if getattr(exc, 'winerror', None) not in {5, 32, 33} or attempt == 5:
                        raise
                    time.sleep(0.05 * (2 ** attempt))
        except OSError as exc:
            raise AppError('App settings could not be saved. Existing saved settings were preserved. '
                           'Close programs locking .mcp-runtime/apps.json or check its folder permissions, '
                           'then retry. If account authorization already opened, check its status first.') from exc
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass  # A scanner may still hold the unused temporary file.
        if invalidate:
            _tool_cache.clear()


def _request(method, path, *, params=None, body=None, key=None):
    secret = key if key is not None else _key()
    if not secret:
        raise AppError('Connect the app catalog in Apps settings first.')
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.request(method, BASE + path, params=params, json=body,
                                      headers={'x-api-key': secret})
    except httpx.TimeoutException as exc:
        raise AppError('The app provider timed out. An action already sent may have completed; check the app before retrying it.') from exc
    except httpx.HTTPError as exc:
        raise AppError('The app provider is unreachable. Check your connection and try again.') from exc
    if response.status_code >= 300:
        messages = {401: 'The catalog key was rejected. Update it in Apps settings.',
                    403: 'Your provider project does not permit this connection or action.',
                    402: 'The provider requires billing or a plan change. No purchase was made.',
                    404: 'This connection or tool no longer exists. Reconnect the app.',
                    429: 'The provider rate limit was reached. Try again later.'}
        raise AppError(messages.get(response.status_code, f'App provider request failed (HTTP {response.status_code}). Check the provider dashboard for details.'), response.status_code)
    if response.status_code == 204 or not response.content:
        return {}
    try:
        data = response.json()
    except ValueError as exc:
        raise AppError('The app provider returned an invalid response.') from exc
    if not isinstance(data, dict):
        raise AppError('The app provider returned an unexpected response.')
    return data


def _slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', value):
        raise AppError('Invalid app identifier.')
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', value):
        raise AppError('The provider returned an invalid identifier.')
    return value


def configure(key):
    if not isinstance(key, str) or not 8 <= len(key.strip()) <= 500 or any(c in key for c in '\r\n'):
        raise AppError('Enter a valid Composio project API key.')
    key = key.strip()
    _request('GET', '/toolkits', params={'limit': 1}, key=key)
    with _lock:
        from scripts.set_env_key import set_key
        set_key(ROOT / '.env', 'COMPOSIO_API_KEY', key)
        os.environ['COMPOSIO_API_KEY'] = key
        _save(_state())
    return status()


def status():
    with _lock:
        data = _state()
        return {'configured': bool(_key()), 'provider': 'Composio',
                'connections': [{'slug': slug, **{k: v for k, v in row.items()
                   if k in ('name', 'status', 'enabled', 'checked_at', 'error', 'no_auth')}}
                    for slug, row in data['apps'].items()]}


def _card(item, saved):
    meta = item.get('meta') or {}
    slug = _slug(item['slug'])
    row = saved.get(slug, {})
    return {'slug': slug, 'name': str(item.get('name') or slug),
            'description': str(meta.get('description') or '')[:1200],
            'categories': [str(c.get('name') or c.get('id') or '') for c in meta.get('categories', []) if isinstance(c, dict)],
            'tool_count': meta.get('tools_count'),
            'no_auth': bool(item.get('no_auth', item.get('is_no_auth', meta.get('isNoAuth', False)))),
            'status': row.get('status', 'not_connected'), 'enabled': row.get('enabled', False)}


def catalog(query='', category='', cursor=''):
    params = {'limit': 48, 'sort_by': 'alphabetically'}
    if query: params['search'] = query[:200]
    if category: params['category'] = category[:100]
    if cursor: params['cursor'] = cursor[:1000]
    page = _request('GET', '/toolkits', params=params)
    with _lock:
        saved = _state()['apps']
        return {'items': [_card(i, saved) for i in page.get('items', [])],
                'next_cursor': page.get('next_cursor'), 'total_items': page.get('total_items')}


def categories():
    page = _request('GET', '/toolkits/categories')
    return {'items': [{'id': str(i.get('id') or i.get('slug') or ''),
                       'name': str(i.get('name') or '')} for i in page.get('items', [])]}


def _session(data, extra=None):
    enabled = sorted(set([s for s, r in data['apps'].items() if r.get('enabled')] + ([extra] if extra else [])))
    settings = {'toolkits': {'enable': enabled}, 'premium_usage': False,
                'manage_connections': {'enable': False}, 'workbench': {'enable': False},
                'preload': {'tools': 'all'}}
    accounts = {s: [r['account_id']] for s, r in data['apps'].items()
                if s in enabled and r.get('account_id') and r.get('status') != 'disconnected'}
    if accounts: settings['connected_accounts'] = accounts
    if data['session_id']:
        try:
            _request('PATCH', '/tool_router/session/' + _identifier(data['session_id']), body=settings)
        except AppError as exc:
            if exc.status_code != 404: raise
            data['session_id'] = None
    if not data['session_id']:
        result = _request('POST', '/tool_router/session', body={'user_id': data['owner'], **settings})
        data['session_id'] = _identifier(result['session_id'])
        _save(data)
    return '/tool_router/session/' + data['session_id']


def connect(slug):
    slug = _slug(slug)
    with _lock:
        data = _state()
        card = _card(_request('GET', '/toolkits/' + slug), data['apps'])
        current = data['apps'].get(slug, {})
        if current.get('enabled') and current.get('status') == 'connected':
            return {'connected': True, 'slug': slug}
        if current.get('status') == 'pending' and current.get('redirect_url') and time.time() - current.get('linked_at', 0) < 600:
            return {'connected': False, 'slug': slug, 'redirect_url': current['redirect_url']}
        if current.get('account_id'):
            # Finish removing the old managed connection before creating another.
            # Local denial is persisted before the network operation.
            current.update(enabled=False, status='disconnect_pending')
            _save(data)
            _request('DELETE', '/connected_accounts/' + _identifier(current['account_id']))
            current.pop('account_id', None)
            current['status'] = 'disconnected'
            _save(data)
        path = _session(data, extra=slug)
        row = {'name': card['name'], 'enabled': True, 'no_auth': card['no_auth'],
               'status': 'pending', 'checked_at': 0}
        if card['no_auth']:
            row['status'] = 'connected'
            data['apps'][slug] = row
            _save(data)
            return {'connected': True, 'slug': slug}
        result = _request('POST', path + '/link', body={'toolkit': slug})
        url = str(result.get('redirect_url') or '')
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or not (parsed.hostname == 'composio.dev' or parsed.hostname.endswith('.composio.dev')) or parsed.username or parsed.password:
            raise AppError('The provider returned an unexpected authorization URL.')
        row['account_id'] = _identifier(result['connected_account_id'])
        row['redirect_url'] = url
        row['linked_at'] = time.time()
        data['apps'][slug] = row
        _save(data)
        return {'connected': False, 'slug': slug, 'redirect_url': url}


def _pages(path, params, max_pages=50):
    items, seen = [], set()
    for _ in range(max_pages):
        page = _request('GET', path, params=params)
        items.extend(page.get('items', []))
        cursor = page.get('next_cursor')
        if not cursor: return items
        if cursor in seen: break
        seen.add(cursor)
        params = {**params, 'cursor': cursor}
    raise AppError('The provider catalog exceeded its pagination limit. Narrow the enabled apps and retry.')


def _refresh(data):
    if not data['session_id'] or not data['apps']: return
    before = [(s, r.get('status')) for s, r in data['apps'].items()]
    selected = [s for s, r in data['apps'].items() if r.get('enabled')]
    for offset in range(0, len(selected), 50):
        batch = selected[offset:offset+50]
        params = {'toolkits': ','.join(batch), 'limit': 50}
        try:
            items = _pages('/tool_router/session/' + _identifier(data['session_id']) + '/toolkits', params)
        except AppError as exc:
            if exc.status_code != 404: raise
            _session(data)
            items = _pages('/tool_router/session/' + _identifier(data['session_id']) + '/toolkits', params)
        by_slug = {i['slug']: i for i in items}
        for slug in batch:
            row = data['apps'][slug]
            item = by_slug.get(slug, {})
            account = item.get('connected_account') or {}
            # The browser redirect is not evidence of account ownership or success.
            owns = account.get('user_id') == data['owner'] and account.get('id') == row.get('account_id')
            active = owns and str(account.get('status', '')).upper() == 'ACTIVE'
            no_auth = row.get('no_auth') and bool(item.get('is_no_auth', item.get('no_auth', (item.get('meta') or {}).get('isNoAuth', False))))
            pending = row.get('status') == 'pending' and time.time() - row.get('linked_at', 0) < 600
            row['status'] = 'connected' if active or no_auth else ('pending' if pending else 'needs_reconnect')
            if row['status'] == 'connected': row.pop('redirect_url', None)
            row['checked_at'] = time.time()
    _save(data, invalidate=before != [(s, r.get('status')) for s, r in data['apps'].items()])


def refresh():
    with _lock:
        data = _state()
        _refresh(data)
    return status()


def set_enabled(slug, enabled):
    with _lock:
        data = _state()
        row = data['apps'].get(_slug(slug))
        if not row: raise AppError('Connect this app first.')
        if type(enabled) is not bool: raise AppError('Enabled must be true or false.')
        row['enabled'] = enabled
        if not enabled: row['status'] = 'disabled'
        _save(data)  # local denial takes effect even when the provider is down
        _session(data)
        if enabled: _refresh(data)
    return status()


def disconnect(slug):
    with _lock:
        data = _state()
        row = data['apps'].get(_slug(slug))
        if not row: raise AppError('This app is not connected.')
        row.update(enabled=False, status='disconnect_pending')
        _save(data)
        if row.get('account_id'):
            # Only the account returned by this owner's link request is removed.
            _request('DELETE', '/connected_accounts/' + _identifier(row['account_id']))
        row.pop('account_id', None)
        row.pop('redirect_url', None)
        row['status'] = 'disconnected'
        _save(data)
        _session(data)
    return status()


def _schema(raw):
    if not isinstance(raw, dict): raise AppError('Invalid tool schema.')
    if 'type' not in raw and 'properties' not in raw:
        raw = {'type': 'object', 'properties': {k: {a: b for a, b in v.items() if a != 'required'}
                for k, v in raw.items() if isinstance(v, dict)},
               'required': [k for k, v in raw.items() if isinstance(v, dict) and v.get('required') is True]}
    jsonschema.Draft202012Validator.check_schema(raw)
    return raw


def tools():
    if not _key(): return []
    with _lock:
        data = _state()
        active = sorted(s for s, r in data['apps'].items() if r.get('enabled') and r.get('status') == 'connected')
        if not active or not data['session_id']: return []
        cache_key = (data['session_id'], tuple(active))
        cached = _tool_cache.get(cache_key)
        if cached and time.time() - cached[0] < 300: return cached[1]
        items = _pages('/tool_router/session/' + _identifier(data['session_id']) + '/tools', {'limit': 500})
        result = []
        for item in items:
            toolkit = (item.get('toolkit') or {}).get('slug')
            slug = item.get('slug', '')
            if toolkit not in active or not re.fullmatch(r'[A-Z0-9_]{1,180}', slug) or slug.startswith('COMPOSIO_') or item.get('is_deprecated'):
                continue
            result.append({'name': f'app__{toolkit}__{slug}', 'description': str(item.get('description') or '')[:4000],
                           'input_schema': _schema(item.get('input_parameters', {})), 'toolkit': toolkit,
                           'slug': slug, 'tags': item.get('tags') or []})
        _tool_cache[cache_key] = (time.time(), result)
        return result


def call(name, arguments):
    from agent import mcp_policy, subagent_scope
    tool = next((t for t in tools() if t['name'] == name), None)
    if not tool: raise AppError('This tool is unavailable. Connect and enable its app, then search again.')
    action = tool['slug'].removeprefix(tool['toolkit'].upper() + '_').lower()
    policy_name = f"mcp__apps_{tool['toolkit']}__{action}"
    if blocked := subagent_scope.check(policy_name): return blocked
    try:
        jsonschema.validate(arguments, tool['input_schema'])
    except jsonschema.ValidationError as exc:
        raise AppError('Arguments do not match this tool. Read its schema and correct the inputs.') from exc
    annotations = {'destructiveHint': 'destructiveHint' in tool['tags']}
    if blocked := mcp_policy.enforce(policy_name, arguments, annotations): return blocked
    verdict = mcp_policy.decide(policy_name, annotations)
    started = time.time()
    try:
        with _lock:
            data = _state()
            _refresh(data)
            row = data['apps'].get(tool['toolkit'], {})
            if not row.get('enabled') or row.get('status') != 'connected':
                raise AppError('This app is disabled or needs to reconnect. No action was sent.')
            body = {'tool_slug': tool['slug'], 'arguments': arguments, 'enable_auto_workbench_offload': False}
            if row.get('account_id'): body['account'] = row['account_id']
            result = _request('POST', '/tool_router/session/' + _identifier(data['session_id']) + '/execute', body=body)
            if result.get('error') or result.get('successful') is False:
                raise AppError('The app rejected this action. Check the provider log ' + str(result.get('log_id') or '')[:100] + ' before retrying.')
        mcp_policy.record(verdict, arguments, decision='completed', duration_ms=int((time.time()-started)*1000), ok=True)
        return json.dumps({'data': result.get('data'), 'log_id': result.get('log_id')}, ensure_ascii=False)
    except Exception:
        mcp_policy.record(verdict, arguments, decision='failed', duration_ms=int((time.time()-started)*1000), ok=False, error='App execution failed; inspect its result before retrying.')
        raise
