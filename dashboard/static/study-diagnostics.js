// Opt-in, memory-only measurements. No images, landmarks, notes or credentials.
const LIMIT = 600;
function add(list, value) {
  if (!Number.isFinite(value) || value < 0) return;
  list.push(value); if (list.length > LIMIT) list.shift();
}
function distribution(values) {
  if (!values.length) return {samples:0, median:null, p95:null};
  const sorted=[...values].sort((a,b)=>a-b);
  const at=p=>Math.round(sorted[Math.max(0,Math.ceil(sorted.length*p)-1)]*10)/10;
  return {samples:sorted.length, median:at(.5), p95:at(.95)};
}
export class StudyDiagnostics {
  constructor() { this.active=false; this.data=null; }
  start(model, now=performance.now()) {
    this.active=true; this.started=now; this.ended=null; this.lastFrame=null;
    this.lastSequence=null; this.stale=false;
    this.data={model, frame_ms:[], request_ms:[], sample_age_ms:[],
      counts:{samples:0,fresh_frames:0,stale_episodes:0,blocked_samples:0,
        grabs:0,applied:0,cancelled:0,failed:0,connection_errors:0,reported_accidents:0}};
  }
  stop(now=performance.now()) { if(this.active){this.ended=now;this.active=false;this.lastFrame=null;} }
  frame(now, visible=true) {
    if(!this.active)return;
    if(!visible){this.lastFrame=null;return;}
    if(this.lastFrame!==null)add(this.data.frame_ms,now-this.lastFrame);
    this.lastFrame=now;
  }
  sample(sample, roundTrip, blocked=false) {
    if(!this.active)return;
    const c=this.data.counts;c.samples++;if(blocked)c.blocked_samples++;
    add(this.data.request_ms,roundTrip);
    if(sample.age_ms!==null)add(this.data.sample_age_ms,sample.age_ms);
    const stale=!sample.tracking||sample.age_ms==null||sample.age_ms>350;
    if(stale&&!this.stale)c.stale_episodes++;
    this.stale=stale;
    if(!stale&&sample.sequence!==this.lastSequence){c.fresh_frames++;this.lastSequence=sample.sequence;}
  }
  event(name, recording=this.data) { if(this.active&&recording===this.data&&Object.hasOwn(this.data.counts,name))this.data.counts[name]++; }
  report(now=performance.now()) {
    if(!this.data)return null;
    return {schema:1,model:this.data.model,duration_seconds:Math.round(((this.ended??now)-this.started)/100)/10,
      recording:this.active,window_limit:LIMIT,
      frame_interval_ms:distribution(this.data.frame_ms),
      hand_request_round_trip_ms:distribution(this.data.request_ms),
      detector_sample_age_ms:distribution(this.data.sample_age_ms),counts:{...this.data.counts},
      limits:'Timing windows contain the latest 600 observations. Frame intervals measure visible browser rendering, not GPU time. Request time and detector age are separate; neither is end-to-end gesture latency. Applied counts are acknowledged hand actions. Accidents are manually reported. No automatic pass/fail or engineering validation.'};
  }
}

export function setupStudyDiagnostics({model, beforeStart}) {
  const metrics=new StudyDiagnostics(), $=id=>document.getElementById(id);
  const format=d=>d.samples?`${d.median} ms median · ${d.p95} ms p95`:'No samples yet';
  function paint() {
    const r=metrics.report();
    $('diagnostics-toggle').textContent=metrics.active?'Stop recording':'Start recording';
    $('diagnostics-export').disabled=!r;$('diagnostics-accident').disabled=!metrics.active;
    if(!r)return;
    $('diagnostics-status').textContent=`${metrics.active?'Recording':'Stopped'} · ${r.duration_seconds}s · ${r.model}`;
    $('diagnostics-frames').textContent=format(r.frame_interval_ms);
    $('diagnostics-request').textContent=format(r.hand_request_round_trip_ms);
    $('diagnostics-age').textContent=format(r.detector_sample_age_ms);
    const c=r.counts;
    $('diagnostics-actions').textContent=`${c.grabs} grabs · ${c.applied} applied · ${c.cancelled} cancelled · ${c.failed} failed`;
    $('diagnostics-tracking').textContent=`${c.stale_episodes} stale episodes · ${c.connection_errors} connection errors · ${c.reported_accidents} reported accidents`;
  }
  $('diagnostics-toggle').onclick=()=>{
    if(metrics.active)metrics.stop();
    else {if(!model())return;beforeStart();metrics.start(model());}
    paint();
  };
  $('diagnostics-accident').onclick=()=>{metrics.event('reported_accidents');paint();};
  $('diagnostics-export').onclick=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(metrics.report(),null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download='apex-study-diagnostics.json';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  setInterval(()=>{if(metrics.active&&$('device-check').open)paint();},1000);
  return {metrics, stop:()=>{metrics.stop();paint();}};
}
