// Contract tests of the actual usage renderer; fixture account values are not live measurements.
const {JSDOM} = require('jsdom');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const dom = new JSDOM(fs.readFileSync(path.join(root, 'dashboard/static/code.html'), 'utf8'), {runScripts:'outside-only', url:'http://localhost/code'});
const w = dom.window, $ = id => w.document.getElementById(id);
w.HTMLDialogElement.prototype.showModal = function() {this.open=true;};
w.HTMLDialogElement.prototype.close = function() {this.open=false;};
w.eval(fs.readFileSync(path.join(root, 'dashboard/static/code_usage.js'), 'utf8'));
const calls = [];
let resolveOld;
const unknown = {available:false, limits_available:false, activity_available:false, limits:[], activity:null, error:'This client does not report quota.'};
w.ApexCodeUsage.init(async url => {
  calls.push(url);
  if (url.includes('/claude')) return unknown;
  return {available:true,limits_available:true,activity_available:true,fetched_at:1800000000,
    limits:[{id:'codex',name:'<script>bad()</script>', primary:{used_percent:0,remaining_percent:100,window_minutes:300,resets_at:1800003600},secondary:null}],
    activity:{lifetime_tokens:0,peak_daily_tokens:100,current_streak_days:null,daily_buckets:[{date:'2026-10-09',tokens:0}]}};
});
const tick = () => new Promise(r => setTimeout(r, 0));
(async () => {
  assert.equal(calls.length,0,'account reads do not run until asked');
  w.ApexCodeUsage.render({title:'Demo',engine:'chatgpt',tokens:999999,model:'requested-model',effort:'high',usage:{
    input_tokens:1000,output_tokens:50,cached_input_tokens:800,cache_write_input_tokens:null,total_tokens:1050,
    unknown_turns:1,field_complete:{cached_input_tokens:false},by_activity:{coding:{total_tokens:1000},review:{total_tokens:50}},
    last_review:{total_tokens:50,model:'review-model'},last_turn:{input_tokens:1000,output_tokens:50,total_tokens:1050,model:'reported-model',cost_usd:0}}});
  assert.match($('s-tokens').textContent,/1,050 tokens \(partial\)/,'structured totals take priority over historical scalars');
  $('s-tokens').click(); await tick();
  assert.equal($('usage-dialog').open,true);
  assert.match($('usage-session').textContent,/800 \(partial\)/,'incomplete field totals are identified');
  assert.match($('usage-session').textContent,/reported-model/);
  assert.match($('usage-session').textContent,/requested-model/);
  assert.match($('usage-session').textContent,/Review tokens50/);
  assert.match($('usage-session').textContent,/review-model/);
  assert.match($('usage-session').textContent,/not a subscription charge/);
  assert.match($('usage-account').textContent,/0% used · 100% remaining/,'reported zero is a valid measurement');
  assert.equal($('usage-account').querySelectorAll('script').length,0,'provider labels remain text');
  assert.equal($('usage-account').querySelector('progress').value,0);
  $('usage-refresh').click(); await tick(); assert.match(calls.at(-1),/refresh=true$/);
  $('usage-provider').value='claude'; $('usage-provider').dispatchEvent(new w.Event('change')); await tick();
  assert.match($('usage-account').textContent,/not available/);
  assert.equal($('usage-account').querySelectorAll('progress').length,0,'unknown quota gets no fake zero meter');
  w.ApexCodeUsage.render({title:'Legacy',engine:'claude',tokens:55});
  assert.equal($('s-tokens').textContent,'Usage'); assert.match($('usage-session').textContent,/No detailed usage reported yet/);
  w.ApexCodeUsage.render(null); assert.match($('usage-session').textContent,/Open a session/);
  w.ApexCodeUsage.init(url => url.includes('/claude') ? Promise.resolve(unknown) : new Promise(r=>resolveOld=r));
  $('usage-provider').value='chatgpt'; $('usage-provider').dispatchEvent(new w.Event('change'));
  $('usage-provider').value='claude'; $('usage-provider').dispatchEvent(new w.Event('change')); await tick();
  resolveOld({limits_available:true,limits:[{id:'stale',primary:{used_percent:80,remaining_percent:20}}]}); await tick();
  assert.doesNotMatch($('usage-account').textContent,/stale|80%/,'older provider response cannot replace current provider');
  w.ApexCodeUsage.init(async()=>{throw new Error('Connection unavailable');});
  $('usage-refresh').click(); await tick(); assert.match($('usage-account').textContent,/Connection unavailable/);
  assert.equal($('usage-refresh').disabled,false);
  console.log('PASS Code usage UI: totals, partial data, quota, refresh, provider races, unknowns, safe labels');
  w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1;});
