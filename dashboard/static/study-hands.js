// Deterministic gesture gate; camera projection and model edits live in study.js.
export class StudyHandController {
  constructor(callbacks) { this.cb=callbacks;this.sequence=-1;this.held=null;this.candidate=null;this.busy=false;this.missingAt=null;this.releaseAt=null; }
  reset(reason='Open your hand to start') {
    if(this.held)this.cb.cancel(reason);
    this.held=null;this.candidate=null;this.missingAt=null;this.releaseAt=null;this.cb.paint(null,reason);
  }
  feed(sample, now) {
    if(!sample.tracking || sample.age_ms==null || sample.age_ms>350){this.reset(sample.tracking?'Tracking stale · movement cancelled':'Start hand tracking on your Apex laptop');return;}
    // Freeze through a brief occlusion, never move or release an unseen hand.
    if(this.missingAt!==null&&now-this.missingAt>180){this.reset('Hand lost · movement cancelled');return;}
    if(sample.sequence===this.sequence)return;
    this.sequence=sample.sequence;
    const hands=(sample.hands||[]).filter(h=>Number.isFinite(h.x)&&Number.isFinite(h.y)&&h.x>=0&&h.x<=1&&h.y>=0&&h.y<=1&&h.id!=null);
    if(this.busy){this.cb.paint(null,'Finishing movement…');return;}
    if(this.held){
      const h=hands.find(h=>h.id===this.held.id);
      if(!h){this.missingAt??=now;this.releaseAt=null;this.cb.paint(null,'Hand briefly hidden · holding position');return;}
      if(h.fist){this.reset('Closed fist · movement cancelled');return;}
      if(this.missingAt!==null&&!h.pinched){this.reset('Hand returned open · movement cancelled');return;}
      this.missingAt=null;
      if(Math.hypot(h.x-this.held.x,h.y-this.held.y)>.25){this.reset('Tracking jumped · movement cancelled');return;}
      if(!h.pinched){
        // A single noisy open frame is not an intentional release. Hold the
        // last pinched pose until a second fresh open observation confirms it.
        this.releaseAt??=now;
        if(now-this.releaseAt<55){this.cb.paint(h,'Release to apply…');return;}
        this.held=null;this.candidate=null;this.releaseAt=null;this.busy=true;
        Promise.resolve().then(()=>this.cb.commit(h)).catch(()=>this.cb.cancel('Movement failed')).finally(()=>{this.busy=false;});
        this.cb.paint(h,'Released');return;
      }
      this.releaseAt=null;this.held={...h};this.cb.move(h);this.cb.paint(h,'Holding · release to apply');return;
    }
    const h=hands.find(h=>h.id===this.candidate?.id)||hands[0];
    if(!h){this.reset('Show your hand to the camera');return;}
    if(h.fist){this.candidate=null;this.cb.paint(h,'Open your hand, then pinch thumb and index');return;}
    const part=this.cb.hit(h.x,h.y,h.pinched?this.candidate?.part:null);
    if(!part){this.candidate=null;this.cb.paint(h,'Point at a component');return;}
    if(!h.pinched){
      if(!this.candidate||this.candidate.id!==h.id||this.candidate.part!==part)this.candidate={id:h.id,part,since:now};
      const progress=Math.min(1,(now-this.candidate.since)/200);
      this.cb.paint(h,progress===1?'Ready · pinch thumb and index':'Hover steadily…',{part,progress});return;
    }
    if(this.candidate&&this.candidate.id===h.id&&this.candidate.part===part&&now-this.candidate.since>=200){
      this.candidate=null;
      if(this.cb.begin(h,part)){this.held={...h};this.cb.paint(h,'Holding · release to apply',{part,progress:1});}
    } else {this.candidate=null;this.cb.paint(h,'Open your hand, hover, then pinch');}
  }
}
