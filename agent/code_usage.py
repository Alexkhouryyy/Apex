"""Provider-reported coding usage, separate from subscription quota.

No inference, credential-file reads, or private service endpoints are used by
the account snapshot. Codex's public app-server protocol is the only transport.
Missing counters stay unknown; token activity is never converted into quota.
"""
from __future__ import annotations

import copy
import math
import re
import threading
import time

from agent import work_engines as we

TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
                'uncached_input_tokens', 'reasoning_output_tokens', 'total_tokens')
_cache = {}
_lock = threading.Lock()
TTL = 60


def number(value, *, integer=True):
    """JSON numeric values only; no bools, negative values, NaN, or coercion."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value > 2**63-1 or not math.isfinite(value):
        return None
    if integer:
        return int(value) if int(value) == value else None
    return value


def _text(value, limit=100):
    return value[:limit] if isinstance(value, str) else None


def turn_usage(engine, payload=None, *, model=None, cost=None, model_usage=None):
    """Normalize final-turn counters without adding cached subsets twice.

    Codex input already includes cache reads. Claude's input excludes both
    cache reads and writes; all three counters are needed for its total input.
    Counts are cumulative across model requests in this turn, not context size.
    """
    raw = payload if isinstance(payload, dict) else {}
    direct, output = number(raw.get('input_tokens')), number(raw.get('output_tokens'))
    cached = number(raw.get('cached_input_tokens' if engine == 'chatgpt' else 'cache_read_input_tokens'))
    written = number(raw.get('cache_write_input_tokens' if engine == 'chatgpt' else 'cache_creation_input_tokens'))
    if engine == 'chatgpt':
        total_input = direct
        uncached = direct-cached if direct is not None and cached is not None and cached <= direct else None
    else:
        total_input = direct+cached+written if all(x is not None for x in (direct, cached, written)) else None
        uncached = direct
    total = total_input+output if total_input is not None and output is not None else None
    models = model_usage if isinstance(model_usage, dict) else {}
    context = None
    if len(models) == 1:
        ident, details = next(iter(models.items()))
        model = model or ident
        if isinstance(details, dict):
            context = number(details.get('contextWindow'))
    return {'scope': 'turn', 'source': 'codex-exec' if engine == 'chatgpt' else 'claude-code',
            'input_tokens': total_input, 'output_tokens': output, 'cached_input_tokens': cached,
            'cache_write_input_tokens': written, 'uncached_input_tokens': uncached,
            'reasoning_output_tokens': number(raw.get('reasoning_output_tokens')),
            'total_tokens': total, 'cost_usd': number(cost, integer=False),
            'cost_is_estimate': True, 'model': _text(model), 'context_window_tokens': context}


def session_usage(completions):
    """Aggregate final completions once, with an explicit missing-data count.

    Historical scalar totals cannot establish the input/cache/output split.
    Re-reading events computes the same result; no live cumulative update is
    added to totals. Duplicate run IDs are ignored defensively.
    """
    rows, seen = [], set()
    for row in completions:
        run_id = row.get('run_id')
        if run_id and run_id in seen:
            continue
        if run_id:
            seen.add(run_id)
        rows.append(row)
    out = _aggregate(rows)
    coding = [row for row in rows if row.get('activity', 'coding') == 'coding']
    reviews = [row for row in rows if row.get('activity') == 'review']
    out['by_activity'] = {'coding': _aggregate(coding), 'review': _aggregate(reviews)}
    out['last_activity'] = rows[-1].get('usage') if rows else None
    out['last_turn'] = coding[-1].get('usage') if coding else None
    out['last_review'] = reviews[-1].get('usage') if reviews else None
    return out


def _aggregate(rows):
    out = {'scope': 'session', 'observed_turns': 0, 'unknown_turns': 0,
           'complete': False, 'last_turn': None, 'cost_is_estimate': True}
    for field in (*TOKEN_FIELDS, 'cost_usd'):
        values = [(row.get('usage') or {}).get(field) for row in rows]
        known = [number(v, integer=field != 'cost_usd') for v in values]
        out[field] = sum(v for v in known if v is not None) if any(v is not None for v in known) else None
    out['field_complete'] = {field: bool(rows) and all(
        number((row.get('usage') or {}).get(field), integer=field != 'cost_usd') is not None for row in rows)
        for field in (*TOKEN_FIELDS, 'cost_usd')}
    for row in rows:
        u = row.get('usage') or {}
        if number(u.get('total_tokens')) is None:
            out['unknown_turns'] += 1
        else:
            out['observed_turns'] += 1
    out['complete'] = bool(rows) and out['unknown_turns'] == 0
    if rows:
        out['last_turn'] = rows[-1].get('usage')
    return out


def _window(raw):
    if not isinstance(raw, dict):
        return None
    used = number(raw.get('usedPercent'), integer=False)
    return {'used_percent': used, 'remaining_percent': max(0, 100-used) if used is not None else None,
            'window_minutes': number(raw.get('windowDurationMins')), 'resets_at': number(raw.get('resetsAt'))}


def limits(raw):
    """Allowlist a public rate-limit response; never forward account identifiers."""
    if not isinstance(raw, dict):
        return []
    buckets = raw.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict) or not buckets:
        single = raw.get('rateLimits')
        buckets = {single.get('limitId') or 'codex': single} if isinstance(single, dict) and single else {}
    rows = []
    for ident, b in list(buckets.items())[:30]:
        if not isinstance(b, dict):
            continue
        rows.append({'id': _text(b.get('limitId') or ident), 'name': _text(b.get('limitName')),
                     'plan': _text(b.get('planType')), 'primary': _window(b.get('primary')),
                     'secondary': _window(b.get('secondary')), 'reached_type': _text(b.get('rateLimitReachedType'))})
    return rows


def activity(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('summary'), dict):
        return None
    summary = raw['summary']
    out = {field: number(summary.get(key)) for field, key in (
        ('lifetime_tokens', 'lifetimeTokens'), ('peak_daily_tokens', 'peakDailyTokens'),
        ('longest_running_turn_seconds', 'longestRunningTurnSec'), ('current_streak_days', 'currentStreakDays'),
        ('longest_streak_days', 'longestStreakDays'))}
    buckets = raw.get('dailyUsageBuckets')
    out['daily_buckets'] = None
    if isinstance(buckets, list):
        out['daily_buckets'] = [{'date': b['startDate'], 'tokens': number(b.get('tokens'))} for b in buckets[:366]
                               if isinstance(b, dict) and isinstance(b.get('startDate'), str)
                               and re.fullmatch(r'\d{4}-\d{2}-\d{2}', b['startDate'])]
    return out


def discover(engine):
    """Read Codex account limits/activity using supported, read-only RPCs."""
    if engine not in ('chatgpt', 'claude'):
        raise ValueError('Choose Claude or ChatGPT.')
    out = {'engine': engine, 'source': 'unavailable', 'fetched_at': time.time(), 'available': False,
           'limits_available': False, 'activity_available': False, 'limits': [], 'activity': None, 'error': ''}
    signed = we.check(engine)
    if not signed['ok']:
        out['error'] = 'Usage unavailable: the coding client is not signed in with its subscription.'
        return out
    if engine == 'claude':
        out['error'] = 'Claude Code does not expose account quota through this read-only CLI protocol. Turn tokens are shown separately.'
        return out
    from agent.code_catalog import CatalogConnection
    connection = CatalogConnection([we.binary(engine), 'app-server'], timeout=12)
    try:
        connection.send({'id': 0, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'apex', 'version': '1.0', 'title': 'Apex Code'}}})
        if 'error' in connection.receive(lambda m: m.get('id') == 0):
            raise RuntimeError('Initialization failed')
        connection.send({'method': 'initialized', 'params': {}})
        errors = []
        for ident, method, field, normalize in (
            (1, 'account/rateLimits/read', 'limits', limits), (2, 'account/usage/read', 'activity', activity)):
            try:
                connection.send({'id': ident, 'method': method, 'params': {}})
                reply = connection.receive(lambda m: m.get('id') == ident)
                if 'error' in reply:
                    raise RuntimeError('Usage read rejected')
                out[field] = normalize(reply.get('result'))
                # An empty limits payload carries no quota evidence.
                out[field+'_available'] = bool(out[field]) if field == 'limits' else out[field] is not None
                if not out[field+'_available']:
                    errors.append('limits' if field == 'limits' else 'token activity')
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                errors.append('limits' if field == 'limits' else 'token activity')
        out['available'] = out['limits_available'] or out['activity_available']
        out['source'] = 'codex-app-server' if out['available'] else 'unavailable'
        if errors:
            out['error'] = f"Account {', '.join(errors)} unavailable from this Codex install/account; update Codex and retry."
        return out
    finally:
        connection.close()


def snapshot(engine, refresh=False):
    key = (engine, we.binary(engine))
    with _lock:
        cached = _cache.get(key)
        if cached and not refresh and time.monotonic()-cached[0] < TTL:
            return copy.deepcopy(cached[1])
    try:
        result = discover(engine)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        result = {'engine': engine, 'source': 'unavailable', 'fetched_at': time.time(), 'available': False,
                  'limits_available': False, 'activity_available': False, 'limits': [], 'activity': None,
                  'error': 'Account usage unavailable. Update the coding client and check its subscription sign-in.'}
    with _lock:
        _cache[key] = (time.monotonic(), result)
    return copy.deepcopy(result)
