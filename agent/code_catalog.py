"""Discover coding models through signed-in CLIs, without inference or API keys."""
import copy
import json
import queue
import subprocess
import threading
import time

from agent import work_engines as we

_cache = {}
_lock = threading.Lock()
TTL = 300


class CatalogConnection:
    def __init__(self, command, timeout=12):
        self.timeout = timeout
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                     env=we._env(), **we._hidden())
        self.messages = queue.Queue(maxsize=1000)
        def read():
            for line in self.proc.stdout:
                try:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        self.messages.put_nowait(message)
                except (ValueError, queue.Full):
                    pass
            try:
                self.messages.put_nowait(None)
            except queue.Full:
                pass
        threading.Thread(target=read, daemon=True).start()

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + '\n')
        self.proc.stdin.flush()

    def receive(self, matches):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(.01, deadline-time.monotonic()))
            except queue.Empty:
                break
            if message is None:
                break
            if matches(message):
                return message
        raise RuntimeError('The coding CLI did not return its model catalog. Update it and retry.')

    def close(self):
        we._kill_tree(self.proc)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdin.close()
        self.proc.stdout.close()


def discover(engine):
    from agent.code_engines import MODEL, EFFORTS
    if engine not in ('claude', 'chatgpt'):
        raise ValueError('Choose Claude or ChatGPT.')
    signed = we.check(engine)
    if not signed['ok']:
        return {'models': [], 'source': 'unavailable', 'error': signed['why']}
    exe = we.binary(engine)
    command = [exe, 'app-server'] if engine == 'chatgpt' else [exe, '-p', '--input-format', 'stream-json',
        '--output-format', 'stream-json', '--verbose', '--permission-mode', 'plan',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}']
    connection = CatalogConnection(command)
    try:
        if engine == 'chatgpt':
            connection.send({'id': 0, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'apex', 'version': '1.0', 'title': 'Apex Code'}}})
            init = connection.receive(lambda m: m.get('id') == 0)
            if 'error' in init:
                raise RuntimeError('Codex initialization failed; update Codex and check its sign-in.')
            connection.send({'method': 'initialized', 'params': {}})
            rows, cursor = [], None
            for index in range(10):
                connection.send({'id': index+1, 'method': 'model/list', 'params': {
                    'limit': 100, 'includeHidden': False, **({'cursor': cursor} if cursor else {})}})
                reply = connection.receive(lambda m: m.get('id') == index+1)
                if 'error' in reply:
                    raise RuntimeError('Codex model discovery failed; update Codex and retry.')
                page = reply['result']
                rows.extend(page.get('data', []))
                cursor = page.get('nextCursor')
                if not cursor:
                    break
        else:
            connection.send({'type': 'control_request', 'request_id': 'apex-catalog',
                             'request': {'subtype': 'initialize', 'hooks': None}})
            reply = connection.receive(lambda m: m.get('type') == 'control_response' and
                                       m.get('response', {}).get('request_id') == 'apex-catalog')
            rows = reply.get('response', {}).get('response', {}).get('models', [])
        models, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            ident = row.get('model') or row.get('value') or row.get('id')
            if not isinstance(ident, str) or not MODEL.fullmatch(ident) or ident in seen or row.get('hidden'):
                continue
            seen.add(ident)
            efforts = [x.get('reasoningEffort') for x in row.get('supportedReasoningEfforts', []) if isinstance(x, dict)]
            models.append({'id': ident, 'label': str(row.get('displayName') or row.get('display_name') or ident)[:100],
                           'efforts': [e for e in efforts if e in EFFORTS], 'default': row.get('isDefault') is True})
        if not models:
            raise RuntimeError('This CLI returned no models; update it or enter an exact model ID.')
        return {'models': models, 'source': 'signed-in-cli', 'error': ''}
    finally:
        connection.close()


def catalog(engine, refresh=False):
    key = (engine, we.binary(engine))
    with _lock:
        cached = _cache.get(key)
        if cached and not refresh and time.monotonic()-cached[0] < TTL:
            return copy.deepcopy(cached[1])
    try:
        result = discover(engine)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        result = {'models': [], 'source': 'unavailable', 'error': 'Model discovery failed. Update the coding CLI or enter an exact model ID.'}
    with _lock:
        _cache[key] = (time.monotonic(), result)
        return copy.deepcopy(result)
