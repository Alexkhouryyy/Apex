// Run with Node and Playwright installed. CHROMIUM_EXECUTABLE optionally selects a local browser.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dir=path.join(__dirname,'..','dashboard','static');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE || undefined});
 try {
  const ctx=await browser.newContext({viewport:{width:1100,height:780}});
  await ctx.route('http://apex.test/**',async route=>{
   const pathname=new URL(route.request().url()).pathname;
   const file=path.join(dir,pathname.startsWith('/static/')?pathname.slice(8):pathname.slice(1));
   if(!fs.existsSync(file)||!fs.statSync(file).isFile())return route.fulfill({status:404,body:''});
   const ext=path.extname(file);let body=fs.readFileSync(file);
   // Exercise the real page headers/CSS without starting agents or API calls.
   if(ext==='.html')body=body.toString().replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,m=>m.includes('src="/static/theme.js"')?m:'');
   return route.fulfill({body,contentType:({'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml'})[ext]||'application/octet-stream'});
  });
  const a=await ctx.newPage(),b=await ctx.newPage();
  await a.goto('http://apex.test/index.html');await b.goto('http://apex.test/home.html');
  const tokens={midnight:'#66ccff',cyberpunk:'#39ff9e',daylight:'#2563eb',ember:'#ff9f5a',ice:'#7dd3fc'};
  // Call the dashboard's actual theme-selection function.
  const app=fs.readFileSync(path.join(dir,'app.js'),'utf8');
  const apply=app.match(/function applyTheme\(id\) \{[\s\S]*?\n\}/)[0];
  await a.addScriptTag({content:'const THEMES='+JSON.stringify(Object.keys(tokens).map(id=>({id})))+';'+apply});
  for(const [id,color]of Object.entries(tokens)){
   await a.evaluate(id=>applyTheme(id),id);
   await b.waitForFunction(id=>document.documentElement.dataset.theme===id,id);
   for(const page of [a,b]){
    const style=await page.locator('.apex-logo').first().evaluate(el=>({
     color:getComputedStyle(el).getPropertyValue('--apex-brand-start').trim(),
     mask:getComputedStyle(el,'::before').maskImage,gradient:getComputedStyle(el,'::before').backgroundImage,
     box:el.getBoundingClientRect().toJSON(),glow:getComputedStyle(el).boxShadow
    }));
    assert.equal(style.color,color);assert.match(style.mask,/apex-chevron-mask.svg/);
    assert.match(style.gradient,/linear-gradient/);assert.equal(style.box.width,32);assert.equal(style.box.height,32);
   }
  }
  await b.reload();assert.equal(await b.evaluate(()=>ApexTheme.get()),'ice');
  await a.evaluate(()=>ApexTheme.set('ember'));
  for(const name of ['apps','board','companion','study']){
   await b.goto('http://apex.test/'+name+'.html');
   assert.equal(await b.evaluate(()=>ApexTheme.get()),'ember');
   assert.equal(await b.locator('.apex-logo').first().evaluate(el=>getComputedStyle(el).getPropertyValue('--apex-brand-start').trim()),tokens.ember);
  }
  await b.goto('http://apex.test/home.html');
  const glow=await b.locator('.apex-logo').evaluate(el=>getComputedStyle(el).boxShadow);
  await b.evaluate(()=>ApexLook.set('normal'));await b.waitForTimeout(250);
  assert.notEqual(await b.locator('.apex-logo').evaluate(el=>getComputedStyle(el).boxShadow),glow);
  await b.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await b.locator('.apex-logo').evaluate(el=>getComputedStyle(el).transitionDuration),'0s');
  await a.evaluate(()=>ApexTheme.set('invalid'));await b.waitForFunction(()=>ApexTheme.get()==='midnight');
  // Optional comparison sheet rendered from the same production logo CSS.
  if(process.env.APEX_BRAND_PREVIEW){
   await b.evaluate(tokens=>{
    document.body.innerHTML='<main style="display:flex;gap:24px;padding:48px;font:14px system-ui;color:white">'+Object.keys(tokens).map(id=>'<section id="'+id+'" style="width:150px;padding:28px;text-align:center;border-radius:24px"><span class="apex-logo" style="width:96px;height:96px"></span><p>'+id[0].toUpperCase()+id.slice(1)+'</p></section>').join('')+'</main>';
   },tokens);
   // Capture each palette's production token values onto its preview tile.
   for(const id of Object.keys(tokens)){
    await b.evaluate(id=>{
     ApexTheme.set(id);ApexLook.set('futuristic');
     const root=getComputedStyle(document.documentElement),tile=document.getElementById(id);
     for(const v of ['start','end','bg','surface'])tile.style.setProperty('--apex-brand-'+v,root.getPropertyValue('--apex-brand-'+v));
     tile.style.background=root.getPropertyValue('--apex-brand-bg');
     if(id==='daylight')tile.style.color='#1a2030';
    },id);
   }
   await b.setViewportSize({width:960,height:285});
   await b.screenshot({path:process.env.APEX_BRAND_PREVIEW});
  }
  console.log('PASS: five palettes, six pages, cross-tab sync, reload, look switching and reduced motion');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
