import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const python=process.env.APEX_PYTHON || (process.platform==='win32'?'python':'python3');
const result=spawnSync(python,[fileURLToPath(new URL('./plugins.py',import.meta.url))],{stdio:'inherit'});
if(result.error) console.error('Python is needed to download the IDE extensions:',result.error.message);
process.exit(result.status ?? 1);
