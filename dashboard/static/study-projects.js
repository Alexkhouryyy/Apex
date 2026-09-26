// Study notebook and explicit saves. Notes remain user text: textContent/value,
// never HTML, and never silently injected into the companion's instructions.
export function setupStudyProjects({api, capture, prepareSave, restore, ready}) {
  const $ = id => document.getElementById(id);
  let project = null, notes = {}, partKey = 'overview', baseline = null, working = false;
  const clone = value => JSON.parse(JSON.stringify(value));
  const signature = value => JSON.stringify(value);
  const snapshot = () => ({...capture(), notes:clone(notes)});
  const dirty = () => baseline !== null && signature(snapshot()) !== baseline;
  const message = text => { $('project-message').textContent = text; };
  function projectUrl() {
    const url = new URL(location.href);url.searchParams.set('project',project.id);
    if(project.model)url.searchParams.set('model',project.model);
    history.replaceState(null,'',url.pathname+url.search);
  }
  function refresh() {
    $('project-status').textContent = project ? project.name + (dirty() ? ' · Unsaved changes' : ' · Saved') : 'Unsaved study';
    $('project-status').title = $('project-status').textContent;
    $('project-save').disabled = working || !ready();
    $('project-copy').disabled = working || !ready() || !project;
    $('pin-study').disabled = working || !project || dirty() || !$('project-workspace').value;
  }
  function selection(id, name) {
    partKey = id || 'overview';
    $('note-label').textContent = id ? 'Notes · ' + name : 'Study notes';
    $('study-notes').value = notes[partKey] || '';
  }
  $('study-notes').oninput = () => {
    if ($('study-notes').value) notes[partKey] = $('study-notes').value;
    else delete notes[partKey];
    refresh();
  };
  async function list() {
    const box = $('saved-projects');box.replaceChildren();
    const result = await api('/api/study/projects');
    if (!result.projects.length) { box.textContent = 'No saved studies yet.'; return; }
    for (const p of result.projects) {
      const row = document.createElement('button');row.type = 'button';row.className = 'saved-project';
      const title = document.createElement('strong'), detail = document.createElement('span');
      title.textContent = p.name;
      detail.textContent = p.compatible ? 'Version ' + p.version + ' · ' + new Date(p.updated_at * 1000).toLocaleString() : 'Different model revision · preserved';
      row.append(title, detail);row.disabled = !p.compatible;
      row.onclick = () => open(p.id);
      box.append(row);
    }
  }
  async function show() {
    if (!$('projects').open) $('projects').showModal();
    $('project-name').value = project?.name || 'Motor study';
    message('Saved on this Apex host. Motion pauses when saved or reopened.');
    refresh();
    try { await list(); } catch (e) { message(e.message); }
    const menu=$('project-workspace');menu.disabled=true;menu.replaceChildren();refresh();
    try {
      const result=await api('/api/board/workspaces');
      for(const space of result.workspaces){const option=document.createElement('option');option.value=space.id;option.textContent=space.name;menu.append(option);}
      if(result.active)menu.value=result.active.id;
      menu.disabled=false;
      $('pin-message').textContent='Save any changes, then choose a workspace for this study.';
    }catch(e){$('pin-message').textContent='Could not load workspaces. '+e.message;}
    refresh();
  }
  async function save(copy = false) {
    if (working || !ready()) return;
    const name = $('project-name').value.trim();
    if (!name) { message('Give this study a name.');$('project-name').focus();return; }
    working = true;refresh();message('Saving…');
    try {
      await prepareSave();
      const saved = snapshot();
      const body = {name, model_hash:saved.model_hash, session_id:saved.session_id, session_revision:saved.revision,
        workspace:{notes:saved.notes, camera:saved.camera, rotor_angle:saved.rotor_angle}};
      const target = copy ? null : project;
      if (target) body.version = target.version;
      const result = await api('/api/study/projects' + (target ? '/' + target.id : ''), {method:'POST',body:JSON.stringify(body)});
      project = result;baseline = signature(saved);projectUrl();
      message('Saved version ' + result.version + '. ' + (dirty() ? 'Newer changes are still unsaved.' : 'You can reopen it after restarting Apex.'));
      try { await list(); } catch (_) { message('Saved successfully. Could not refresh the project list.'); }
    } catch (e) { message('Not saved. ' + e.message); }
    finally { working = false;refresh(); }
  }
  async function open(id) {
    if (working) return;
    if (dirty() && !window.confirm('Open this study and discard unsaved view changes or notes?')) return;
    working = true;refresh();message('Opening…');
    if (!$('projects').open) $('projects').showModal();
    try {
      const result = await api('/api/study/projects/' + id + '/open', {method:'POST'});
      // Do not discard the current notebook until the requested project exists.
      await prepareSave();
      await restore(result);
      notes = result.workspace.notes;
      $('study-notes').value = notes[partKey] || '';
      project = result.project;baseline = signature(snapshot());projectUrl();
      $('project-name').value = project.name;
      message('Opened version ' + project.version + '. Rotor motion is paused.');
      $('projects').close();
    } catch (e) { message('Could not open study. ' + e.message); }
    finally { working = false;refresh(); }
  }
  $('projects-open').onclick = show;
  $('projects-close').onclick = () => $('projects').close();
  $('project-form').onsubmit = e => {e.preventDefault();save();};
  $('project-copy').onclick = () => save(true);
  $('project-workspace').onchange=refresh;
  $('pin-study').onclick=async()=>{
    if(working||!project||dirty()||!$('project-workspace').value)return;
    const saved={...project},workspaceId=$('project-workspace').value;
    const name=$('project-workspace').selectedOptions[0].textContent;
    working=true;refresh();$('project-workspace').disabled=true;
    $('pin-message').textContent='Adding saved study…';
    try{
      await api('/api/board/workspaces/'+encodeURIComponent(workspaceId)+'/studies',{method:'POST',body:JSON.stringify({action:'pin',project_id:saved.id,version:saved.version})});
      $('pin-message').textContent='Added to '+name+'. Open it from the board’s workspace menu.'+(dirty()?' Newer edits still need saving.':'');
    }catch(e){$('pin-message').textContent='Not added. '+e.message;}
    finally{working=false;$('project-workspace').disabled=false;refresh();}
  };
  addEventListener('beforeunload', e => { if (dirty()) {e.preventDefault();e.returnValue = '';} });
  setInterval(() => { if (ready()) refresh(); }, 300);
  return {selection, async initialize(projectId) {
    if(baseline===null)baseline = signature(snapshot());refresh();
    if(projectId && !project)await open(projectId);
  }};
}
