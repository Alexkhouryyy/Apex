import * as THREE from '../dashboard/static/vendor/three/build/three.module.min.js';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const source=fs.readFileSync('dashboard/static/study.js','utf8');
const code=source.slice(source.indexOf('function beginManipulation('),source.indexOf('let fingerGuide='));
const camera=new THREE.PerspectiveCamera();camera.position.set(7,4,8);
const orbit={target:new THREE.Vector3(.45,0,0),enabled:true,enableDamping:true,minDistance:2.5,maxDistance:30,update(){camera.lookAt(this.target);}};
let mode='orbit',writes=0;
const {begin,move,cancel,commit}=new Function('THREE','camera','orbit','$','command',`
 let current={revision:1,transforms:{}},manipulation=null,cameraTween=null,amount=0,targetAmount=0;
 const status=()=>{},diagnostics={metrics:{event(){}}};
 ${code}
 return {begin:beginManipulation,move:moveManipulation,cancel:cancelManipulation,commit:commitManipulation};
`)(THREE,camera,orbit,()=>({value:mode}),()=>{writes++;return true;});
const start=camera.position.clone(),target=orbit.target.clone(),radius=camera.position.distanceTo(target);
assert(begin({x:.5,y:.5},'@view','hand'));
move({x:.6,y:.55});assert(camera.position.distanceTo(start)>.1);
assert(Math.abs(camera.position.distanceTo(target)-radius)<1e-8,'orbit keeps radius');
cancel();assert(camera.position.distanceTo(start)<1e-8,'cancellation restores camera');assert(orbit.enabled&&orbit.enableDamping);
assert(begin({x:.5,y:.5},'@view','hand'));move({x:.6,y:.55});const applied=camera.position.clone();await commit();assert(camera.position.distanceTo(applied)<1e-8,'release keeps camera');
mode='zoom';assert(begin({x:.5,y:.5},'@view','hand'));move({x:.5,y:10});assert(Math.abs(camera.position.distanceTo(target)-30)<1e-8,'zoom max bounded');move({x:.5,y:-10});assert(Math.abs(camera.position.distanceTo(target)-2.5)<1e-8,'zoom min bounded');await commit();
assert.equal(writes,0,'camera gestures do not create component edits');assert.deepEqual(orbit.target.toArray(),target.toArray());
console.log('PASS: real Three.js camera orbit/zoom, radius and zoom limits, cancellation restore, confirmed release, and no component writes.');
