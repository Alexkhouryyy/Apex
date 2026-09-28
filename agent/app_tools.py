"""Small discovery surface for large app/MCP catalogs; dispatch retains policy."""
import json
import re
import jsonschema
from agent import apps, mcp_client, mcp_policy, subagent_scope

DEFINITIONS = [
    {'name': 'search_connected_tools', 'description': 'Find actions in enabled Apps and MCP connections. Search before using an integration, then describe the exact tool for its inputs.',
     'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, 'required': ['query']}},
    {'name': 'describe_connected_tool', 'description': 'Read the exact input schema of an enabled app/MCP tool returned by search_connected_tools.',
     'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}},
    {'name': 'call_connected_tool', 'description': 'Execute a discovered app/MCP tool with schema-valid arguments. Apex checks permissions on the underlying action and may require approval. Never automatically retry a timed-out write.',
     'input_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'arguments': {'type': 'object'}}, 'required': ['name', 'arguments']}},
]


def available(*, local_only=False):
    off = set(mcp_policy.servers_off())
    local = [t for t in mcp_client.get_definitions() if mcp_policy.split_name(t['name'])[0] not in off]
    remote = [] if local_only else [t for t in apps.tools() if 'apps_' + t['toolkit'] not in off]
    return local + remote


def dispatch(operation, inputs):
    if operation == 'search_connected_tools':
        words = re.findall(r'\w+', str(inputs.get('query', '')).lower())
        limit = max(1, min(20, int(inputs.get('limit', 8))))
        results = []
        warning = None
        try:
            candidates = available()
        except apps.AppError as exc:
            candidates, warning = available(local_only=True), str(exc)
        for t in candidates:
            name, description = t['name'], t['description']
            score = sum(4 * (w in name.lower()) + (w in description.lower()) for w in words)
            if score or not words: results.append((score, name, description))
        results.sort(key=lambda t: (-t[0], t[1]))
        return json.dumps({'tools': [{'name': n, 'description': d[:400]} for _, n, d in results[:limit]],
                           'matches': len(results), 'warning': warning, 'hint': 'Connect more services in /apps if needed.'})
    name = inputs.get('name')
    tool = next((t for t in available(local_only=isinstance(name, str) and name.startswith('mcp__')) if t['name'] == name), None)
    if not tool: return 'Tool unavailable. Connect and enable its app in /apps, then search again.'
    if operation == 'describe_connected_tool':
        return json.dumps({k: tool[k] for k in ('name', 'description', 'input_schema')})
    if operation != 'call_connected_tool': raise ValueError('Unknown connection operation.')
    arguments = inputs.get('arguments')
    if not isinstance(arguments, dict): raise ValueError('Arguments must be an object.')
    jsonschema.validate(arguments, tool['input_schema'])
    if name.startswith('app__'): return apps.call(name, arguments)
    if blocked := subagent_scope.check(name): return blocked
    if name.startswith(('mcp__hass', 'mcp__homeassistant', 'mcp__home-assistant')):
        from agent.iot import is_enabled
        if not is_enabled(): return 'IoT is disabled. Enable it in Control first.'
    return mcp_client.call(name, arguments)
