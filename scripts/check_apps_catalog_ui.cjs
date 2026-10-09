// Catalog responses may skip unusable provider entries; valid apps stay usable.
const {JSDOM} = require('jsdom');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const dom = new JSDOM(fs.readFileSync(path.join(root, 'dashboard/static/apps.html'), 'utf8'),
  {url:'http://localhost/apps', runScripts:'outside-only'});
const w = dom.window, $ = id => w.document.getElementById(id), requests = [];
let mode = 'mixed';
w.fetch = async url => {
  requests.push(url);
  let data;
  if(url==='/api/apps/status') data={configured:true,connections:[]};
  else if(url==='/api/apps/categories') data={items:[]};
  else if(url.startsWith('/api/apps/catalog?')) {
    const cursor=new URL(url,'http://localhost').searchParams.get('cursor');
    data=mode==='mixed' ? {items:[{slug:'github',name:'GitHub',categories:[]}],skipped_items:1,next_cursor:'',total_items:2}
      : cursor ? {items:[{slug:'gmail',name:'Gmail',categories:[]}],skipped_items:0,next_cursor:''}
      : {items:[],skipped_items:2,next_cursor:'page-2'};
  } else if(url==='/api/apps/local') data={items:[],runtime:{servers:[],tool_count:0,detail:''}};
  else throw new Error('Unexpected request '+url);
  return {ok:true,status:200,json:async()=>data};
};
w.eval(fs.readFileSync(path.join(root,'dashboard/static/apps.js'),'utf8'));
const tick=()=>new Promise(resolve=>setTimeout(resolve,20));
(async()=>{
  await tick();
  assert.equal($('results').querySelector('.card h3').textContent,'GitHub');
  assert.equal($('results').querySelector('.card button').textContent,'Connect');
  assert.equal($('catalog-warning').hidden,false);
  assert.match($('catalog-warning').textContent,/1 app listing/);
  assert.doesNotMatch($('results').textContent,/Catalog unavailable/);
  mode='empty';$('search-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.match($('results').textContent,/No apps available on this page/);
  assert.equal($('more').hidden,false,'an unsupported page still offers its next page');
  $('more').click();await tick();
  assert.ok(requests.some(url=>url.includes('cursor=page-2')));
  assert.equal($('results').querySelector('.card h3').textContent,'Gmail');
  assert.equal($('results').querySelector('.empty'),null,'loaded apps replace the empty-page explanation');
  assert.match($('catalog-warning').textContent,/2 app listings/);
  w.document.querySelector('[data-view=local]').click();await tick();
  assert.equal($('catalog-warning').hidden,true,'catalog warnings do not follow into Local MCP');
  console.log('PASS Apps catalog: valid cards, skipped entries, empty-page pagination and view reset');
  w.close();
})().catch(error=>{console.error(error);w.close();process.exitCode=1;});
