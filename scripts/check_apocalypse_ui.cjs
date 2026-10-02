const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'../dashboard/static');
const dom=new JSDOM(fs.readFileSync(path.join(root,'apocalypse.html'),'utf8'),{url:'http://apex.test/apocalypse',runScripts:'outside-only'});
const w=dom.window,$=id=>w.document.getElementById(id),tick=()=>new Promise(r=>setTimeout(r,10));
let active=false,downloaded=false,chats=0;
w.fetch=async(url,opts)=>{assert.equal(opts.cache,'no-store');let data={};
  if(url==='/api/apocalypse/status')data={active,model:{selected:'qwen3:4b',downloaded,server_ready:true},storage:{path:'D:\\Apex-Apocalypse',free_bytes:2e12},documents:[],nomad:{ready:false,docker_available:false,revision:'a'.repeat(40)},voice:{server_ready:false}};
  else if(url==='/api/apocalypse/chat'){chats++;data={answer:'<script>local text only</script>'};}
  else throw Error('Unexpected fetch '+url);
  return {ok:true,json:async()=>data};
};
w.eval(fs.readFileSync(path.join(root,'apocalypse.js'),'utf8'));
(async()=>{
  await tick();assert.equal($('send').disabled,true);assert.equal($('nomad-open').hidden,true);
  assert.match($('mode').textContent,/NORMAL APEX/);assert.match($('nomad-status').textContent,/Docker/);
  active=downloaded=true;$('refresh').click();await tick();assert.equal($('send').disabled,false);
  $('message').value='Hello offline';$('chat').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await tick();
  assert.equal(chats,1);assert.match($('answer').textContent,/<script>/);assert.equal($('answer').querySelector('script'),null);
  for(const node of w.document.querySelectorAll('script[src],link[rel="stylesheet"]'))assert.ok((node.getAttribute('src')||node.getAttribute('href')).startsWith('/static/'));
  console.log('Apocalypse: local assets, missing model guard, Docker readiness and text-only chat rendering pass.');
})().catch(e=>{console.error(e);process.exit(1);});
