import * as THREE from './vendor/three/build/three.module.min.js';

// Select actual triangles, including disconnected pieces inside a merged mesh.
// A single welded surface has no semantic part boundaries: select a local
// surface region and label it as such rather than inventing an engine name.
export function regionForFace(geometry, face, wholeComponent = false) {
  const pos=geometry.attributes.position,idx=geometry.index;
  const count=Math.floor((idx?idx.count:pos.count)/3);
  if(!Number.isInteger(face)||face<0||face>=count)throw new Error('Invalid model face.');
  if(count>200000)return {faces:[face],kind:'surface',label:'Surface triangle'};
  geometry.computeBoundingBox();
  const epsilon=Math.max(geometry.boundingBox.getSize(new THREE.Vector3()).length()*1e-6,1e-9);
  const vertices=new Map(),weld=[],edges=new Map(),adj=Array.from({length:count},()=>[]),normals=[];
  const material=f=>geometry.groups.find(g=>f*3>=g.start&&f*3<g.start+g.count)?.materialIndex??0;
  const vertex=i=>idx?idx.getX(i):i;
  for(let i=0;i<pos.count;i++){
    const key=[pos.getX(i),pos.getY(i),pos.getZ(i)].map(n=>Math.round(n/epsilon)).join(',');
    if(!vertices.has(key))vertices.set(key,vertices.size);weld[i]=vertices.get(key);
  }
  const a=new THREE.Vector3(),b=new THREE.Vector3(),c=new THREE.Vector3();
  for(let f=0;f<count;f++){
    const ids=[vertex(f*3),vertex(f*3+1),vertex(f*3+2)];
    a.fromBufferAttribute(pos,ids[0]);b.fromBufferAttribute(pos,ids[1]);c.fromBufferAttribute(pos,ids[2]);
    normals.push(b.sub(a).cross(c.sub(a)).normalize().clone());
    for(let j=0;j<3;j++){
      const x=weld[ids[j]],y=weld[ids[(j+1)%3]];
      if(x===y)continue;
      const key=(x<y?x+':'+y:y+':'+x)+':'+(wholeComponent?'all':material(f));
      const prior=edges.get(key);
      if(prior!==undefined){adj[f].push(prior);adj[prior].push(f)}else edges.set(key,f);
    }
  }
  const walk=(patch)=>{
    const chosen=new Set([face]),queue=[face],mat=material(face),normal=normals[face];
    let head=0;
    while(head<queue.length&&chosen.size<(patch?Math.min(5000,count):count)){
      const f=queue[head++];for(const other of adj[f]){
        if(chosen.has(other)||(!wholeComponent&&material(other)!==mat)||(patch&&normal.dot(normals[other])<.82))continue;
        chosen.add(other);queue.push(other);
      }
    }
    return [...chosen];
  };
  const connected=walk(false);
  if(wholeComponent||connected.length<count)return {faces:connected,kind:'component',label:'Mesh component'};
  return {faces:walk(true),kind:'surface',label:'Surface region'};
}

export class ModelSelection {
  constructor(models,camera){this.models=models;this.camera=camera;this.ray=new THREE.Raycaster();this.overlay=null;this.current='';this.regions=new WeakMap();}
  index(group){
    let i=0;group.traverse(o=>{if(o.isMesh&&!o.userData.apexSelectionOverlay)o.userData.apexMeshId=i++});
  }
  pick(x,y,id=null,detail=true){
    const camera=this.camera();if(!camera)return null;
    camera.updateMatrixWorld();this.ray.setFromCamera(new THREE.Vector2(x*2-1,1-y*2),camera);
    const candidates=[];
    for(const [card,entry] of this.models){if(id&&card!==id)continue;entry.group.updateMatrixWorld(true);for(const h of this.ray.intersectObject(entry.group,true)){if(h.object.isMesh&&!h.object.userData.apexSelectionOverlay)candidates.push({...h,card,src:entry.src})}}
    candidates.sort((a,b)=>a.distance-b.distance);const hit=candidates[0];
    return hit?(detail?this.describe(hit.card,hit.src,hit.object,hit.faceIndex):{id:hit.card,src:hit.src}):null;
  }
  describe(id,src,mesh,face){
    let regions=this.regions.get(mesh.geometry);if(!regions){regions=new Map();this.regions.set(mesh.geometry,regions)}
    let count=0;this.models.get(id)?.group.traverse(o=>{if(o.isMesh&&!o.userData.apexSelectionOverlay)count++});
    let owner=mesh;while(owner.parent&&owner.userData.part===undefined)owner=owner.parent;
    const authored=owner.userData.part!==undefined;
    const cacheKey=face+':'+(count>1||authored);
    let region=regions.get(cacheKey);if(!region){region=regionForFace(mesh.geometry,face,count>1||authored);if(regions.size>12)regions.clear();regions.set(cacheKey,region)}
    const name=authored?(owner.name||mesh.name||'Part '+(owner.userData.part+1)):
      region.kind==='surface'?region.label+(mesh.name?' · '+mesh.name:''):(mesh.name||region.label);
    return {id,part:{src,mesh:mesh.userData.apexMeshId,face,kind:authored?'part':region.kind,name:String(name).slice(0,120)}};
  }
  show(selection){
    const part=selection?.selected_part,key=part?JSON.stringify([selection.id,part]):'';
    if(key===this.current&&this.overlay?.parent)return;
    this.clear();this.current=key;if(!part)return;
    const entry=this.models.get(selection.id);if(!entry||entry.src!==part.src)return;
    let mesh;entry.group.traverse(o=>{if(o.isMesh&&!o.userData.apexSelectionOverlay&&o.userData.apexMeshId===part.mesh)mesh=o});
    if(!mesh)return;
    let count=0;entry.group.traverse(o=>{if(o.isMesh&&!o.userData.apexSelectionOverlay)count++});
    let region;try{region=regionForFace(mesh.geometry,part.face,count>1||part.kind==='part')}catch(_){return}
    const source=mesh.geometry,index=source.index,positions=source.attributes.position;
    const values=[];for(const face of region.faces)for(let j=0;j<3;j++){const i=index?index.getX(face*3+j):face*3+j;values.push(positions.getX(i),positions.getY(i),positions.getZ(i))}
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(values,3));
    const accent=getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()||'#83dcd1';
    const material=new THREE.MeshBasicMaterial({color:accent,transparent:true,opacity:.42,side:THREE.DoubleSide,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-2});
    this.overlay=new THREE.Mesh(geometry,material);this.overlay.userData.apexSelectionOverlay=true;mesh.add(this.overlay);
  }
  clear(){if(this.overlay){this.overlay.removeFromParent();this.overlay.geometry.dispose();this.overlay.material.dispose();this.overlay=null}this.current='';}
}
