// Usage reported by coding clients. Unknown counters never become zero or quota.
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const node = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const known = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
  const count = n => known(n) ? n.toLocaleString() : 'Not reported';
  const value = (u, field) => count(u?.[field]) + (known(u?.[field]) && u.field_complete?.[field] === false ? ' (partial)' : '');
  let fetcher, session = null, selected = 'chatgpt', requestId = 0;
  function grid(rows) {
    const dl = node('dl', 'usage-grid');
    for (const [label, value] of rows) dl.append(node('dt', '', label), node('dd', '', value));
    return dl;
  }
  function metrics(u) {
    return grid([['Input (including cache)', value(u, 'input_tokens')], ['Cached input', value(u, 'cached_input_tokens')],
      ['Cache writes', value(u, 'cache_write_input_tokens')], ['Output', value(u, 'output_tokens')],
      ['Reasoning output', value(u, 'reasoning_output_tokens')], ['Total tokens', value(u, 'total_tokens')]]);
  }
  function drawSession() {
    const root = $('usage-session'); root.replaceChildren();
    if (!session) { root.append(node('p', 'fine', 'Open a session to see its reported token usage.')); return; }
    const u = session.usage || {}, last = u.last_turn || {};
    root.append(node('p', 'fine', `${session.title || 'Session'} · coding and review totals`));
    root.append(metrics(u));
    if (u.by_activity) root.append(grid([['Coding tokens', value(u.by_activity.coding, 'total_tokens')],
      ['Review tokens', value(u.by_activity.review, 'total_tokens')]]));
    if (u.unknown_turns) root.append(node('p', 'usage-warning', `Partial totals: ${u.unknown_turns} turn(s) did not report usage. Older scalar totals do not provide this breakdown.`));
    if (!known(u.total_tokens)) root.append(node('p', 'fine', 'No detailed usage reported yet. It appears when the coding client completes a turn.'));
    if (u.last_turn) {
      const details = node('details', 'usage-turn'); details.append(node('summary', '', 'Last coding turn'));
      details.append(metrics(last), grid([['Reported model', last.model || 'Not reported'],
        ['Requested model', session.model || 'Account default'], ['Requested effort', session.effort || 'Account default'],
        ['Context capacity', known(last.context_window_tokens) ? `${count(last.context_window_tokens)} tokens` : 'Not reported']]));
      if (known(last.cost_usd)) details.append(node('p', 'fine', `Provider cost estimate: $${last.cost_usd.toFixed(4)}. This is not a subscription charge.`));
      root.append(details);
    }
    if (u.last_review) {
      const details = node('details', 'usage-turn'); details.append(node('summary', '', 'Last review'));
      details.append(metrics(u.last_review), grid([['Reported model', u.last_review.model || 'Not reported']]));
      root.append(details);
    }
    root.append(node('p', 'fine', 'Current context utilization is not exposed by the exec stream.'));
  }
  function windowCard(label, w) {
    const box = node('div', 'usage-window'); box.append(node('strong', '', label));
    if (!w) { box.append(node('p', 'fine', 'Not reported')); return box; }
    if (known(w.used_percent)) {
      box.append(node('p', '', `${w.used_percent}% used · ${known(w.remaining_percent) ? w.remaining_percent : Math.max(0, 100-w.used_percent)}% remaining`));
      const bar = node('progress'); bar.max = 100; bar.value = Math.min(100, w.used_percent); bar.setAttribute('aria-label', label + ' usage'); box.append(bar);
    } else box.append(node('p', 'fine', 'Usage percentage not reported'));
    const reset = known(w.resets_at) ? new Date(w.resets_at * 1000) : null;
    box.append(node('p', 'fine', `${known(w.window_minutes) ? `${w.window_minutes}-minute window` : 'Window not reported'} · ${reset && Number.isFinite(reset.getTime()) ? `Resets ${reset.toLocaleString()}` : 'Reset time not reported'}`));
    return box;
  }
  function drawAccount(data) {
    const root = $('usage-account'); root.replaceChildren();
    if (data.error) root.append(node('p', 'usage-warning', data.error));
    if (data.limits_available && Array.isArray(data.limits)) for (const bucket of data.limits) {
      root.append(node('h4', '', `${bucket.name || bucket.id || 'Codex'}${bucket.plan ? ` · ${bucket.plan}` : ''}`));
      if (bucket.reached_type) root.append(node('p', 'usage-warning', 'Limit reached: ' + bucket.reached_type));
      root.append(windowCard('Primary window', bucket.primary), windowCard('Secondary window', bucket.secondary));
    }
    else root.append(node('p', 'fine', 'Account quota: not available. No remaining percentage can be inferred from session tokens.'));
    const a = data.activity;
    if (data.activity_available && a) {
      root.append(node('h4', '', 'Account token activity'), grid([['Lifetime tokens', count(a.lifetime_tokens)], ['Peak daily tokens', count(a.peak_daily_tokens)],
        ['Current streak', known(a.current_streak_days) ? `${a.current_streak_days} days` : 'Not reported']]));
      if (Array.isArray(a.daily_buckets) && a.daily_buckets.length) {
        const details = node('details'); details.append(node('summary', '', 'Recent daily activity'));
        details.append(grid(a.daily_buckets.slice(-7).map(b => [b.date, count(b.tokens)]))); root.append(details);
      }
    }
    if (known(data.fetched_at)) root.append(node('p', 'fine', 'Checked ' + new Date(data.fetched_at * 1000).toLocaleString()));
  }
  async function load(refresh = false) {
    const id = ++requestId, provider = $('usage-provider').value;
    $('usage-refresh').disabled = true; $('usage-account').replaceChildren(node('p', 'fine', 'Reading reported account usage…'));
    try {
      const data = await fetcher(`/api/code/usage/${provider}${refresh ? '?refresh=true' : ''}`);
      if (id === requestId) drawAccount(data);
    } catch (error) {
      if (id === requestId) $('usage-account').replaceChildren(node('p', 'usage-warning', error.message || 'Usage unavailable.'));
    } finally { if (id === requestId) $('usage-refresh').disabled = false; }
  }
  function open(provider) {
    $('usage-provider').value = provider || selected; drawSession();
    if (!$('usage-dialog').open) $('usage-dialog').showModal();
    load();
  }
  window.ApexCodeUsage = {
    init(api) {
      fetcher = api;
      $('account-usage').onclick = () => open();
      $('s-tokens').onclick = () => open(session?.engine);
      $('usage-close').onclick = () => $('usage-dialog').close();
      $('usage-provider').onchange = () => load(); $('usage-refresh').onclick = () => load(true);
    },
    select(provider) { selected = provider || 'chatgpt'; },
    render(value) {
      session = value;
      if (value) {
        const u = value.usage || {};
        $('s-tokens').textContent = known(u.total_tokens) ? `${count(u.total_tokens)} tokens${u.unknown_turns ? ' (partial)' : ''}` : 'Usage';
      }
      if ($('usage-dialog').open) drawSession();
    },
    turn(u) {
      const box = node('details', 'usage-turn'); box.append(node('summary', '', 'Token breakdown'));
      box.append(metrics(u)); return box;
    },
  };
})();
