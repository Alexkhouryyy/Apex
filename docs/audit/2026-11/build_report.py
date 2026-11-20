"""Build the Apex audit report page from the subsystem inventories in this folder.

    python docs/audit/2026-11/build_report.py OUT_DIR

Writes OUT_DIR/index.html (summary, ranked problems, scorecard) and
OUT_DIR/data.json (every area's features and findings, rendered by the page).
The ranked list below is written by hand from the inventories' confirmed
high/medium findings; update its "state" as fixes land.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

AREAS = [  # key, short name: in the order an owner thinks about Apex
    ('core', 'Brain and resident mode'), ('voice', 'Voice and Celine'), ('companion', 'Screen Companion'),
    ('platform', 'Dashboard and launchers'), ('apocalypse', 'Offline library'), ('memory', 'Memory'),
    ('autonomy', 'Background awareness'), ('presence', 'Avatars and hands'), ('code', 'Apex Code'),
    ('work', 'Work, skills and MCP'), ('study', 'Study and board'), ('world', 'World view'),
    ('channels', 'Remote channels'), ('research', 'Research and computer use'), ('reverse', 'Reverse engineering'),
]
STATUS = [('working', 'Works'), ('built_untested_on_pc', 'Not tried on your PC'), ('off_by_default', 'Off until set up'),
          ('partial', 'Partly works'), ('planned_or_stub', 'Planned only'), ('broken', 'Broken')]

# Ranked by what it costs the owner today. state: open | fixed (with the PR).
PROBLEMS = [
    dict(state='fixed', pr=88, area='platform', title='Your dashboard password and push keys were in the repo',
         why='An old one-time setup script held them. It is removed, but the values stay in git history.',
         todo='Change DASHBOARD_TOKEN and make new push keys (commands in the chat).'),
    dict(state='fixed', pr=88, area='platform', title='The Windows test run on GitHub crashed halfway',
         why='A test pressed Ctrl+C in a way that kills the whole process on Windows.'),
    dict(state='fixed', pr=89, area='apocalypse', title='Adding packs could silently drop the Wikipedia download',
         why='The size limit let new small packs push out the half-done 127 GB download, then said Done.'),
    dict(state='fixed', pr=89, area='apocalypse', title='New and updated books did not show up in search',
         why='NOMAD was never asked to rescan after a download.'),
    dict(state='fixed', pr=90, area='core', title='Resident mode skips half of what Apex sets up at start',
         why='Your launcher runs resident mode, which never switches on sub-agents, never schedules the nightly '
             'reflection, weekly review or morning briefing, never runs the skill curator and never loads Python '
             'skills. One shared start-up fixes findings in five areas.'),
    dict(state='fixed', pr=90, area='voice', title='Long replies come out in the robot PC voice',
         why='A Ctrl+Space reply is sent to Celine whole, with a 20-second limit, so anything over 3-4 sentences '
             'falls back to the Windows voice. Those turns also answer as Apex, not Celine.'),
    dict(state='open', area='core', size='medium', title='Risky actions are always refused, with no way to say yes',
         why='In resident mode nothing can ask you to confirm, so running Python, skills, image generation and '
             'similar are refused even when you asked for them.'),
    dict(state='fixed', pr=90, area='autonomy', title='Everything you copy is kept forever and sent to the model',
         why='Background awareness logs the first 200 characters of every clipboard copy, passwords and API keys '
             'included, with no time limit.'),
    dict(state='open', area='research', size='medium', title='With DeepSeek, Apex controls your screen blind',
         why='Every screenshot and image is dropped before it reaches DeepSeek, so desktop control, browser control '
             'and the webcam tool work without sight. The docs say DeepSeek supports images.'),
    dict(state='fixed', pr=90, area='core', title='DeepSeek network errors skip the fallback and can hang',
         why='Only Claude errors are handled; a DeepSeek outage gives no friendly message and a turn can hang up to 10 minutes.'),
    dict(state='fixed', pr=90, area='core', title='The morning briefing crashes when it is scheduled',
         why='The time zone is passed twice, so the scheduler refuses the job. Schedules also run in UTC while '
             'Celine is told your local time.'),
    dict(state='open', area='memory', size='small', title="The Memory page's Save button always fails",
         why='The page encodes the request twice and the server rejects it. The page check hides it.'),
    dict(state='open', area='memory', size='small', title='Reflection can delete memories without asking',
         why='When it is 85% sure, it applies "forget" suggestions itself.'),
    dict(state='fixed', pr=90, area='autonomy', title='Background awareness cannot see your active window on Windows',
         why='The window watcher only knows a Linux tool, so most Guardian triggers never fire on your laptop.'),
    dict(state='open', area='voice', size='small', title='Hard to stop Celine mid-reply on Windows',
         why='The stop keys only work on Linux and the swipe-down gesture only changes the tray icon; talking over her is the only way.'),
    dict(state='open', area='apocalypse', size='small', title='The offline dashboard does not start without Ollama',
         why='The readiness page is meant to stay up, but a missing or slow Ollama stops the whole Apocalypse service.'),
    dict(state='open', area='memory', size='small', title='Meaning-based search may quietly be keyword-only',
         why='The platform launcher forces offline mode for Hugging Face, and nothing pre-downloads the embedding model.'),
    dict(state='open', area='core', size='small', title='Power cuts slowly eat the daily spending cap',
         why='Cost reservations left by a crash are never released, and the $2 "session" cap covers days of uptime.'),
    dict(state='open', area='reverse', size='medium', title='The Reverse page does not work with the real REA',
         why='It sends argument names REA rejects, its tests use a fake that hides it, and it is locked once a dashboard token is set.'),
    dict(state='open', area='world', size='small', title="World view's Ask Celine button is broken",
         why='It calls an engine action that does not exist. The full engine itself has never run.'),
    dict(state='open', area='platform', size='small', title='Without a dashboard token your own pages are locked',
         why='Home, Setup, Settings and other owner pages need the master token; setting one (step 1 above) also fixes this.'),
]


def e(text) -> str:
    return html.escape(str(text or ''), quote=True)


def load():
    areas = []
    for key, name in AREAS:
        path = HERE / f'{key}.json'
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        data.pop('files_covered', None)
        data['key'], data['name'] = key, name
        areas.append(data)
    return areas


def counts(areas):
    total = {k: 0 for k, _ in STATUS}
    for area in areas:
        for feature in area['features']:
            total[feature.get('status', 'partial')] = total.get(feature.get('status', 'partial'), 0) + 1
    return total


def severity_counts(area):
    out = {'high': 0, 'medium': 0, 'low': 0}
    for gap in area['gaps']:
        out[gap.get('severity', 'low')] = out.get(gap.get('severity', 'low'), 0) + 1
    return out


def render(areas, commit, date) -> str:
    names = dict(AREAS)
    total = counts(areas)
    n_features = sum(total.values())
    n_findings = sum(len(a['gaps']) for a in areas)
    confirmed = sum(1 for a in areas for g in a['gaps'] if g.get('confirmed'))
    mean = sum(a['rating']['score'] for a in areas) / max(1, len(areas))
    bar = ''.join(f'<span class="seg st-{k}" style="flex-grow:{total[k]}" title="{e(label)}: {total[k]}"></span>'
                  for k, label in STATUS if total[k])
    legend = ''.join(f'<li><span class="dot st-{k}"></span>{e(label)} <b>{total[k]}</b></li>' for k, label in STATUS)
    fixed = [p for p in PROBLEMS if p['state'] == 'fixed']
    open_ = [p for p in PROBLEMS if p['state'] == 'open']
    fixed_rows = ''.join(
        f'<li><span class="chip ok">Fixed · #{p["pr"]}</span><div><b>{e(p["title"])}</b><p>{e(p["why"])}'
        + (f' <strong class="todo">{e(p["todo"])}</strong>' if p.get('todo') else '') + '</p></div></li>' for p in fixed)
    open_rows = ''.join(
        f'<li><span class="rank">{i}</span><div><b>{e(p["title"])}</b><p>{e(p["why"])}</p>'
        f'<span class="meta">{e(names[p["area"]])} · {e(p["size"])} fix</span></div></li>'
        for i, p in enumerate(open_, 1))
    score_rows = ''
    for area in sorted(areas, key=lambda a: (a['rating']['score'], a['name'])):
        sev = severity_counts(area)
        c = {k: 0 for k, _ in STATUS}
        for f in area['features']:
            c[f.get('status', 'partial')] = c.get(f.get('status', 'partial'), 0) + 1
        mini = ''.join(f'<span class="seg st-{k}" style="flex-grow:{c[k]}"></span>' for k, _ in STATUS if c[k])
        score_rows += (
            f'<tr><th scope="row"><a href="#{e(area["key"])}">{e(area["name"])}</a></th>'
            f'<td class="num"><span class="score s{int(area["rating"]["score"])}">{area["rating"]["score"]:g}</span></td>'
            f'<td><div class="bar mini" aria-hidden="true">{mini}</div><span class="sub">{len(area["features"])} features</span></td>'
            f'<td class="num">{sev["high"] or "–"}</td><td class="num">{sev["medium"] or "–"}</td>'
            f'<td class="why">{e(area["rating"]["why"])}</td></tr>')
    owner = []
    for area in areas:
        for item in area.get('needs_from_owner', [])[:3]:
            owner.append(f'<li><span class="meta">{e(area["name"])}</span>{e(item)}</li>')
    missing = [name for key, name in AREAS if key not in {a['key'] for a in areas}]
    pending = (f'<p class="note">Still being inspected: {e(", ".join(missing))}.</p>' if missing else '')
    return TEMPLATE.format(
        date=e(date), commit=e(commit), n_areas=len(areas), n_features=n_features, n_findings=n_findings,
        confirmed=confirmed, mean=f'{mean:.1f}', bar=bar, legend=legend, working=total['working'],
        untried=total['built_untested_on_pc'], broken=total['broken'], fixed_rows=fixed_rows,
        open_rows=open_rows, score_rows=score_rows, owner_rows=''.join(owner), pending=pending,
        n_open=len(open_), n_fixed=len(fixed))


TEMPLATE = '''<title>Apex Inspection</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700;800&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
/* Layout: an inspection sheet. Verdict and the ranked fix list first, the scorecard next, every area's detail last. */
:root {{
  --bg: #f6f7f5; --panel: #ffffff; --ink: #18211f; --muted: #5b6662; --line: #d9dfdb; --accent: #0d6b5f;
  --ok: #2e7d4f; --info: #3a6ea5; --off: #8a8f8c; --part: #b7791f; --plan: #9a7fb8; --bad: #b8402f;
  --ok-bg: #e4f2e9; --bad-bg: #f8e5e1;
  --display: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --body: "Source Sans 3", "Segoe UI", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #121816; --panel: #1a2220; --ink: #e5ece9; --muted: #9aa8a3; --line: #2c3834; --accent: #4fb8a8;
  --ok: #5cbf86; --info: #7aa7d9; --off: #7d8682; --part: #e0a74a; --plan: #b9a2d6; --bad: #e87a68;
  --ok-bg: #1c3326; --bad-bg: #3a221e; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #121816; --panel: #1a2220; --ink: #e5ece9; --muted: #9aa8a3; --line: #2c3834; --accent: #4fb8a8;
  --ok: #5cbf86; --info: #7aa7d9; --off: #7d8682; --part: #e0a74a; --plan: #b9a2d6; --bad: #e87a68;
  --ok-bg: #1c3326; --bad-bg: #3a221e; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--ink); font: 16px/1.55 var(--body); }}
.wrap {{ max-width: 1040px; margin: 0 auto; padding-block: 32px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 40px; }}
h1, h2, h3 {{ font-family: var(--display); line-height: 1.15; text-wrap: balance; margin: 0; }}
h1 {{ font-size: clamp(2rem, 5vw, 3rem); font-weight: 800; letter-spacing: -0.02em; }}
h2 {{ font-size: 1.45rem; font-weight: 700; }}
h3 {{ font-size: 1.1rem; font-weight: 700; }}
p {{ margin: 0; max-width: 68ch; }}
a {{ color: var(--accent); }}
a:focus-visible, summary:focus-visible, button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.eyebrow {{ font: 600 .78rem/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }}
header {{ display: grid; gap: 14px; }}
.verdict {{ font-size: 1.15rem; }}
.stamp {{ display: flex; flex-wrap: wrap; gap: 8px 22px; font: 400 .85rem/1.4 var(--mono); color: var(--muted); }}
.stamp b {{ color: var(--ink); font-weight: 600; }}
.figures {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }}
.figure {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; }}
.figure b {{ display: block; font: 800 2rem/1.1 var(--display); font-variant-numeric: tabular-nums; }}
.figure span {{ color: var(--muted); font-size: .9rem; }}
.bar {{ display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: var(--line); gap: 2px; }}
.bar.mini {{ height: 8px; width: 140px; max-width: 100%; }}
.seg {{ min-width: 3px; }}
.st-working {{ background: var(--ok); }} .st-built_untested_on_pc {{ background: var(--info); }}
.st-off_by_default {{ background: var(--off); }} .st-partial {{ background: var(--part); }}
.st-planned_or_stub {{ background: var(--plan); }} .st-broken {{ background: var(--bad); }}
.legend {{ list-style: none; padding: 0; margin: 10px 0 0; display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: .9rem; color: var(--muted); }}
.legend b {{ color: var(--ink); font-variant-numeric: tabular-nums; }}
.dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }}
section {{ display: grid; gap: 16px; }}
.list {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
.list li {{ display: grid; grid-template-columns: auto 1fr; gap: 14px; align-items: start; background: var(--panel);
  border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; }}
.list li > div {{ min-width: 0; display: grid; gap: 4px; }}
.list p {{ color: var(--muted); }}
.rank {{ font: 800 1.1rem/1 var(--display); color: var(--accent); min-width: 1.6em; padding-top: 3px; font-variant-numeric: tabular-nums; }}
.chip {{ font: 600 .72rem/1 var(--mono); padding: 5px 8px; border-radius: 4px; white-space: nowrap; }}
.chip.ok {{ background: var(--ok-bg); color: var(--ok); }}
.todo {{ color: var(--bad); font-weight: 600; }}
.meta {{ font: 400 .78rem/1.3 var(--mono); color: var(--muted); }}
.tablewrap {{ overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; min-width: 720px; font-size: .95rem; }}
th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
thead th {{ font: 600 .72rem/1.2 var(--mono); letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }}
tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
td.num {{ font-variant-numeric: tabular-nums; text-align: center; }}
td.why {{ color: var(--muted); font-size: .9rem; min-width: 260px; }}
.sub {{ display: block; color: var(--muted); font-size: .8rem; margin-top: 4px; }}
.score {{ display: inline-block; min-width: 2.1em; padding: 3px 0; border-radius: 4px; font: 700 .95rem/1.2 var(--display);
  color: var(--panel); background: var(--part); }}
.score.s1, .score.s2, .score.s3, .score.s4 {{ background: var(--bad); }}
.score.s7, .score.s8, .score.s9, .score.s10 {{ background: var(--ok); }}
.owner li {{ grid-template-columns: 1fr; gap: 2px; }}
.note {{ color: var(--muted); font-style: italic; }}
details.area {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
details.area > summary {{ cursor: pointer; padding: 14px 16px; display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; list-style: none; }}
details.area > summary::-webkit-details-marker {{ display: none; }}
details.area > summary::before {{ content: "+"; font: 700 1rem/1 var(--mono); color: var(--accent); width: 1em; }}
details.area[open] > summary::before {{ content: "−"; }}
.areabody {{ padding: 0 16px 18px; display: grid; gap: 16px; min-width: 0; }}
.feat {{ display: grid; gap: 2px; padding: 10px 0; border-top: 1px solid var(--line); }}
.feat .top {{ display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; }}
.pill {{ font: 600 .7rem/1 var(--mono); padding: 4px 7px; border-radius: 4px; color: var(--panel); }}
.gap {{ border-left: 3px solid var(--off); padding: 6px 0 6px 12px; display: grid; gap: 4px; }}
.gap.high {{ border-color: var(--bad); }} .gap.medium {{ border-color: var(--part); }}
.ev {{ font: 400 .78rem/1.45 var(--mono); color: var(--muted); overflow-wrap: anywhere; }}
.small {{ font-size: .9rem; color: var(--muted); }}
@media (prefers-reduced-motion: no-preference) {{ details.area[open] .areabody {{ animation: fade .2s ease-out; }} }}
@keyframes fade {{ from {{ opacity: .4; }} to {{ opacity: 1; }} }}
</style>

<div class="wrap">
<header>
  <span class="eyebrow">Apex · full inspection · {date}</span>
  <h1>Apex Inspection</h1>
  <p class="verdict">Apex is solid inside, careful about safety and heavily tested, but the way you actually run it
  (resident mode, on Windows) gets less than the rest of the code assumes, and most of it has never run on your laptop.</p>
  <div class="stamp"><span>Code at <b>{commit}</b></span><span><b>{n_areas}</b> areas</span>
    <span><b>{n_features}</b> features</span><span><b>{n_findings}</b> findings, <b>{confirmed}</b> confirmed against the code</span>
    <span>Average score <b>{mean}</b>/10</span></div>
  {pending}
</header>

<section aria-labelledby="h-state">
  <h2 id="h-state">Every feature, by status</h2>
  <div class="figures">
    <div class="figure"><b>{working}</b><span>work today</span></div>
    <div class="figure"><b>{untried}</b><span>built, never tried on your PC</span></div>
    <div class="figure"><b>{broken}</b><span>broken</span></div>
    <div class="figure"><b>{n_open}</b><span>problems ranked below</span></div>
  </div>
  <div>
    <div class="bar" role="img" aria-label="Feature status across Apex">{bar}</div>
    <ul class="legend">{legend}</ul>
  </div>
</section>

<section aria-labelledby="h-fixed">
  <h2 id="h-fixed">Already fixed from this inspection</h2>
  <ul class="list">{fixed_rows}</ul>
</section>

<section aria-labelledby="h-open">
  <h2 id="h-open">What to fix next, in order</h2>
  <p class="small">Ranked by what it costs you today. Each was confirmed by reading the code and, where possible, running it.</p>
  <ol class="list">{open_rows}</ol>
</section>

<section aria-labelledby="h-score">
  <h2 id="h-score">Scorecard</h2>
  <div class="tablewrap"><table>
    <thead><tr><th scope="col">Area</th><th scope="col">Score</th><th scope="col">Features</th>
      <th scope="col">High</th><th scope="col">Medium</th><th scope="col">Why</th></tr></thead>
    <tbody>{score_rows}</tbody>
  </table></div>
</section>

<section aria-labelledby="h-owner">
  <h2 id="h-owner">Only you can check these</h2>
  <p class="small">Things that need your laptop, your accounts or your hands. The first few per area.</p>
  <ul class="list owner">{owner_rows}</ul>
</section>

<section aria-labelledby="h-areas">
  <h2 id="h-areas">Every area in detail</h2>
  <div id="areas" class="list"><p class="small">Loading the details…</p></div>
</section>
</div>

<script>
(function () {{
  var LABEL = {{working:"Works", built_untested_on_pc:"Not tried on your PC", off_by_default:"Off until set up",
    partial:"Partly works", planned_or_stub:"Planned only", broken:"Broken"}};
  function el(tag, cls, text) {{ var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }}
  function render(areas) {{
    var box = document.getElementById("areas"); box.replaceChildren();
    areas.forEach(function (a) {{
      var d = el("details", "area"); d.id = a.key;
      var s = el("summary"); s.append(el("h3", null, a.name), el("span", "meta", a.rating.score + "/10 · " + a.features.length + " features · " + a.gaps.length + " findings"));
      d.append(s);
      var body = el("div", "areabody");
      body.append(el("p", null, a.summary));
      var gaps = a.gaps.slice().sort(function (x, y) {{ return ["high","medium","low"].indexOf(x.severity) - ["high","medium","low"].indexOf(y.severity); }});
      if (gaps.length) {{
        body.append(el("h3", null, "Findings"));
        gaps.forEach(function (g) {{
          var b = el("div", "gap " + g.severity);
          b.append(el("span", "meta", g.severity.toUpperCase() + (g.confirmed ? " · confirmed" : " · not confirmed")));
          b.append(el("p", null, g.issue));
          if (g.fix_hint) b.append(el("p", "small", "Fix: " + g.fix_hint));
          b.append(el("span", "ev", g.evidence));
          body.append(b);
        }});
      }}
      body.append(el("h3", null, "Features"));
      a.features.forEach(function (f) {{
        var row = el("div", "feat"), top = el("div", "top");
        var pill = el("span", "pill st-" + f.status, LABEL[f.status] || f.status);
        top.append(el("b", null, f.name), pill); row.append(top, el("p", null, f.what));
        row.append(el("p", "small", "How: " + f.how_to_use + (f.setup_needed ? " · Setup: " + f.setup_needed : "")));
        row.append(el("span", "ev", f.evidence));
        body.append(row);
      }});
      d.append(body); box.append(d);
    }});
    if (location.hash) {{ var t = document.getElementById(location.hash.slice(1)); if (t && t.tagName === "DETAILS") {{ t.open = true; t.scrollIntoView(); }} }}
  }}
  fetch("data.json").then(function (r) {{ if (!r.ok) throw new Error(r.status); return r.json(); }})
    .then(render)
    .catch(function () {{ document.getElementById("areas").replaceChildren(el("p", "small", "The details file did not load. Refresh the page to try again.")); }});
  document.addEventListener("click", function (ev) {{
    var a = ev.target.closest && ev.target.closest('a[href^="#"]');
    if (!a) return; var t = document.getElementById(a.getAttribute("href").slice(1));
    if (t && t.tagName === "DETAILS") t.open = true;
  }});
}})();
</script>
'''


def main(argv):
    out = Path(argv[1] if len(argv) > 1 else '.')
    out.mkdir(parents=True, exist_ok=True)
    areas = load()
    commit = argv[2] if len(argv) > 2 else ''
    date = argv[3] if len(argv) > 3 else 'November 2026'
    (out / 'index.html').write_text(render(areas, commit, date), encoding='utf-8')
    (out / 'data.json').write_text(json.dumps(areas, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'{len(areas)} areas → {out / "index.html"}, {out / "data.json"}')


if __name__ == '__main__':
    main(sys.argv)
