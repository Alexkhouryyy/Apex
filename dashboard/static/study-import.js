// Import a real 3D model into the study library (agent/study_import.py), and
// the two actions an imported model offers: drafting notes with AI, and
// removing it. Upload uses XHR for a progress bar; everything else goes
// through the page's authenticated api().
const $ = id => document.getElementById(id);

// Imported files carry no take-apart directions. Most assemblies stack their
// parts along one axis (a motor, a gearbox, a wheel): find the axis the part
// centres spread along (or the longest side if they are all concentric), spread
// the parts in order along it, and push off-centre parts outward as well.
export function autoExplode(THREE,entries,extent){
  const n=entries.length;if(n<2){for(const e of entries)e.offset.set(0,0,0);return;}
  const mean=new THREE.Vector3();for(const e of entries)mean.add(e.center);mean.multiplyScalar(1/n);
  const d=entries.map(e=>e.center.clone().sub(mean)),m=[[0,0,0],[0,0,0],[0,0,0]];
  for(const v of d){const a=v.toArray();for(let i=0;i<3;i++)for(let j=0;j<3;j++)m[i][j]+=a[i]*a[j]/n;}
  let axis=new THREE.Vector3(1,1,1).normalize();
  for(let k=0;k<30;k++){const a=axis.toArray();const next=new THREE.Vector3(...[0,1,2].map(i=>m[i][0]*a[0]+m[i][1]*a[1]+m[i][2]*a[2]));if(next.length()<1e-9)break;axis=next.normalize();}
  const L=Math.max(extent.x,extent.y,extent.z);
  if(m[0][0]+m[1][1]+m[2][2]<(0.02*L)**2){axis=extent.x>=extent.y&&extent.x>=extent.z?new THREE.Vector3(1,0,0):extent.y>=extent.z?new THREE.Vector3(0,1,0):new THREE.Vector3(0,0,1);}
  const t=d.map(v=>v.dot(axis)),order=[...t.keys()].sort((i,j)=>t[i]-t[j]||i-j);
  order.forEach((i,rank)=>{
    const radial=d[i].clone().sub(axis.clone().multiplyScalar(t[i]));
    const out=radial.length()>0.05*L?radial.clone().normalize().multiplyScalar(0.12*L):new THREE.Vector3();
    entries[i].offset.copy(axis).multiplyScalar((rank/(n-1)-0.5)*1.8*L).add(radial.multiplyScalar(0.6)).add(out);
  });
}

export function setupStudyImport({token, api, status, beforeLeave, navigate = url => location.assign(url)}) {
  const dialog = $('import-dialog'), message = $('import-message'), progress = $('import-progress');
  let current = null, busy = false;
  const say = (text, error = false) => { message.textContent = text; message.classList.toggle('error', error); };

  $('import-open').onclick = () => { if (!dialog.open) { say(''); progress.hidden = true; dialog.showModal(); } };
  $('import-close').onclick = () => { if (!busy) dialog.close(); };
  $('import-file').onchange = () => {
    const file = $('import-file').files[0];
    if (file && !$('import-name').value.trim())
      $('import-name').placeholder = file.name.replace(/\.(glb|gltf)$/i, '').replace(/[_-]+/g, ' ');
    say(file && !/\.(glb|gltf)$/i.test(file.name)
      ? 'That is not a .glb or .gltf file. Convert it to glTF 2.0 first (Blender: File → Export → glTF 2.0).' : '', true);
  };

  function upload(file, query) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/study/import?' + query);
      xhr.setRequestHeader('Authorization', 'Bearer ' + token());
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = e => { if (e.lengthComputable) progress.value = e.loaded / e.total; };
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch (_) {}
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error(body.detail || `Import failed (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error('The upload was interrupted. Check the connection to Apex and try again.'));
      xhr.send(file);
    });
  }

  $('import-form').onsubmit = async e => {
    e.preventDefault();
    const file = $('import-file').files[0];
    if (!file || busy) return;
    if (!/\.(glb|gltf)$/i.test(file.name)) { say('Choose a .glb or .gltf file.', true); return; }
    if (file.size > 150 * 1024 * 1024) { say('That file is larger than 150 MB.', true); return; }
    const query = new URLSearchParams({name: file.name, title: $('import-name').value.trim(),
      category: $('import-category').value.trim(), detail: $('import-detail').value,
      source: $('import-source').value.trim(), license: $('import-license').value.trim()});
    busy = true; $('import-go').disabled = true; progress.hidden = false; progress.value = 0;
    say('Uploading ' + file.name + '…');
    try {
      const result = await upload(file, query);
      say(`Imported “${result.title}” · ${result.parts} part${result.parts === 1 ? '' : 's'}. Opening it…`);
      beforeLeave?.();
      navigate('/study?model=' + encodeURIComponent(result.id));
    } catch (error) {
      say(error.message, true);
    } finally {
      busy = false; $('import-go').disabled = false;
    }
  };

  $('draft-notes').onclick = async () => {
    if (!current?.imported || busy) return;
    busy = true; $('draft-notes').disabled = true;
    status(`Drafting notes for ${current.parts.length} parts with AI… this can take a minute.`);
    try {
      const r = await api('/api/study/model/' + encodeURIComponent(current.id) + '/draft-notes', {method: 'POST', body: '{}'});
      status(`Drafted notes for ${r.drafted} of ${r.of} parts · AI-drafted, not reviewed. Reloading…`);
      beforeLeave?.();
      navigate('/study?model=' + encodeURIComponent(current.id));
    } catch (error) {
      status(error.message);
    } finally {
      busy = false; $('draft-notes').disabled = false;
    }
  };

  $('remove-import').onclick = async () => {
    if (!current?.imported || busy) return;
    if (!confirm(`Remove “${current.title}” from the library? Its file and drafted notes are deleted from this Apex host. Saved studies of it can no longer be opened.`)) return;
    try {
      await api('/api/study/model/' + encodeURIComponent(current.id), {method: 'DELETE'});
      beforeLeave?.();
      navigate('/study?model=jet-engine');
    } catch (error) {
      status(error.message);
    }
  };

  return {
    // Called whenever a subject loads: imported ones show their tools.
    show(manifest) {
      current = manifest;
      $('import-tools').hidden = !manifest?.imported;
      if (!manifest?.imported) return;
      const drafted = !!manifest.notes;
      $('draft-notes').textContent = drafted ? 'Redraft notes' : 'Draft notes with AI';
      $('import-about').textContent = drafted
        ? 'Imported by you · notes AI-drafted, not reviewed.'
        : 'Imported by you · parts named from the file. Draft notes to explain each part.';
    },
  };
}
