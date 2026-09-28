"""Versioned, opt-in executable plugins. Enabling grants trusted Python host access.

Supports the general Hermes register(ctx) tool/observer/command conventions.
Provider contracts below are Apex-specific; this is not a Hermes emulator.
Discovery and repository review never import plugin code.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import sys
import threading
import time
import uuid
import zipfile

import httpx
import jsonschema
import yaml
from packaging.requirements import Requirement
from agent import continuity, repository_hub

ROOT = Path.home() / '.apex' / 'plugins'
BUNDLED = Path(__file__).resolve().parent.parent / 'apex_plugins'
KEY = 'executable-plugins'
LOCK = threading.RLock()
_cache = {}
_errors = {}
_failed = {}
_inside_provider = contextvars.ContextVar('apex_plugin_provider', default=False)
_inside_hook = contextvars.ContextVar('apex_plugin_hook', default=False)
LOG = logging.getLogger(__name__)
HOOKS = {'pre_tool_call', 'post_tool_call'}
DEFAULT = {'installed': {}, 'reviews': {}, 'providers': {'memory': '', 'context': ''}}


def _state():
    return continuity.read(KEY, copy.deepcopy(DEFAULT))


def _save(state):
    return continuity.write(KEY, state['data'], state['revision'])


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,47}', value):
        raise ValueError('Plugin names must start with a letter and use up to 48 letters, digits, - or _.')
    return value


def _relative(value):
    if not isinstance(value, str) or len(value) > 240:
        raise ValueError('Invalid plugin path.')
    if value in ('', '.'):
        return ''
    p = PurePosixPath(value)
    if p.is_absolute() or '\\' in value or any(x in ('', '.', '..') for x in value.split('/')):
        raise ValueError('Plugin path must stay inside the repository.')
    for part in p.parts:
        if any(c in part for c in ':<>"|?*') or any(ord(c)<32 for c in part) or part.endswith((' ', '.')) or re.match(r'(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)', part):
            raise ValueError('Plugin path is not portable to Windows.')
    return str(p)


def _folder(token):
    if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('Unknown plugin package.')
    return ROOT / 'packages' / token


def _digest(folder):
    h = hashlib.sha256()
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise ValueError('Plugin symlinks are unsupported.')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            h.update(path.relative_to(folder).as_posix().encode())
            h.update(b'\0')
            h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def _manifest(folder):
    p = folder / 'plugin.yaml'
    if not p.is_file() or p.stat().st_size > 64000:
        raise ValueError('Choose a directory containing plugin.yaml (maximum 64 KB).')
    text = p.read_text(encoding='utf-8')
    if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(text)):
        raise ValueError('YAML aliases are unsupported in plugin manifests.')
    d = yaml.safe_load(text)
    if not isinstance(d, dict):
        raise ValueError('plugin.yaml must be a mapping.')
    _name(d.get('name'))
    if not (folder / '__init__.py').is_file():
        raise ValueError('This plugin needs __init__.py with register(ctx). UI-only and portable packages require an adapter.')
    # Round-trip bounds aliases and non-JSON YAML types before persisting/displaying.
    try:
        encoded = json.dumps(d)
        if len(encoded) > 64000:
            raise ValueError('Manifest is too large.')
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('Manifest must contain bounded JSON-compatible values.') from exc
    for field in ('provides_tools', 'provides_hooks', 'capabilities', 'python_dependencies', 'requires_plugins', 'requires_env'):
        if field in d and not isinstance(d[field], list):
            raise ValueError(f'{field} must be a list.')
    if not isinstance(d.get('config_schema', {}), dict):
        raise ValueError('config_schema must be a mapping.')
    return d


def _requirements(folder, manifest):
    p = folder / 'pyproject.toml'
    if p.is_file():
        import tomllib
        data = tomllib.loads(p.read_text(encoding='utf-8'))
        return data.get('project', {}).get('dependencies', [])
    return manifest.get('python_dependencies', manifest.get('pip_dependencies', []))


def _issues(folder, m):
    issues = []
    if m.get('kind', 'standalone') not in ('standalone', 'backend'):
        issues.append('This Hermes plugin kind needs an Apex adapter: ' + str(m['kind']))
    if m.get('capabilities'):
        issues.append('Privileged Hermes capabilities are not implemented: ' + ', '.join(map(str, m['capabilities'])))
    if m.get('requires_plugins'):
        issues.append('Cross-plugin dependencies require an Apex adapter.')
    if m.get('requires_hermes'):
        issues.append('This package explicitly requires the Hermes runtime.')
    for hook in m.get('provides_hooks', []):
        if hook not in HOOKS:
            issues.append('Unsupported lifecycle hook: ' + str(hook))
    for item in m.get('requires_env', []):
        key = item.get('name') if isinstance(item, dict) else item
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            issues.append('Invalid requires_env declaration.')
        elif not os.getenv(key):
            issues.append(f'Missing environment variable: {key}. Configure it before enabling.')
    try:
        for value in _requirements(folder, m):
            req = Requirement(value)
            if req.url:
                issues.append('Direct-URL dependencies require an external runtime.')
                continue
            if req.marker and not req.marker.evaluate():
                continue
            try:
                version = importlib.metadata.version(req.name)
                if version not in req.specifier:
                    issues.append(f'Dependency mismatch: {req}; installed {version}.')
            except importlib.metadata.PackageNotFoundError:
                issues.append(f'Missing dependency: {req}. Dependencies are not installed automatically.')
    except Exception:
        issues.append('Invalid dependency declaration.')
    return issues


def _download(repo, sha):
    data = bytearray()
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        with client.stream('GET', f'https://codeload.github.com/{repo}/zip/{sha}') as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 25_000_000:
                    raise ValueError('Plugin archive exceeds 25 MB.')
    return bytes(data)


def _extract(raw, subdir, folder):
    """Manual bounded extraction: never trust ZIP paths, modes or extractall."""
    selected, seen, total = [], set(), 0
    prefix = subdir + '/' if subdir else ''
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.infolist()) > 20000:
            raise ValueError('Repository archive has too many entries.')
        roots = {x.filename.split('/')[0] for x in archive.infolist()}
        if len(roots) != 1:
            raise ValueError('Expected a GitHub archive with one root directory.')
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            _relative(entry.orig_filename)
            relative = entry.filename.partition('/')[2]
            _relative(relative)
            if not relative.startswith(prefix):
                continue
            path = relative[len(prefix):]
            _relative(path)
            if '__pycache__' in PurePosixPath(path).parts or path.endswith('.pyc') or '.git' in PurePosixPath(path).parts:
                raise ValueError('Generated Python bytecode and Git metadata cannot be installed.')
            mode = entry.external_attr >> 16
            if mode & 0o170000 not in (0, 0o100000):
                raise ValueError('Archive links and special files are unsupported.')
            key = path.casefold()
            if key in seen:
                raise ValueError('Archive contains duplicate Windows paths.')
            seen.add(key)
            total += entry.file_size
            if entry.file_size > 5_000_000 or total > 50_000_000 or len(seen) > 1000:
                raise ValueError('Plugin exceeds the file count or size limit.')
            selected.append((entry, path))
        if not selected:
            raise ValueError('Plugin subdirectory was not found.')
        for entry, path in selected:
            dest = folder / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read(entry))


def preview(source, ref='HEAD', subdir=''):
    repo = repository_hub.parse_repo(source)
    subdir = _relative(subdir)
    if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}', ref) or '..' in ref:
        raise ValueError('Choose a branch, tag or commit.')
    from urllib.parse import quote
    sha = repository_hub.github(repo + '/commits/' + quote(ref, safe='')).get('sha', '')
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('GitHub did not return an immutable revision.')
    token = uuid.uuid4().hex
    folder = _folder(token)
    folder.mkdir(parents=True)
    _extract(_download(repo, sha), subdir, folder)
    manifest = _manifest(folder)
    result = dict(id=token, repo=repo, revision=sha, ref=ref, subdir=subdir,
                  manifest=manifest, digest=_digest(folder), reviewed_at=time.time())
    with LOCK:
        state = _state()
        state['data']['reviews'][token] = result
        _save(state)
    return dict(**result, issues=_issues(folder, manifest), files=_files(folder),
                notice='Review does not run code. Installation is disabled by default. Enabling runs Python with Apex host permissions.')


def preview_bundled(name):
    import shutil
    folder = BUNDLED / _name(name)
    if not folder.is_dir() or folder.is_symlink() or any(p.is_symlink() for p in folder.rglob('*')):
        raise ValueError('Bundled plugin not found.')
    token = uuid.uuid4().hex
    dest = _folder(token)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    manifest = _manifest(dest)
    digest = _digest(dest)
    result = dict(id=token, repo='bundled/' + name, revision=digest, ref='bundled', subdir='',
                  manifest=manifest, digest=digest, reviewed_at=time.time())
    with LOCK:
        state = _state()
        state['data']['reviews'][token] = result
        _save(state)
    return dict(**result, issues=_issues(dest, manifest), files=_files(dest), notice='Bundled with Apex. Review and install disabled, then enable to activate.')


def _files(folder):
    return [dict(path=p.relative_to(folder).as_posix(), bytes=p.stat().st_size)
            for p in sorted(folder.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']


def review_file(token, path):
    with LOCK:
        state = _state()['data']
        packages = list(state['reviews'].values()) + [p for v in state['installed'].values() for p in [v['package'], *v.get('history', [])]]
        if not any(p['id'] == token for p in packages):
            raise ValueError('Unknown plugin review.')
    path = _relative(path)
    file = _folder(token) / path
    if not file.resolve().is_relative_to(_folder(token).resolve()) or file.is_symlink() or not file.is_file() or file.stat().st_size > 200000:
        raise ValueError('Choose a text file smaller than 200 KB.')
    try:
        return {'path': path, 'content': file.read_text(encoding='utf-8')}
    except UnicodeDecodeError as exc:
        raise ValueError('This file is binary.') from exc


def install(token, replace=None):
    with LOCK:
        state = _state()
        p = state['data']['reviews'].get(token)
        if not p:
            raise ValueError('Review the repository first.')
        if _digest(_folder(token)) != p['digest']:
            raise ValueError('Reviewed package changed; inspect it again.')
        name = p['manifest']['name']
        old = state['data']['installed'].get(name)
        if old and replace != old['package']['digest']:
            raise ValueError('Plugin already installed or changed. Review the update before replacing it.')
        if replace and not old:
            raise ValueError('Plugin was removed. Review a fresh installation.')
        if old and (old['package']['repo'], old['package']['subdir']) != (p['repo'], p['subdir']):
            raise ValueError('An update must use the installed repository and subdirectory.')
        history = ([old['package']] + old.get('history', []))[:5] if old else []
        state['data']['installed'][name] = dict(package=p, enabled=False, settings=old['settings'] if old else {}, history=history)
        _clear_providers(state['data'], name)
        _save(state)
        _cache.pop(name, None)
        _errors.pop(name, None)
    return {'name': name, 'enabled': False, 'message': 'Installed disabled. Review settings, then enable.'}


def _settings(manifest, values):
    if not isinstance(values, dict):
        raise ValueError('Settings must be an object.')
    out = {}
    schema = manifest.get('config_schema', {})
    if set(values) - set(schema):
        raise ValueError('Unknown plugin setting.')
    types = {'str': str, 'string': str, 'int': int, 'integer': int, 'float': (int, float), 'number': (int, float), 'bool': bool, 'boolean': bool, 'list': list, 'array': list, 'dict': dict, 'object': dict}
    for key, spec in schema.items():
        if not isinstance(spec, dict) or spec.get('type') == 'secret' or spec.get('secret'):
            raise ValueError('Use requires_env for secrets; inline secret settings require an adapter.')
        if key not in values and 'default' not in spec:
            if spec.get('required'):
                raise ValueError('Missing setting: ' + key)
            continue
        value = values.get(key, spec.get('default'))
        expected = types.get(spec.get('type', 'str'))
        if expected is None or not isinstance(value, expected) or (isinstance(value, bool) and expected != bool):
            raise ValueError('Invalid type for setting: ' + key)
        if 'enum' in spec and value not in spec['enum']:
            raise ValueError('Invalid choice for setting: ' + key)
        out[key] = value
    return out


def configure(name, values):
    with LOCK:
        state = _state()
        item = state['data']['installed'].get(_name(name))
        if not item:
            raise ValueError('Plugin not installed.')
        if item['enabled']:
            raise ValueError('Disable the plugin before changing settings.')
        item['settings'] = _settings(item['package']['manifest'], values)
        _save(state)
        _cache.pop(name, None)
    return {'message': 'Settings saved.'}


def _clear_providers(data, name):
    for kind, selected in data['providers'].items():
        if selected.startswith(name + ':'):
            data['providers'][kind] = ''


def set_enabled(name, enabled, trust=False):
    if type(enabled) is not bool:
        raise ValueError('enabled must be a boolean.')
    with LOCK:
        state = _state()
        item = state['data']['installed'].get(_name(name))
        if not item:
            raise ValueError('Plugin not installed.')
        if enabled:
            if trust is not True:
                raise ValueError('Confirm that you trust this plugin to run Python with Apex host permissions.')
            _failed.pop(name, None)
            ctx = _load(item)  # registration must succeed before publishing activation
            item['registered'] = dict(tools=list(ctx.tools), commands=list(ctx.commands), hooks=list(ctx.hooks), providers=list(ctx.providers))
        item['enabled'] = enabled
        if not enabled:
            _clear_providers(state['data'], name)
        _save(state)
        if not enabled:
            _cache.pop(name, None)
    return {'enabled': enabled, 'message': 'Enabled for subsequent calls.' if enabled else 'Disabled. An already-running call may finish.'}


def change(name, action):
    with LOCK:
        state = _state()
        item = state['data']['installed'].get(_name(name))
        if not item:
            raise ValueError('Plugin not installed.')
        if action == 'remove':
            del state['data']['installed'][name]
        elif action == 'rollback':
            if not item['history']:
                raise ValueError('No previous version saved.')
            old = item['package']
            item['package'] = item['history'].pop(0)
            item['history'].insert(0, old)
            item['enabled'] = False
        else:
            raise ValueError('Unknown plugin action.')
        _clear_providers(state['data'], name)
        _save(state)
        _cache.pop(name, None)
        _errors.pop(name, None)
    return {'message': 'Removed from the runtime; package files and plugin data retained.' if action == 'remove' else 'Previous version restored, disabled. Review its settings before enabling.'}


def check_update(name):
    item = _state()['data']['installed'].get(_name(name))
    if not item:
        raise ValueError('Plugin not installed.')
    p = item['package']
    result = preview_bundled(name) if p['ref'] == 'bundled' else preview(p['repo'], p['ref'], p['subdir'])
    result['replace'] = p['digest']
    result['changed'] = result['digest'] != p['digest']
    return result


class PluginContext:
    """Deliberately small host API. Unknown Hermes APIs fail visibly at registration."""
    def __init__(self, name, folder, settings):
        self.name = name
        self.plugin_dir = folder
        self.data_dir = ROOT / 'data' / name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = settings
        self.logger = logging.getLogger('apex.plugin.' + name)
        self.tools, self.hooks, self.commands, self.providers = {}, {}, {}, {}

    def get_config(self, key=None, default=None):
        return copy.deepcopy(self.config) if key is None else self.config.get(key, default)

    def has_capability(self, name):
        return False

    def register_tool(self, name, schema, handler, toolset=None, **kwargs):
        _name(name)
        if kwargs:
            raise ValueError('Unsupported tool registration options: ' + ', '.join(kwargs))
        if not callable(handler) or name in self.tools:
            raise ValueError('Invalid or duplicate tool: ' + name)
        schema = copy.deepcopy(schema)
        params = schema.get('parameters', schema.get('input_schema', {'type': 'object'}))
        jsonschema.Draft202012Validator.check_schema(params)
        self.tools[name] = (dict(name=name, description=str(schema.get('description', name))[:2000], input_schema=params), handler)

    def register_hook(self, event, callback):
        if event not in HOOKS or not callable(callback):
            raise ValueError('Unsupported observer hook: ' + str(event))
        self.hooks.setdefault(event, []).append(callback)

    def register_command(self, name, handler, description='', args_hint=''):
        _name(name)
        if not callable(handler) or name in self.commands:
            raise ValueError('Invalid or duplicate command.')
        self.commands[name] = (description, handler)

    def dispatch_tool(self, name, args):
        from agent.core import _execute_tool
        return _execute_tool(name, args)

    def register_memory_provider(self, name, *, recall, remember):
        self._provider('memory', name, dict(recall=recall, remember=remember))

    def register_context_engine(self, name, *, summarize):
        self._provider('context', name, dict(summarize=summarize))

    def _provider(self, kind, name, handlers):
        _name(name)
        if not all(callable(fn) for fn in handlers.values()):
            raise ValueError('Provider handlers must be callable.')
        key = kind + ':' + name
        if key in self.providers:
            raise ValueError('Duplicate provider.')
        self.providers[key] = handlers


def _load(item):
    p = item['package']
    folder = _folder(p['id'])
    name = p['manifest']['name']
    if _digest(folder) != p['digest']:
        raise ValueError('Installed plugin files changed. Review a fresh revision before enabling.')
    issues = _issues(folder, p['manifest'])
    if issues:
        raise ValueError('; '.join(issues))
    settings = _settings(p['manifest'], item['settings'])
    key = (p['id'], json.dumps(settings, sort_keys=True))
    if _failed.get(name, (None, None))[0] == key:
        raise ValueError(_failed[name][1])
    if name in _cache and _cache[name][0] == key:
        return _cache[name][1]
    ctx = PluginContext(name, folder, settings)
    module_name = '_apex_plugin_' + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(module_name, folder / '__init__.py', submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        if not callable(getattr(module, 'register', None)):
            raise ValueError('Plugin must export register(ctx).')
        registered = module.register(ctx)
        if hasattr(registered, '__await__'):
            if hasattr(registered, 'close'):
                registered.close()
            raise ValueError('register(ctx) must be synchronous.')
        for field, actual in [('provides_tools', ctx.tools), ('provides_hooks', ctx.hooks)]:
            if set(p['manifest'].get(field, [])) - set(actual):
                raise ValueError('Plugin did not register its declared ' + field)
    except Exception as exc:
        for key_name in list(sys.modules):
            if key_name == module_name or key_name.startswith(module_name + '.'):
                sys.modules.pop(key_name, None)
        error = 'Plugin registration failed: ' + str(exc)[:500]
        _failed[name] = (key, error)
        raise ValueError(error) from exc
    _cache[name] = (key, ctx)
    _errors.pop(name, None)
    return ctx


def _active():
    out = []
    if not ROOT.exists():
        return out
    with LOCK:
        for name, item in _state()['data']['installed'].items():
            if item['enabled']:
                try:
                    out.append(_load(item))
                except Exception as exc:
                    _errors[name] = str(exc)[:500]
    return out


def _prefix(name):
    return 'plugin__' + name[:20].replace('-', '_') + '_' + hashlib.sha256(name.encode()).hexdigest()[:8] + '__'


def _export(name):
    return name[:12] + '_' + hashlib.sha256(name.encode()).hexdigest()[:8]


def _entries(ctx):
    for name, (schema, handler) in ctx.tools.items():
        yield _prefix(ctx.name) + 't_' + _export(name), schema, handler
    for name, (description, handler) in ctx.commands.items():
        schema = dict(name=name, description=f'/{ctx.name}:{name} — {description}', input_schema={'type': 'object', 'properties': {'args': {'type': 'string'}}, 'required': ['args'], 'additionalProperties': False})
        yield _prefix(ctx.name) + 'c_' + _export(name), schema, lambda args, fn=handler: fn(args['args'])
    skills = {p.parent.name: p for p in (ctx.plugin_dir / 'skills').glob('*/SKILL.md') if p.stat().st_size <= 100000}
    if skills:
        schema = dict(name='instructions', description='Read bundled skills for ' + ctx.name, input_schema={'type': 'object', 'properties': {'name': {'type': 'string', 'enum': sorted(skills)}}, 'required': ['name'], 'additionalProperties': False})
        yield _prefix(ctx.name) + 'skills', schema, lambda args: skills[args['name']].read_text(encoding='utf-8')


def definitions():
    return [dict(schema, name=name) for ctx in _active() for name, schema, handler in _entries(ctx)]


def call(name, inputs):
    for ctx in _active():
        for full, schema, handler in _entries(ctx):
            if full == name:
                try:
                    jsonschema.validate(inputs, schema['input_schema'])
                    out = handler(copy.deepcopy(inputs))
                    if hasattr(out, '__await__'):
                        import asyncio
                        out = asyncio.run(out)
                    return out if isinstance(out, str) else json.dumps(out)
                except Exception as exc:
                    _errors[ctx.name] = str(exc)[:500]
                    return '[Plugin error] ' + str(exc)[:500]
    return '[Plugin unavailable] Disabled, changed, failed to load, or unknown tool.'


def emit(event, **payload):
    if _inside_hook.get():
        return None
    for ctx in _active():
        for handler in ctx.hooks.get(event, []):
            token = _inside_hook.set(True)
            try:
                import inspect
                params = inspect.signature(handler).parameters
                selected = payload if any(p.kind == p.VAR_KEYWORD for p in params.values()) else {k:v for k,v in payload.items() if k in params}
                result = handler(**copy.deepcopy(selected))
                if inspect.isawaitable(result):
                    import asyncio
                    result = asyncio.run(result)
                # A plugin may tighten policy, never override an Apex refusal.
                if event == 'pre_tool_call' and isinstance(result, dict) and result.get('action') in ('block', 'approve'):
                    return 'Plugin ' + ctx.name + ': ' + str(result.get('message') or 'This action requires owner review.')
            except Exception as exc:
                _errors[ctx.name] = str(exc)[:500]
                LOG.warning('Plugin %s observer failed: %s', ctx.name, type(exc).__name__)
                if event == 'pre_tool_call':
                    return 'Plugin ' + ctx.name + ' pre-tool hook failed; action was not executed.'
            finally:
                _inside_hook.reset(token)
    return None


def slash(text):
    match = re.fullmatch(r'/([A-Za-z][A-Za-z0-9_-]{0,47}):([A-Za-z][A-Za-z0-9_-]{0,47})(?:\s+(.*))?', text, re.S)
    if not match:
        return None
    return _prefix(match[1]) + 'c_' + _export(match[2]), {'args': match[3] or ''}


def select_provider(kind, selected):
    if kind not in ('memory', 'context') or not isinstance(selected, str):
        raise ValueError('Unknown provider type.')
    with LOCK:
        if selected and not any(ctx.name + ':' + k.split(':', 1)[1] == selected and k.startswith(kind + ':') for ctx in _active() for k in ctx.providers):
            raise ValueError('Enable a compatible provider plugin first.')
        state = _state()
        state['data']['providers'][kind] = selected
        _save(state)
    return {'message': 'Provider selection saved for subsequent operations.'}


def provider_call(provider_kind, method, **kwargs):
    """(handled, value). Re-entry uses built-in behavior so providers can delegate."""
    if _inside_provider.get() or not ROOT.exists():
        return False, None
    selected = _state()['data']['providers'].get(provider_kind)
    if not selected:
        return False, None
    for ctx in _active():
        key = provider_kind + ':' + selected.split(':', 1)[-1]
        if selected.startswith(ctx.name + ':') and key in ctx.providers:
            token = _inside_provider.set(True)
            try:
                return True, ctx.providers[key][method](**kwargs)
            finally:
                _inside_provider.reset(token)
    raise RuntimeError('Selected plugin provider is unavailable. Select built-in in Plugins.')


def inventory():
    # Inventory does not import code. Only enabled execution/discovery loads plugins.
    data = _state()['data']
    result = []
    for name, item in data['installed'].items():
        p = item['package']
        folder = _folder(p['id'])
        issues = _issues(folder, p['manifest'])
        try:
            if _digest(folder) != p['digest']:
                issues.append('Installed files changed; activation blocked.')
        except (ValueError, OSError) as exc:
            issues.append('Installed package unavailable: ' + str(exc)[:200])
        ctx = _cache.get(name, (None, None))[1]
        result.append(dict(name=name, package=p, enabled=item['enabled'], settings=item['settings'],
                           history=len(item['history']), issues=issues, error=_errors.get(name, ''),
                           loaded=bool(ctx and item['enabled'] and not issues),
                           tools=list(ctx.tools) if ctx else [], commands=list(ctx.commands) if ctx else [],
                           hooks=list(ctx.hooks) if ctx else [],
                           providers=list(ctx.providers) if ctx else item.get('registered', {}).get('providers', [])))
    bundled = []
    for folder in sorted(BUNDLED.glob('*/plugin.yaml')):
        try:
            m = _manifest(folder.parent)
            bundled.append(dict(name=m['name'], description=m.get('description', ''), version=m.get('version', '')))
        except Exception:
            continue
    return {'plugins': result, 'selected': data['providers'], 'bundled': bundled}
