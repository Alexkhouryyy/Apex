// Camera-space reach mapping and deliberate, open-hand mode selection.
// Coordinates stay in this tab and are never persisted or sent to the server.
export class StudyReach {
  constructor(){this.gain=1;this.center={x:.5,y:.5};}
  point(x,y){return {x:Math.max(0,Math.min(1,.5+(x-this.center.x)*this.gain)),y:Math.max(0,Math.min(1,.5+(y-this.center.y)*this.gain))};}
  hand(h){
    if(!Number.isFinite(h.x)||!Number.isFinite(h.y)||h.x<0||h.x>1||h.y<0||h.y>1)return h;
    const fingertips=Object.fromEntries(Object.entries(h.fingertips||{}).map(([name,p])=>{const q=this.point(...p);return [name,[q.x,q.y]];}));
    // The hologram hand is drawn where the cursor is: same reach mapping.
    const joints=Array.isArray(h.joints)?h.joints.map(p=>{const q=this.point(...p);return [q.x,q.y];}):h.joints;
    return {...h,...this.point(h.x,h.y),rawX:h.x,rawY:h.y,fingertips,joints};
  }
}
export class StudyModeHover {
  reset(){this.target=null;this.id=null;this.since=null;this.last=null;this.latched=false;}
  constructor(){this.reset();}
  feed(h,target,now){
    if(!target||!h||h.pinched||h.fist){this.reset();return {progress:0,activate:false};}
    if(target!==this.target||h.id!==this.id||this.last===null||now-this.last>180){this.reset();this.target=target;this.id=h.id;this.since=now;}
    this.last=now;
    const progress=Math.min(1,(now-this.since)/650),activate=progress===1&&!this.latched;
    if(activate)this.latched=true;
    return {progress,activate};
  }
}
export function setupStudyComfort({enabled,reset,paint,setMode}){
  const $=id=>document.getElementById(id),reach=new StudyReach(),hover=new StudyModeHover();
  const bar=$('hand-mode-bar'),buttons=[...bar.querySelectorAll('[data-hand-mode]')];
  let centering=null,sequence=null;
  function clearHover(){hover.reset();for(const b of buttons)b.style.setProperty('--dwell','0%');}
  function resetInput(label){centering=null;clearHover();reset(label);}
  function syncMode(mode){for(const b of buttons)b.setAttribute('aria-pressed',String(b.dataset.handMode===mode));}
  for(const b of buttons)b.onclick=()=>{resetInput('Mode changed · hover again');setMode(b.dataset.handMode);b.blur();};
  $('hand-reach').onchange=()=>{reach.gain=Number($('hand-reach').value);resetInput('Reach changed · hover again');$('hand-reach').blur();};
  $('hand-center').onclick=()=>{
    if(!enabled()){$('comfort-status').textContent='Enable hands first, then centre your reach.';return;}
    resetInput('Rest your arm comfortably and show an open hand');centering={deadline:performance.now()+8000,candidate:null};
    $('comfort-status').textContent='Rest your elbow. In 2 seconds, hold an open hand still in your comfortable position.';
    centering.ready=performance.now()+2000;$('hand-center').blur();
  };
  $('hand-reach-reset').onclick=()=>{centering=null;reach.center={x:.5,y:.5};reach.gain=1;$('hand-reach').value='1';resetInput('Full reach restored');$('comfort-status').textContent='Full camera reach restored.';$('hand-reach-reset').blur();};
  return {
    syncMode,
    stop(){centering=null;clearHover();bar.hidden=true;},
    show(){bar.hidden=false;},
    blocked(){clearHover();if(centering)centering.candidate=null;},
    prepare(sample,now){
      const fresh=sample.tracking&&sample.age_ms!=null&&sample.age_ms<=350;
      if(!fresh)clearHover();
      if(centering){
        if(now>centering.deadline){centering=null;$('comfort-status').textContent='No steady open hand found. Centre here to retry.';return null;}
        if(!fresh){centering.candidate=null;paint(null,'Waiting for fresh tracking to centre your reach');return null;}
        if(sample.sequence===sequence)return null;
        sequence=sample.sequence;
        const h=(sample.hands||[]).find(h=>h.id!=null&&!h.pinched&&!h.fist&&Number.isFinite(h.x)&&Number.isFinite(h.y)&&h.x>=0&&h.x<=1&&h.y>=0&&h.y<=1);
        if(!h||now<centering.ready){centering.candidate=null;paint(null,'Relax your arm · hold an open hand still');return null;}
        const c=centering.candidate;
        if(!c||c.id!==h.id||now-c.last>180||Math.hypot(h.x-c.x,h.y-c.y)>.04)centering.candidate={...h,since:now,last:now};
        else {c.last=now;if(now-c.since>=650){reach.center={x:h.x,y:h.y};centering=null;resetInput('Reach centred · hover again');$('comfort-status').textContent='Centred on your relaxed hand. Choose Less reach if you want smaller movements.';return null;}}
        paint(null,'Hold steady · centring your reach');return null;
      }
      return {...sample,hands:(sample.hands||[]).map(h=>reach.hand(h))};
    },
    route(h,now){
      if(bar.hidden)return null;
      const r=$('model').getBoundingClientRect(),x=r.left+h.x*r.width,y=r.top+h.y*r.height;
      const inside=bar.getBoundingClientRect();
      const b=buttons.find(b=>{const q=b.getBoundingClientRect();return x>=q.left&&x<=q.right&&y>=q.top&&y<=q.bottom;});
      const result=hover.feed(h,b?.dataset.handMode,now);
      for(const button of buttons)button.style.setProperty('--dwell',button===b?result.progress*100+'%':'0%');
      if(result.activate)setMode(b.dataset.handMode);
      if(b)return {label:h.pinched||h.fist?'Open your hand to choose a mode':result.progress===1?'Mode selected · move back to the model':'Hold over '+b.textContent+' to switch',progress:result.progress};
      return x>=inside.left&&x<=inside.right&&y>=inside.top&&y<=inside.bottom?{label:'Point at a mode',progress:0}:null;
    }
  };
}
