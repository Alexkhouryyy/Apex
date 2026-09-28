// Real Three.js raycasts and the actual board module in Chromium; no camera or live server.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'..','dashboard','static');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE||undefined,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:1200,height:900}}),errors=[],posts=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{
   localStorage.setItem('apex_token','test');
   window.WebSocket=class {constructor(){window.boardSocket=this;}close(){}};
  });
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url()),p=url.pathname;
   if(url.hostname!=='apex.test')return route.abort();
   if(p.startsWith('/api/')){
    const data=route.request().postDataJSON();posts.push({p,data});
    return route.fulfill({json:p==='/api/board/select'?{selection:{id:data.id,title:'Rocket',selected_part:data.part}}:{files:[],workspaces:[]}});
   }
   if(p==='/companion')return route.fulfill({contentType:'text/html',body:'<html></html>'});
   const file=path.join(root,p==='/board'?'board.html':p.replace(/^\/static\//,''));
   if(!fs.existsSync(file)||!fs.statSync(file).isFile())return route.fulfill({status:404,body:''});
   let body=fs.readFileSync(file);
   if(p==='/board')body=body.toString().replace(/(<script type="module">[\s\S]*?)(<\/script>)/, '$1\nwindow.testBoard={models,modelSelection,scene,camera3,reportModelHits,askAbout,renderHud,whyNot};\n$2');
   return route.fulfill({body,contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'});
  });
  await page.goto('http://apex.test/board');
  await page.waitForFunction(()=>window.testBoard?.camera3);
  const checks=await page.evaluate(async()=>{
   const T=await import('/static/vendor/three/build/three.module.min.js');
   const {regionForFace}=await import('/static/board-selection.js');
   const check=(ok,message)=>{if(!ok)throw Error(message)};
   const geometry=new T.BufferGeometry();
   geometry.setAttribute('position',new T.Float32BufferAttribute([0,0,0,1,0,0,0,1,0, 2,0,0,3,0,0,2,1,0],3));
   check(regionForFace(geometry,0).faces.length===1,'merged disconnected pieces were selected together');
   const cube=new T.BoxGeometry(1,1,1);cube.clearGroups();
   check(regionForFace(cube,0).faces.length<12,'fused model did not select local surface');
   check(regionForFace(cube,0).kind==='surface','fused model invented a part');
   const {models,modelSelection,scene}=testBoard;
   const group=new T.Group();
   const bell=new T.Mesh(new T.BoxGeometry(.16,.16,.1),new T.MeshBasicMaterial());bell.name='Engine bell';bell.position.set(.7,-.6,0);
   const body=new T.Mesh(new T.BoxGeometry(.16,.16,.1),new T.MeshBasicMaterial());body.name='Body';body.position.set(.4,-.4,0);
   group.add(bell,body);scene.add(group);models.set('rocket',{group,src:'rocket.glb',base:1});modelSelection.index(group);
   const hit=modelSelection.pick(.7,.6);check(hit.part.name==='Engine bell','wrong mesh hit');
   check(!modelSelection.pick(.05,.05),'empty space selected');
   modelSelection.show({id:hit.id,selected_part:hit.part});
   check(modelSelection.overlay.parent===bell,'highlight attached to wrong part');
   check(modelSelection.overlay.geometry.attributes.position.count===36,'mesh selection highlighted only one material face');
   check(body.children.length===0,'sibling was highlighted');
   modelSelection.clear();check(bell.children.length===0,'clear left stale overlay');
   group.position.x=.05;
   check(modelSelection.pick(.75,.6).part.name==='Engine bell','transformed mesh hit failed');
   group.position.x=0;
   testBoard.renderHud({tracking:true,hands:[{ratio:.1,threshold:.22,release:.32,pinched:false,intent:'confirming pinch'}],cards:[]});
   check(document.querySelector('#hud-state').textContent==='confirming pinch','HUD claimed premature pinch');
   check(testBoard.whyNot({intent:'confirming pinch'},{holding:'Rocket'}).includes('holding'),'held card lost priority');
   return 9;
  });
  await page.mouse.click(840,540);
  await page.waitForFunction(()=>testBoard.modelSelection.overlay?.parent?.name==='Engine bell');
  assert(posts.some(p=>p.p==='/api/board/select'&&p.data.part?.name==='Engine bell'));
  await page.evaluate(()=>testBoard.reportModelHits([{x:.7,y:.6,id:19}]));
  assert(posts.some(p=>p.p==='/api/board/model-hits'&&p.data.hits[0]?.hand===19));
  await page.evaluate(()=>testBoard.askAbout({id:'rocket',title:'Rocket',object_kind:'model',x:.4,y:.4}));
  assert(posts.some(p=>p.p==='/api/board/select'&&p.data.part?.name==='Body'));
  assert.deepEqual(errors,[]);
  console.log(`PASS: ${checks} geometry/HUD checks; actual click, stable hand identity, and tap selection requests.`);
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
