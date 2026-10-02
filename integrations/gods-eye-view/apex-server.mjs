import { timingSafeEqual, createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { build, preview } from 'vite';
import makeConfig from './apex.config.mjs';
import { localProviderPlugins } from './server/providers/local.js';
import { apiNotFoundPlugin } from './server/standalone/api-not-found.js';

const parent = Number(process.env.APEX_PARENT_PID);
const secret = Buffer.from(process.env.APEX_ENGINE_SECRET || '');
const prefix = '/world/engine/';
if (secret.length < 32 || !Number.isInteger(parent) || parent < 1) {
  throw new Error('Start World View through Apex.');
}
const fingerprint = () => createHash('sha256').update(JSON.stringify([
  process.env.GOOGLE_MAPS_API_KEY || '', process.env.CESIUM_ION_TOKEN || '',
  readFileSync('apex-bridge.js','utf8'), readFileSync('apex.config.mjs','utf8'),
])).digest('hex');
let version = '', rebuilding = false, restartPromise, closing = false;
function buildStamp() {
  try {return JSON.parse(readFileSync('APEX_BUILD.json','utf8'));} catch (_) {return {};}
}
async function rebuild() {
  if (restartPromise) return restartPromise;
  restartPromise = (async () => {
    rebuilding = true;
    try {
      const config = makeConfig({command:'build', mode:'production'});
      await build({...config, configFile:false, build:{...config.build, emptyOutDir:false}});
      version = String(Date.now());
      writeFileSync('APEX_BUILD.json', JSON.stringify({version, fingerprint:fingerprint()}) + '\n');
    } finally {rebuilding = false; restartPromise = null;}
  })();
  return restartPromise;
}
// Load the engine's local provider configuration without opening a dev server.
makeConfig({command:'build', mode:'production'});
const stamp = buildStamp();
if (!existsSync('dist/index.html') || stamp.fingerprint !== fingerprint()) await rebuild();
else version = stamp.version;
const providers = [...localProviderPlugins(), apiNotFoundPlugin()];
function gateway(req,res,next) {
  const provided = Buffer.from(String(req.headers['x-apex-engine-secret'] || ''));
  if (provided.length !== secret.length || !timingSafeEqual(provided,secret)) {
    res.statusCode = 403; res.end('Open World View through Apex.'); return;
  }
  delete req.headers['x-apex-engine-secret'];
  if (rebuilding) {res.statusCode = 503; res.end('Applying provider settings…'); return;}
  if (req.url === '/apex-health') {
    res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify({engine:'gods-eye-view'})); return;
  }
  if (!req.url?.startsWith(prefix)) {res.statusCode = 404; res.end('Not found'); return;}
  if (req.url === prefix + 'apex-version') {
    res.setHeader('Content-Type','application/json'); res.setHeader('Cache-Control','no-store');
    res.end(JSON.stringify({version})); return;
  }
  if (req.url.startsWith(prefix + 'api/')) req.url = '/' + req.url.slice(prefix.length);
  next();
}
const server = await preview({
  configFile:false, base:prefix,
  plugins:[{name:'apex-production-providers', configurePreviewServer(server) {
    server.middlewares.use(gateway);
    const owner = {middlewares:server.middlewares, httpServer:server.httpServer, restart:rebuild};
    // Install the same local provider handlers on Apex's private server. This
    // includes the local-only key setup handler, with Apex's owner/origin guard.
    for (const plugin of providers) plugin.configureServer?.(owner);
  }}],
  preview:{host:'127.0.0.1', port:Number(process.env.APEX_ENGINE_PORT), strictPort:true,
    headers:{'X-Frame-Options':'SAMEORIGIN','Content-Security-Policy':"frame-ancestors 'self'"}},
});
const httpServer = server.httpServer;
console.log('Apex world engine ready (production assets, original provider middleware)');
async function stop() {
  if (closing) return;
  closing = true; clearInterval(watchdog);
  httpServer.closeAllConnections();
  await new Promise(resolve => httpServer.close(resolve));
  for (const plugin of providers) await plugin.closeBundle?.();
  process.exit(0);
}
const watchdog = setInterval(() => {
  try {process.kill(parent,0);} catch (_) {void stop();}
},2000);
process.on('SIGTERM',() => void stop());
process.on('SIGINT',() => void stop());
