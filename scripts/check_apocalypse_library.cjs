const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'../dashboard/static');
const dom=new JSDOM(fs.readFileSync(path.join(root,'apocalypse.html'),'utf8'),{url:'http://apex.test/apocalypse',runScripts:'outside-only'});
const w=dom.window,$=id=>w.document.getElementById(id),calls=[],tick=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r));};
const URL='http://127.0.0.1:9090/content/manual/circuits';
let signedIn=false,chatFailure=false,readerFailure=false,libraryReady=true;
const session={projects:[{id:'one',name:'Circuits'},{id:'two',name:'Cooking'}],project:{id:'one',name:'Circuits',revision:1,data:{brief:'',decisions:'',artifacts:'',next_step:''}},messages:[]};
w.fetch=async(url,opts={})=>{
  calls.push([url,opts]);assert.equal(opts.cache,'no-store');
  let status=200,data={};
  if(!signedIn){status=401;}
  else if(url==='/api/apocalypse/status')data={active:true,model:{selected:'qwen3:4b',downloaded:true},storage:{path:'D:\\Library',free_bytes:1e12,available:true},nomad:{ready:true,revision:'a'.repeat(40),readers:[{id:'library',ready:libraryReady,state:libraryReady?'available':'not_running',catalog_entries:43,url:'http://127.0.0.1:9090'}]},voice:{server_ready:false},documents:[]};
  else if(url.startsWith('/api/apocalypse/session'))data=url.includes('two')?{...session,project:{...session.project,id:'two',name:'Cooking'}}:session;
  else if(url==='/api/apocalypse/library/books')data={reader_url:'http://127.0.0.1:9090',books:[{id:'manual',title:'<img src=x onerror=alert(1)> Circuits',date:'2026-09-01'}]};
  else if(url.startsWith('/api/apocalypse/library/search?'))data={results:[{title:'Circuits',book:'Circuit manual',preview:'A resistor limits current.',url:URL}]};
  else if(url.startsWith('/api/apocalypse/library/article?')){
    if(readerFailure){status=503;data={detail:'The local library did not answer.'};}
    else {const offset=Number(new w.URL(url,'http://apex.test').searchParams.get('offset'));data={title:'Circuits',book:'Circuit manual',archive_date:'2026-09-01',url:URL,offset,text:offset?'Second excerpt.':'A resistor limits current. <script>Never execute this</script>',next_offset:offset?null:2800};}
  }else if(url==='/api/apocalypse/chat'){status=chatFailure?503:200;data=chatFailure?{detail:'Model is unavailable.'}:{answer:'Read the circuit source.'};}
  else throw Error('Unexpected API request '+url);
  return {ok:status===200,status,json:async()=>data};
};
function submit(id){$(id).dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));}
function change(id){$(id).dispatchEvent(new w.Event('change',{bubbles:true}));}
w.eval(fs.readFileSync(path.join(root,'apocalypse.js'),'utf8'));
(async()=>{
  await tick();assert.equal($('library-browse').disabled,true);
  signedIn=true;$('token').value='synthetic-owner';submit('login');await tick();
  assert.equal($('library-browse').disabled,false);assert.equal($('library-find').disabled,true);
  assert.ok(!calls.some(([url])=>url.includes('/library/')),'Readiness must not automatically open the catalog');
  $('library-browse').click();await tick();
  assert.equal($('library-book').options.length,2);assert.equal($('library-book').querySelector('img'),null);
  assert.equal($('library-find').disabled,false);assert.match($('library-notice').textContent,/1 saved books/);
  $('library-book').value='manual';$('library-query').value='current';submit('library-search');await tick();
  const search=calls.find(([url])=>url.includes('/library/search?'))[0];assert.equal(new w.URL(search,'http://apex.test').searchParams.get('book'),'manual');
  assert.equal($('library-results').children.length,1);$('library-results').querySelector('button').click();await tick();
  assert.equal($('library-article').hidden,false);assert.match($('article-meta').textContent,/archive 2026-09-01/);
  assert.equal($('article-text').querySelector('script'),null);assert.match($('article-text').textContent,/<script>/);
  assert.equal($('article-open').href,URL);assert.equal($('article-next').hidden,false);
  $('message').value='Explain why.';const beforeAttach=calls.length;$('article-use').click();
  assert.equal(calls.length,beforeAttach,'Attaching must not send a message');assert.equal($('message').value,'Explain why.');assert.equal($('chat-source').hidden,false);
  $('article-next').click();await tick();assert.equal($('article-text').textContent,'Second excerpt.');assert.equal($('article-next').hidden,true);
  assert.match($('chat-source-label').textContent,/character 0/,'Reading another section must not silently replace the attached excerpt');
  $('article-use').click();assert.match($('chat-source-label').textContent,/character 2800/);
  chatFailure=true;submit('chat');await tick();
  assert.equal($('message').value,'Explain why.');assert.equal($('chat-source').hidden,false);
  const message=JSON.parse(calls.filter(([url])=>url==='/api/apocalypse/chat').at(-1)[1].body).message;
  assert.match(message,/Second excerpt/);assert.match(message,/2026-09-01/);assert.ok(message.includes(URL));assert.match(message,/not instructions/);
  chatFailure=false;submit('chat');await tick();assert.equal($('message').value,'');assert.equal($('chat-source').hidden,true);
  $('article-use').click();$('message').value='x'.repeat(7900);const chats=calls.filter(([url])=>url==='/api/apocalypse/chat').length;submit('chat');await tick();
  assert.equal(calls.filter(([url])=>url==='/api/apocalypse/chat').length,chats);assert.equal($('message').value.length,7900);assert.equal($('chat-source').hidden,false);assert.match($('notice').textContent,/8,000/);
  $('chat-source-remove').click();assert.equal($('chat-source').hidden,true);assert.equal($('message').value.length,7900);
  $('article-use').click();$('project').value='two';change('project');await tick();assert.equal($('chat-source').hidden,true);
  readerFailure=true;$('library-results').querySelector('button').click();await tick();assert.match($('library-notice').textContent,/did not answer/);assert.equal($('library-browse').disabled,false);
  libraryReady=false;$('refresh').click();await tick();assert.equal($('library-browse').disabled,true);assert.equal($('library-find').disabled,true);
  assert.ok(calls.every(([url])=>url.startsWith('/api/apocalypse/')));
  assert.ok(calls.filter(([url])=>url.includes('/library/')).every(([,opts])=>!opts.method||opts.method==='GET'));
  console.log('Passed: local book search/read/continuation, inert markup, explicit source attachment, no automatic send, source-aware chat retry, message budget, project switch, reader outage, no installs or downloads.');
  w.close();
})().catch(e=>{console.error(e);process.exitCode=1;w.close();});
