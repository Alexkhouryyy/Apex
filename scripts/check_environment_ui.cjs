const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dir=path.join(__dirname,'..','dashboard','static');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE||undefined});
 try{
  const ctx=await browser.newContext({viewport:{width:1440,height:1050},serviceWorkers:'block'});
  let installed=false,enabled=true;const requests=[];
  const repo={id:'review',repo:'example/capabilities',revision:'a'.repeat(40),skills:['skills/writer'],skill_count:1,manifests:['package.json'],readme:'Reference documentation <script>window.injected=true</script>',note:'Indexed, not installed.'};
  await ctx.route('**/*',async route=>{
   const u=new URL(route.request().url());if(u.hostname!=='apex.test')return route.abort();
   const p=u.pathname;let result={};
   if(p.startsWith('/api/')){
    const data=route.request().postDataJSON();requests.push({p,data});
    if(p==='/api/status')result={model:'test-model',tools_count:42,uptime_s:120};
    if(p==='/api/environment')result={repositories:[repo],procedures:installed?[{name:'writer',available:enabled,imported:true,metadata:{enabled,repo:repo.repo,revision:repo.revision}}]:[],executable:[{name:'formatter',description:'Format text'}],forged:[],failures:[]};
    if(p==='/api/control/mcp')result={servers:[{server:'github',state:'connected',enabled:true,tools:4}],policy:{mode:'ask'},audit:{}};
    if(p==='/api/control/settings')result={settings:[{key:'SLACK_BOT_TOKEN',secret:true,set:false,display:'not set',editable:true}],restart:{ok:true},env_file:'.env'};
    if(p==='/api/control/update')result={can_update:true,state:'ready',detail:'On main. Working tree clean.'};
    if(p==='/api/environment/repositories')result=repo;
    if(p==='/api/environment/preview')result={...repo,id:'pinned-preview',path:'skills/writer',files:{'SKILL.md':'---\nname: writer\n---\nReusable instructions <script>window.injected=true</script>'},unsupported:[],license:'MIT',review_help:'Review compatibility.'};
    if(p==='/api/home/skills/install'){assert.equal(data.id,'pinned-preview');installed=true;result={name:data.name}}
    if(p==='/api/home/skills/enabled'){enabled=data.enabled;result={enabled,available:enabled}}
    if(p==='/api/environment/develop')result={result:'Skill staged for approval. No code installed.'};
    if(p==='/api/environment/skills/formatter')result={content:'def run(inputs): return inputs'};
    if(p==='/api/models')result={current:'test-model',models:[{model:'test-model',provider:'test',available:true}]};
    if(p==='/api/devices')result={devices:[]};
    if(p==='/api/auth/tokens')result={tokens:[]};
    return route.fulfill({json:result});
   }
   const file=path.join(dir,p==='/'?'index.html':p.replace(/^\/static\//,''));
   if(!fs.existsSync(file)||!fs.statSync(file).isFile())return route.fulfill({status:404,body:''});
   let body=fs.readFileSync(file);const ext=path.extname(file);
   return route.fulfill({body,contentType:({'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml'})[ext]||'application/octet-stream'});
  });
  const page=await ctx.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://apex.test/?tab=repositories');
  await page.locator('#tab-repositories.active').waitFor();
  const missing=await page.locator('.nav-btn').evaluateAll(nodes=>nodes.filter(n=>!document.getElementById('tab-'+n.dataset.tab)).map(n=>n.dataset.tab));assert.deepEqual(missing,[]);
  assert.equal(await page.locator('#env-pairing #devices-panel').count(),1);assert.equal(await page.locator('#env-pairing #access-panel').count(),1);
  await page.locator('#repo-intake input[name=source]').fill('https://github.com/example/capabilities');
  await page.locator('#repo-intake button').click();await page.locator('.repo-skill').waitFor();
  await page.locator('.repo-skill').click();await page.locator('#repo-install').waitFor();
  await page.locator('#repo-install textarea').fill('Uses Apex text tools on Windows; no extra runtime dependencies.');
  await page.locator('#repo-install button').click();await page.waitForFunction(()=>document.getElementById('repo-status').textContent.startsWith('Installed'));
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  await page.locator('.nav-btn[data-tab=plugins]').click();await page.locator('[data-skill-toggle]').waitFor();await page.locator('[data-skill-toggle]').click();
  await page.waitForFunction(()=>document.querySelector('[data-skill-toggle]')?.textContent==='Enable');assert.equal(enabled,false);
  await page.locator('.nav-btn[data-tab=skills]').click();await page.locator('#skill-existing option[value=formatter]').waitFor({state:'attached'});
  await page.locator('#skill-develop textarea').fill('Improve the formatter to handle empty strings.');await page.locator('#skill-existing').selectOption('formatter');await page.locator('#skill-develop button').click();
  await page.waitForFunction(()=>document.getElementById('skill-status').textContent.includes('staged'));
  assert(requests.some(x=>x.p==='/api/environment/develop'&&x.data.existing==='formatter'));
  for(const tab of ['system','mcp','models','channels','pairing','documentation']){
   await page.locator('.nav-btn[data-tab='+tab+']').click();await page.locator('#tab-'+tab+'.active').waitFor();
  }
  await page.locator('.nav-btn[data-tab=repositories]').click();
  await page.waitForTimeout(300);
  if(process.env.APEX_ENV_PREVIEW)await page.screenshot({path:process.env.APEX_ENV_PREVIEW});
  // WebGL/CDN features are unrelated to the isolated management workflow.
  assert.deepEqual(errors.filter(e=>!(/WebSocket|THREE|Chart|Globe|Failed to fetch dynamically/.test(e))),[]);
  await page.setViewportSize({width:390,height:844});
  assert(await page.locator('#main-content').evaluate(el=>el.scrollWidth<=el.clientWidth+3),'management content fits mobile width');
  console.log('PASS: navigation, repository preview/install, escaped upstream text, plugin toggle, skill improvement, system/MCP/models/channels/pairing and mobile layout');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
