import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const code=fs.readFileSync(process.argv[2]||new URL('../dashboard/static/apocalypse.js',import.meta.url),'utf8');
const html=fs.readFileSync(new URL('../dashboard/static/apocalypse.html',import.meta.url),'utf8');
const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]));
const settle=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
function setup(){
 const nodes=new Map(),store=new Map(),calls=[],queue=[];let confirmations=0;
 function node(id){if(!nodes.has(id))nodes.set(id,{hidden:['login','content'].includes(id),disabled:false,textContent:'',value:'',children:[],files:[],
  addEventListener(event,fn){this[event]=fn;},replaceChildren(){this.children=[];},append(...rows){this.children.push(...rows);},focus(){this.focused=true;},click(){}});return nodes.get(id);}
 const context={document:{getElementById:id=>ids.has(id)?node(id):null,createElement:type=>({...node('temp-'+nodes.size),type,children:[]})},localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},
 fetch:async(url,options)=>{calls.push([url,options]);const next=queue.shift();if(!next)throw Error('Missing fixture response for '+url);if(next instanceof Error)throw next;return {status:next.status,ok:next.status===200,json:async()=>next.body};},
 confirm:()=>{confirmations++;return false;},setTimeout,URL,Error,console};
 const response=(status,body)=>queue.push({status,body});
 response(401,{});vm.runInNewContext(code,context);
 return {node,store,calls,queue,response,get confirmations(){return confirmations;}};
}
const state={active:true,model:{downloaded:true,selected:'qwen3:4b'},storage:{path:'D:\\Library',free_bytes:1e10},nomad:{ready:false,docker_available:true,revision:'a'.repeat(40)},voice:{server_ready:false},preparation:{exists:false},documents:[{name:'source.md'}]};
const session=(name='First',id='default',revision=1)=>({projects:[{id:'default',name:'First'},{id:'two',name:'Second'}],project:{id,name,revision,data:{brief:name+' goal',decisions:'Decision',artifacts:'report.md',next_step:'Inspect wiring'}},messages:[{role:'user',text:'Create a report'},{role:'agent',text:'Saved a checked report.'}],thread_id:1});
const h=setup();await settle();
assert.equal(h.node('login').hidden,false);assert.equal(h.node('send').disabled,true);assert.equal(h.calls[0][1].headers.Authorization,undefined);
h.node('token').value='synthetic-owner';h.response(200,state);h.response(200,session());h.node('login').submit({preventDefault(){}});await settle();
assert.equal(h.node('token').value,'');assert.equal(h.node('send').disabled,false);assert.equal(h.node('brief').value,'First goal');assert.equal(h.node('history').children.length,2);
h.node('brief').value='UNSAVED DRAFT';h.node('brief').input();h.node('project').value='two';h.node('project').change();await settle();
assert.equal(h.confirmations,1);assert.equal(h.node('project').value,'default');assert.equal(h.node('brief').value,'UNSAVED DRAFT');
h.response(409,{detail:'This document changed. Reload before saving; your draft is preserved.'});h.node('handoff').submit({preventDefault(){}});await settle();
assert.match(h.node('notice').textContent,/document changed/);assert.equal(h.node('brief').value,'UNSAVED DRAFT');
h.node('message').value='Continue';h.response(200,{answer:'Continued',saved:true});h.response(200,session('First','default',2));h.node('chat').submit({preventDefault(){}});await settle();
const chat=h.calls.find(([url])=>url==='/api/apocalypse/chat');assert.equal(JSON.parse(chat[1].body).workspace_id,'default');assert.equal(h.node('message').value,'');assert.equal(h.node('brief').value,'UNSAVED DRAFT');
h.response(200,{revision:3});h.node('handoff').submit({preventDefault(){}});await settle();
assert.equal(JSON.parse(h.calls.at(-1)[1].body).revision,1);assert.equal(h.node('notice').textContent,'Project handoff saved.');
h.node('project').value='two';h.response(200,session('Second','two'));h.node('project').change();await settle();assert.equal(h.node('brief').value,'Second goal');
h.response(401,{});h.node('refresh').click();await settle();assert.equal(h.node('content').hidden,true);assert.equal(h.node('send').disabled,true);assert.equal(h.node('refresh').disabled,false);
console.log('Passed: owner sign-in, saved history, project selection, draft preservation, save conflict, pinned chat request, expired-session gating.');

const r=setup();await settle();r.node('token').value='synthetic-owner';
const readerState={...state,storage:{...state.storage,available:false},nomad:{...state.nomad,ready:true,readers:[
 {id:'library',label:'Information library',state:'available',ready:true,catalog_entries:21,url:'http://127.0.0.1:9090'},
 {id:'courses',label:'Courses',state:'app_available',ready:true,url:'http://127.0.0.1:8200'},
 {id:'notes',label:'Notes',state:'not_running',ready:false,url:'http://127.0.0.1:8100'},
 {id:'tools',label:'Data tools',state:'app_available',ready:true,url:'https://example.com'},
]}};
r.response(200,readerState);r.response(200,session());r.node('login').submit({preventDefault(){}});await settle();
const rows=r.node('reader-statuses').children;
assert.equal(rows.length,4);
assert.match(rows[0].textContent,/21 titles listed/);assert.equal(rows[0].children[0].href,'http://127.0.0.1:9090');
assert.match(rows[1].textContent,/Imported courses still need checking/);
assert.match(rows[2].textContent,/not running/);assert.equal(rows[2].children.length,0);
assert.equal(rows[3].children.length,0);assert.equal(r.node('upload').disabled,true);
assert.match(r.node('free').textContent,/Connect your library drive/);
r.response(200,{...readerState,storage:{...state.storage,available:true},nomad:{...readerState.nomad,readers:[
 {id:'library',label:'Information library',state:'empty',ready:true,catalog_entries:0,url:'http://127.0.0.1:9090'},
]}});r.response(200,session());r.node('refresh').click();await settle();
assert.match(r.node('reader-statuses').children[0].textContent,/no titles are listed/);
assert.equal(r.node('upload').disabled,false);
assert.ok(r.calls.every(([url,options])=>url.startsWith('/api/apocalypse/')&&(!options.method||options.method==='GET')));
console.log('Passed: reader catalog, remapped local link, course import distinction, unavailable drive, empty library, no remote links or setup/download requests.');

// Saved model text must remain inert; only the configured local reader's
// content citations become anchors. Never render model output as HTML.
const cited=setup();await settle();cited.node('token').value='synthetic-owner';
const sourceURL='http://127.0.0.1:9090/content/devdocs_en_python_2026-08/howto/sorting';
const citedSession=session();citedSession.messages=[{role:'agent',text:
 '<img src=x onerror=alert(1)> [Sorting]('+sourceURL+') [Remote](https://example.com) '+
 '[Other reader](http://127.0.0.1:8090/content/book/page) [Settings](http://127.0.0.1:9090/api/settings)'}];
cited.response(200,readerState);cited.response(200,citedSession);cited.node('login').submit({preventDefault(){}});await settle();
const paragraph=cited.node('history').children[0].children[1];
const citations=paragraph.children.filter(child=>child.type==='a');
assert.equal(citations.length,1);assert.equal(citations[0].href,sourceURL);assert.equal(citations[0].rel,'noopener noreferrer');
assert.ok(paragraph.children.some(child=>child.textContent.includes('<img src=x onerror=alert(1)>')));
assert.ok(paragraph.children.some(child=>child.textContent.includes('[Remote]')));
assert.ok(paragraph.children.every(child=>['span','a'].includes(child.type)));
console.log('Passed: local source citations, owner-remapped reader, inert model markup, external and non-content links stay plain text.');
