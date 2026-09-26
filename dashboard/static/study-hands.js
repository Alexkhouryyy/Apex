// Deterministic gesture gate; camera projection and model edits live in study.js.
export class StudyHandController {
  constructor(callbacks) { this.cb=callbacks;this.sequence=-1;this.held=null;this.candidate=null;this.busy=false; }
  reset(reason='Open your hand to start') {
    if(this.held)this.cb.cancel(reason);
    this.held=null;this.candidate=null;this.cb.paint(null,reason);
  }
  feed(sample, now) {
    if(!sample.tracking || sample.age_ms==null || sample.age_ms>350){this.reset(sample.tracking?'Tracking stale · movement cancelled':'Start hand tracking on your Apex laptop');return;}
    if(sample.sequence===this.sequence)return;
    this.sequence=sample.sequence;
    const hands=(sample.hands||[]).filter(h=>Number.isFinite(h.x)&&Number.isFinite(h.y)&&h.x>=0&&h.x<=1&&h.y>=0&&h.y<=1&&h.id!=null);
    if(this.busy){this.cb.paint(null,'Finishing movement…');return;}
    if(this.held){
      const h=hands.find(h=>h.id===this.held.id);
      if(!h){this.reset('Hand lost · movement cancelled');return;}
      if(Math.hypot(h.x-this.held.x,h.y-this.held.y)>.25){this.reset('Tracking jumped · movement cancelled');return;}
      if(!h.pinched){
        this.held=null;this.candidate=null;this.busy=true;
        Promise.resolve(this.cb.commit(h)).catch(()=>this.cb.cancel('Movement failed')).finally(()=>{this.busy=false;});
        this.cb.paint(h,'Released');return;
      }
      this.held={...h};this.cb.move(h);this.cb.paint(h,'Holding · release to apply');return;
    }
    const h=hands.find(h=>h.id===this.candidate?.id)||hands[0];
    if(!h){this.reset('Show one open hand');return;}
    const part=this.cb.hit(h.x,h.y);
    if(!part){this.candidate=null;this.cb.paint(h,'Point at a component');return;}
    if(!h.pinched){
      if(!this.candidate||this.candidate.id!==h.id||this.candidate.part!==part)this.candidate={id:h.id,part,since:now};
      this.cb.paint(h,now-this.candidate.since>=250?'Ready · pinch to hold':'Hover steadily…');return;
    }
    if(this.candidate&&this.candidate.id===h.id&&this.candidate.part===part&&now-this.candidate.since>=250){
      this.candidate=null;
      if(this.cb.begin(h,part)){this.held={...h};this.cb.paint(h,'Holding · release to apply');}
    } else {this.candidate=null;this.cb.paint(h,'Open your hand, hover, then pinch');}
  }
}
