import { build } from 'vite';
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import makeConfig from './apex.config.mjs';
const config = makeConfig({command:'build',mode:'production'});
await build({...config,configFile:false});
const fingerprint = createHash('sha256').update(JSON.stringify([
  process.env.GOOGLE_MAPS_API_KEY || '', process.env.CESIUM_ION_TOKEN || '',
  readFileSync('apex-bridge.js','utf8'), readFileSync('apex.config.mjs','utf8'),
])).digest('hex');
writeFileSync('APEX_BUILD.json',JSON.stringify({version:String(Date.now()),fingerprint})+'\n');
console.log('Apex World View production assets ready.');
