// Offline checks of the production budget renderer with fixture accounting.
const {JSDOM} = require('jsdom');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const dom = new JSDOM(fs.readFileSync(path.join(root, 'dashboard/static/index.html'), 'utf8'),
  {runScripts:'outside-only', url:'http://localhost'});
const w = dom.window, $ = id => w.document.getElementById(id);
const source = fs.readFileSync(path.join(root, 'dashboard/static/app.js'), 'utf8');
// Load the actual formatter and budget function without starting unrelated polling.
w.eval(source.match(/^function fmtCost\(.*$/m)[0]);
w.eval(source.slice(source.indexOf('async function loadBudget()'),
  source.indexOf("document.getElementById('budget-enabled').addEventListener")));
let data;
w.api = async url => { assert.equal(url, '/api/budget'); return data; };
(async () => {
  assert.equal(w.fmtCost(0), '$0.0000');
  for (const unknown of [null, undefined, NaN, Infinity]) assert.equal(w.fmtCost(unknown), 'Unknown');
  data = {enabled:true, daily_usd:5, session_usd:2, today_spend:1, session_spend:0.5,
    daily_reserved:3.5, session_reserved:0.5, daily_unknown_calls:0, session_unknown_calls:0};
  await w.loadBudget();
  assert.equal($('budget-bar-daily').style.width, '90%');
  assert.equal($('budget-bar-session').style.width, '50%');
  assert.ok($('budget-bar-daily').classList.contains('over'));
  assert.match($('budget-label-daily').textContent, /3\.5000 reserved/);
  assert.equal($('budget-enabled').checked, true);
  assert.equal($('budget-daily-input').value, '5');
  data = {...data, daily_unknown_calls:1, session_unknown_calls:1, enabled:false};
  await w.loadBudget();
  assert.match($('budget-label-daily').textContent, /Unknown total.*1 calls with unknown cost/);
  assert.equal($('budget-bar-daily').parentElement.style.visibility, 'hidden');
  assert.equal($('budget-bar-session').parentElement.style.visibility, 'hidden');
  assert.equal($('budget-enabled').checked, false);
  data = {...data, today_spend:0, session_spend:0, daily_reserved:0, session_reserved:0,
    daily_unknown_calls:0, session_unknown_calls:0};
  await w.loadBudget();
  assert.equal($('budget-bar-daily').parentElement.style.visibility, '');
  assert.equal($('budget-bar-daily').style.width, '0%');
  assert.equal($('budget-bar-daily').classList.contains('over'), false);
  assert.equal($('budget-label-daily').textContent, '$0.00 / $5.00');
  console.log('PASS Budget UI: reserved allowance, unknown totals, meter recovery, settings and genuine zero cost');
  w.close();
})().catch(e => { console.error(e); w.close(); process.exitCode = 1; });
