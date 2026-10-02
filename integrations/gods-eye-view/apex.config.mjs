import { defineConfig, loadEnv } from 'vite';
import { createBrowserViteConfig } from './build/vite.js';
import { localProviderPlugins } from './server/providers/local.js';
import { apiNotFoundPlugin } from './server/standalone/api-not-found.js';
import { timingSafeEqual } from 'node:crypto';
import { cp } from 'node:fs/promises';
import path from 'node:path';

const base = '/world/engine/';
const requestBridge = `(() => {
 const prefix = '/world/engine/';
 const rewrite = value => {
   try { const u = new URL(value, location.href);
     if (u.origin === location.origin && !u.pathname.startsWith(prefix)) {
       u.pathname = prefix + u.pathname.replace(/^\\//, ''); return u.href;
     }
   } catch (_) {} return value;
 };
 const original = window.fetch.bind(window);
 window.fetch = (input, options) => {
   if (input instanceof Request) {
     const url = rewrite(input.url);
     return original(url === input.url ? input : new Request(url, input), options);
   }
   return original(rewrite(input), options);
 };
 const open = XMLHttpRequest.prototype.open;
 XMLHttpRequest.prototype.open = function(method, url, ...args) { return open.call(this, method, rewrite(url), ...args); };
})();`;

function apexIntegration(command) {
  return {
    name: 'apex-world-integration',
    async closeBundle() {
      // vite-plugin-cesium includes the URL base in its filesystem destination.
      // Our private server strips that URL base; stage the worker/assets tree at
      // the corresponding dist root as well, without changing upstream files.
      if (command === 'build') await cp(path.resolve('node_modules/cesium/Build/Cesium'),
        path.resolve('dist/cesium'), {recursive:true});
    },
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        return {html: html.replace('<head>', '<head><script>' + requestBridge + '</script>'),
          tags: [{tag:'script', attrs:{type:'module', src:'/apex-bridge.js'}, injectTo:'body'}]};
      },
    },
    configureServer(server) {
      const expected = Buffer.from(process.env.APEX_ENGINE_SECRET || '');
      if (expected.length < 32) throw new Error('Start this engine through Apex. Its private gateway credential is missing.');
      server.middlewares.use((req, res, next) => {
        const provided = Buffer.from(String(req.headers['x-apex-engine-secret'] || ''));
        if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
          res.statusCode = 403; res.end('Open World View through Apex.'); return;
        }
        delete req.headers['x-apex-engine-secret'];
        if (req.url === '/apex-health') {
          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify({engine:'gods-eye-view'})); return;
        }
        // Provider plugins intentionally mount /api while the browser is served
        // under a base path. Strip only that known gateway prefix for APIs.
        if (req.url?.startsWith(base + 'api/')) req.url = '/' + req.url.slice(base.length);
        next();
      });
    },
  };
}

export default defineConfig(({command, mode}) => {
  for (const [key, value] of Object.entries(loadEnv(mode, process.cwd(), ''))) {
    if (process.env[key] === undefined) process.env[key] = value;
  }
  const config = createBrowserViteConfig({
    plugins: [apexIntegration(command), ...localProviderPlugins(), apiNotFoundPlugin()],
    host:'127.0.0.1', port:Number(process.env.APEX_ENGINE_PORT || 4174), command,
    googleApiKey:process.env.GOOGLE_MAPS_API_KEY, cesiumToken:process.env.CESIUM_ION_TOKEN,
  });
  return {...config, base, server:{...config.server, strictPort:true, hmr:false,
    headers:{'X-Frame-Options':'SAMEORIGIN', 'Content-Security-Policy':"frame-ancestors 'self'"}}};
});
