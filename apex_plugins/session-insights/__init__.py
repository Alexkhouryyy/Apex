"""A useful opt-in example of Apex tools, commands, settings and lifecycle hooks."""
import json
import threading
from collections import Counter, deque


def register(ctx):
    limit = ctx.get_config('recent_limit', 100)
    if not 1 <= limit <= 1000:
        raise ValueError('recent_limit must be between 1 and 1000.')
    recent, counts, lock = deque(maxlen=limit), Counter(), threading.Lock()

    def observe(tool_name, duration_ms=0, **kwargs):
        with lock:
            counts[tool_name] += 1
            recent.append({'tool': tool_name, 'duration_ms': duration_ms})

    def usage(args):
        with lock:
            return json.dumps({'total_calls': sum(counts.values()), 'by_tool': dict(counts), 'recent': list(recent)})

    ctx.register_hook('post_tool_call', observe)
    ctx.register_tool(name='usage', handler=usage, schema={
        'name': 'usage', 'description': 'Show current-process tool counts and recent latency. No arguments or result contents are stored.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}})
    ctx.register_command('status', lambda args: usage({}), 'Show tool counts and latency')
