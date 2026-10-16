// Design tools share the board's real models, selection, history and partner.
export class DesignWorkbench {
  constructor({api, select, workspace, partner, photo, clearReference, action}) {
    Object.assign(this,{api,select,workspace,partner,photo,clearReference,action});
    this.part=0;this.key='';this.generation=0;this.drafts=new Map();
    const axes=(kind,values)=>`<div class="dw-axes">${['x','y','z'].map((a,i)=>`<label>${a.toUpperCase()}<input id="dw-${kind}-${a}" type="number" step="any" required ${kind==='size'||kind==='create'?'min="0.5" max="10000"':''} ${values?`value="${values[i]}"`:''}></label>`).join('')}</div>`;
    this.panel=document.createElement('aside');this.panel.id='design-workbench';this.panel.hidden=true;this.panel.setAttribute('aria-label','Measured design workbench');
    this.panel.innerHTML=`<header><div><span class="dw-kicker">APEX / DESIGN</span><h2>From idea to object.</h2></div><button id="dw-close" aria-label="Close design tools">×</button></header>
      <p class="dw-muted">Describe it. Shape its parts. Check what you can make.</p><div class="dw-row"><button id="dw-new">＋ Measured build</button><button id="dw-talk">Ask Céline ↗</button></div>
      <details><summary>01 / Visual reference</summary><p class="dw-muted">A photo supplies appearance. Physical dimensions need your measurements.</p>
      <input id="dw-photo" type="file" accept="image/png,image/jpeg,image/webp" aria-label="Reference photo"><button id="dw-camera">Use current camera frame</button><img id="dw-preview" alt="Selected design reference" hidden>
      <div class="dw-row"><button id="dw-share" disabled>Use in my next question</button><button id="dw-remove" hidden>Remove</button></div><p id="dw-photo-status" class="dw-muted" role="status">Photos stay in this tab until you share them.</p></details>
      <span class="dw-kicker">02 / COMPONENTS</span><h3 id="dw-title">Select or create a model</h3><p id="dw-version" class="dw-muted"></p><div class="dw-row" id="dw-view" hidden><button id="dw-turn-left" aria-label="Turn view left">↶</button><button id="dw-frame">Frame object</button><button id="dw-turn-right" aria-label="Turn view right">↷</button></div><button id="dw-hands" hidden>Edit parts with hands · P</button><div id="dw-parts"></div><p id="dw-empty" class="dw-muted">Start with measured shapes, or ask Céline to build a custom assembly.</p><button id="dw-retry" hidden>Retry inspection</button>
      <form id="dw-edit" hidden><label>Component name<input id="dw-name" required maxlength="60"></label><fieldset><legend>Physical size · mm</legend>${axes('size')}</fieldset><fieldset><legend>Position · mm</legend>${axes('position')}</fieldset><fieldset><legend>Rotation · degrees</legend>${axes('rotation')}</fieldset>
      <p class="dw-muted">X = width · Y = height · Z = depth. View scaling never changes these measurements.</p><div class="dw-row"><button id="dw-apply">Save new version</button><button id="dw-reload" type="button">Reload saved</button></div></form>
      <section id="dw-verify" hidden><span class="dw-kicker">03 / CHECK & EXPORT</span><label>Check scope<select id="dw-scope"><option value="part">Selected component</option><option value="all">Whole assembly</option></select></label><div class="dw-row"><button id="dw-check">Run print checks</button><select id="dw-format" aria-label="Export format"><option value="3mf">3MF · millimetres</option><option value="stl">STL</option></select></div><button id="dw-export">Check & export file</button><div id="dw-report" role="status"></div><button id="dw-download" hidden>Download checked file</button>
      <p class="dw-muted">Checks cover the configured 3D printer and sampled mesh geometry. Assembly fit, electronics and CNC toolpaths need separate validation.</p></section><p id="dw-status" role="status" aria-live="polite"></p>`;
    document.body.append(this.panel);
    this.dialog=document.createElement('dialog');this.dialog.id='design-create';this.dialog.innerHTML=`<form id="dw-create"><span class="dw-kicker">NEW MEASURED BUILD</span><h2>Start with real dimensions.</h2><label>Name<input id="dw-create-title" required maxlength="80" placeholder="My enclosure"></label><label>Starting shape<select id="dw-shape"><option value="enclosure">Round enclosure · three parts</option><option value="box">Box</option><option value="cylinder">Cylinder</option><option value="tube">Hollow tube</option><option value="sphere">Sphere</option><option value="gear">Gear</option></select></label><fieldset><legend>Size · millimetres</legend>${axes('create',[40,12,40])}</fieldset><p class="dw-muted">The enclosure is an editable example with its lid beside the base. Use equal X/Z dimensions. Ask Céline for a custom assembly.</p><p id="dw-create-error" role="alert"></p><div class="dw-row"><button id="dw-create-go">Create build</button><button id="dw-cancel" type="button">Cancel</button></div></form>`;document.body.append(this.dialog);
    const toggle=document.createElement('button');toggle.id='design-toggle';toggle.textContent='Design';toggle.setAttribute('aria-pressed','false');toggle.onclick=()=>this.open(this.panel.hidden);document.querySelector('#board-bar .header-actions').prepend(toggle);
    this.$('dw-close').onclick=()=>this.open(false);this.$('dw-new').onclick=()=>this.dialog.showModal();this.$('dw-cancel').onclick=()=>this.dialog.close();
    this.$('dw-create').onsubmit=e=>{e.preventDefault();this.create();};this.$('dw-edit').onsubmit=e=>{e.preventDefault();this.edit();};
    this.$('dw-edit').oninput=()=>{if(!this.data)return;this.drafts.set(this.draftKey(),{source:this.data.source,fields:this.fields()});this.clearReport();this.status('Unsaved measurements.');};
    this.$('dw-reload').onclick=()=>{this.drafts.delete(this.draftKey());this.key='';this.update(this.latest);};
    this.$('dw-retry').onclick=()=>{this.key='';this.update(this.latest);};
    this.$('dw-talk').onclick=()=>this.partner({apex:'design-open'});
    this.$('dw-photo').onchange=()=>this.readPhoto(this.$('dw-photo').files[0]);
    this.$('dw-camera').onclick=()=>{const frame=this.photo();if(frame)this.reference('data:image/jpeg;base64,'+frame);else this.status('No camera frame is available. Enable your configured hand camera, or upload a photo.');};
    this.$('dw-remove').onclick=()=>this.reference(null);
    this.$('dw-share').onclick=()=>{if(!this.referenceImage)return;this.partner({apex:'design-reference',image:this.referenceImage.split(',')[1]});this.sharedReference=true;this.$('dw-photo-status').textContent='Reference staged in Céline. Send your question to share it.';};
    this.$('dw-check').onclick=()=>this.fabricate(false);this.$('dw-export').onclick=()=>this.fabricate(true);this.$('dw-download').onclick=()=>this.download();this.$('dw-scope').onchange=()=>this.clearReport();
    this.$('dw-frame').onclick=()=>this.view(true);this.$('dw-turn-left').onclick=()=>this.view(false,-Math.PI/12);this.$('dw-turn-right').onclick=()=>this.view(false,Math.PI/12);
    this.$('dw-hands').onclick=async()=>{if(!this.data)return;try{await this.api('/api/board/parts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:this.data.card.id,...this.workspace()})});this.status('Parts mode is ready. Resume your configured hand controls, then pinch one component. Release before applying measurements.');}catch(e){this.status(e.message);}};
    this.open(new URLSearchParams(location.search).get('design')==='1');
  }
  $(id){return document.getElementById(id);}
  status(text){this.$('dw-status').textContent=text;}
  open(on=true){this.panel.hidden=!on;document.body.classList.toggle('design-workspace',on);this.$('design-toggle').setAttribute('aria-pressed',String(on));if(on&&this.latest)this.update(this.latest);}
  draftKey(){return `${this.data?.workspace?.epoch||''}:${this.data?.card.id}:${this.part}`;}
  fields(){const read=k=>['x','y','z'].map(a=>Number(this.$(`dw-${k}-${a}`).value));return{name:this.$('dw-name').value,size_mm:read('size'),position_mm:read('position'),rotation_deg:read('rotation')};}
  fill(p){this.$('dw-name').value=p.name;for(const[k,f]of[['size','size_mm'],['position','position_mm'],['rotation','rotation_deg']])['x','y','z'].forEach((a,i)=>{this.$(`dw-${k}-${a}`).value=p[f][i];});}
  clearReport(){this.validationGeneration=(this.validationGeneration||0)+1;this.checked=null;this.$('dw-report').replaceChildren();this.$('dw-download').hidden=true;}
  async update(msg){
    if(!msg)return;
    if(this.latest?.workspace?.epoch!==msg.workspace?.epoch)this.reference(null);
    this.latest=msg;if(this.panel.hidden)return;
    const sel=msg.selection,card=(msg.cards||[]).find(c=>c.id===sel?.id),key=JSON.stringify([msg.workspace?.epoch,card?.id,card?.src]);
    if(key===this.key){const index=sel?.selected_part?.mesh;if(Number.isInteger(index)&&index!==this.part&&this.data?.parts[index])this.choose(index,false);return;}
    this.key=key;const generation=++this.generation;this.data=null;this.clearReport();this.$('dw-retry').hidden=true;this.$('dw-edit').hidden=this.$('dw-verify').hidden=this.$('dw-view').hidden=this.$('dw-hands').hidden=true;this.$('dw-parts').replaceChildren();this.$('dw-empty').hidden=false;this.$('dw-version').textContent='';
    if(card?.kind!=='model'){this.data=null;this.$('dw-title').textContent='Select or create a model';this.$('dw-empty').textContent='Start with measured shapes, or ask Céline to build a custom assembly.';return;}
    this.$('dw-title').textContent=card.title;
    this.$('dw-empty').textContent='Loading saved components…';
    try{
      const data=await(await this.api('/api/board/design/'+encodeURIComponent(card.id))).json();if(generation!==this.generation)return;
      if(data.source!==card.src||data.workspace?.epoch!==msg.workspace?.epoch){this.key='';return;}
      this.data=data;this.$('dw-empty').hidden=true;this.$('dw-edit').hidden=this.$('dw-verify').hidden=this.$('dw-view').hidden=this.$('dw-hands').hidden=false;
      this.$('dw-version').textContent=`Saved v${data.version} · ${data.parts.length} components · ${data.extent_mm.map(v=>Number(v.toFixed(2))).join(' × ')} mm overall`;
      this.choose(Math.min(Number.isInteger(sel.selected_part?.mesh)?sel.selected_part.mesh:this.part,data.parts.length-1),false);
    }catch(e){if(generation!==this.generation)return;this.data=null;this.$('dw-empty').textContent=e.message;this.$('dw-retry').hidden=false;}
  }
  async choose(index,send=true){
    const p=this.data?.parts[index];if(!p)return;
    if(send){const source=this.data.source;const selected=await this.select(this.data.card.id,{kind:'part',mesh:index,face:0,name:p.name,src:source});if(!selected||this.data?.source!==source){this.status('The component selection changed. Wait for the workspace to update.');return;}}
    this.part=index;this.clearReport();const draft=this.drafts.get(this.draftKey());this.conflicted=Boolean(draft&&draft.source!==this.data.source);this.fill(draft?.fields||p);this.$('dw-apply').disabled=this.conflicted;
    this.status(this.conflicted?'This component changed. Your draft is preserved; reload the saved version before applying it.':draft?'Unsaved measurements restored.':'Select a component, then change its measurements or ask Céline about it.');
    const list=this.$('dw-parts');list.replaceChildren();for(const row of this.data.parts){const b=document.createElement('button');b.type='button';b.className='dw-component';b.setAttribute('aria-pressed',String(row.index===index));const name=document.createElement('span'),dims=document.createElement('small');name.textContent=row.name;dims.textContent=row.size_mm.map(v=>Number(v.toFixed(2))).join(' × ')+' mm';b.append(name,dims);b.onclick=()=>this.choose(row.index);list.append(b);}
  }
  async request(action,extra={}){return(await this.api('/api/board/design',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...this.workspace(),...extra})})).json();}
  async view(frame,delta=0){
    const card=this.latest?.cards?.find(c=>c.id===this.data?.card.id);if(!card||!this.action)return;
    const left=document.getElementById('objects-panel')?.getBoundingClientRect().right||0,right=this.panel.getBoundingClientRect().left;
    const changes=frame?{x:(left+right)/2/innerWidth,y:.48,scale:Math.min(4,Math.max(.5,(right-left-70)/(.18*innerWidth)))}:{rot:Math.atan2(Math.sin((card.rot||0)+delta),Math.cos((card.rot||0)+delta))};
    try{await this.action('transform',{id:card.id,changes});}catch(e){this.status(e.message);}
  }
  async create(){this.$('dw-create-go').disabled=true;this.$('dw-create-error').textContent='';try{await this.request('create',{title:this.$('dw-create-title').value,shape:this.$('dw-shape').value,size_mm:['x','y','z'].map(a=>Number(this.$('dw-create-'+a).value))});this.dialog.close();this.status('Build saved. Select a component to refine it.');}catch(e){this.$('dw-create-error').textContent=e.message;}finally{this.$('dw-create-go').disabled=false;}}
  async edit(){
    if(!this.data||this.conflicted||this.saving)return;const before=this.data,part=this.part,fields=this.fields(),stamp=JSON.stringify(fields),key=this.draftKey();this.saving=true;this.$('dw-apply').disabled=true;
    try{const data=await this.request('edit',{id:before.card.id,expected_src:before.source,part,changes:fields});
      const pending=this.drafts.get(key);if(!pending||JSON.stringify(pending.fields)===stamp)this.drafts.delete(key);else this.drafts.set(key,{source:data.source,fields:pending.fields});
      if(this.data?.card.id!==before.card.id||this.data?.workspace?.epoch!==before.workspace?.epoch||this.part!==part)return;
      const live=this.latest?.cards?.find(c=>c.id===before.card.id);if(live&&live.src!==before.source&&live.src!==data.source){this.key='';this.update(this.latest);return;}
      this.fill(this.drafts.get(key)?.fields||data.parts[part]);this.conflicted=false;this.data=data;this.key='';this.clearReport();this.status(`Saved v${data.version}. Undo restores the previous geometry.`);
    }catch(e){this.status(e.message);if(e.status===409){this.conflicted=true;this.key='';}}finally{this.saving=false;this.$('dw-apply').disabled=Boolean(this.conflicted);}
  }
  async fabricate(exportFile){
    if(!this.data||this.busy)return;if(this.drafts.has(this.draftKey())||this.conflicted){this.status('Save or reload your measurements before checking the saved geometry.');return;}
    this.busy=true;const before=this.data,part=this.$('dw-scope').value==='all'?null:this.part;this.clearReport();const generation=this.validationGeneration;this.$('dw-report').textContent='Measuring the saved mesh…';
    try{const result=await this.request(exportFile?'export':'check',{id:before.card.id,expected_src:before.source,part,format:this.$('dw-format').value});if(this.data?.source!==before.source||this.data?.card.id!==before.card.id||(this.$('dw-scope').value==='all'?null:this.part)!==part)return;
      if(this.data?.workspace?.epoch!==before.workspace?.epoch||this.validationGeneration!==generation)return;
      const heading=document.createElement('strong'),detail=document.createElement('pre');heading.textContent=`${result.scope} · v${result.version} · ${result.verdict.replaceAll('_',' ')}`;detail.textContent=result.description;this.$('dw-report').replaceChildren(heading,detail);this.checked=result;this.$('dw-download').hidden=!result.file;
    }catch(e){this.$('dw-report').textContent=e.message;}finally{this.busy=false;}
  }
  async download(){if(!this.checked?.file)return;const file=this.checked.file;try{const r=await this.api('/api/forge/download/'+file.split('/').map(encodeURIComponent).join('/')),blob=await r.blob();if(this.checked?.file!==file)return;const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=file.split('/').slice(-2).join('-');a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}catch(e){this.status(e.message);}}
  reference(image){this.photoGeneration=(this.photoGeneration||0)+1;if(this.sharedReference){this.clearReference?.();this.sharedReference=false;}this.referenceImage=image;this.$('dw-preview').hidden=!image;if(image)this.$('dw-preview').src=image;else this.$('dw-preview').removeAttribute('src');this.$('dw-share').disabled=!image;this.$('dw-remove').hidden=!image;this.$('dw-photo-status').textContent=image?'Visual reference ready. Enter measured dimensions; this is not a 3D scan.':'Photos stay in this tab until you share them.';}
  async readPhoto(file){
    if(!file)return;if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>15*1024*1024){this.status('Choose a PNG, JPEG or WebP below 15 MB.');return;}const url=URL.createObjectURL(file),generation=this.photoGeneration=(this.photoGeneration||0)+1;
    try{const img=new Image();await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=reject;img.src=url;});if(generation!==this.photoGeneration)return;if(img.width*img.height>40000000)throw Error('Choose a photo below 40 megapixels.');const factor=Math.min(1,1280/Math.max(img.width,img.height)),canvas=document.createElement('canvas');canvas.width=Math.round(img.width*factor);canvas.height=Math.round(img.height*factor);const c=canvas.getContext('2d');c.fillStyle='#fff';c.fillRect(0,0,canvas.width,canvas.height);c.drawImage(img,0,0,canvas.width,canvas.height);const image=canvas.toDataURL('image/jpeg',.82);if(image.length>2000000)throw Error('Choose a simpler or smaller photo.');this.reference(image);}catch(e){this.status(e.message||'This photo could not be opened.');}finally{URL.revokeObjectURL(url);}
  }
}
