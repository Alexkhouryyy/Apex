'use strict';
const { ContainerModule } = require('@theia/core/shared/inversify');
const { BackendApplicationContribution, EarlyExpressMiddleware } = require('@theia/core/lib/node/backend-application');
const crypto = require('node:crypto');
const { WsRequestValidatorContribution } = require('@theia/core/lib/node/ws-request-validators');
const { PluginVSCodeEnvironment } = require('@theia/plugin-ext-vscode/lib/common/plugin-vscode-environment');
const { FileUri } = require('@theia/core/lib/common/file-uri');
const express = require('express');
const path = require('node:path');
const { PluginHostEnvironmentVariable } = require('@theia/plugin-ext/lib/common/plugin-protocol');
const secret = process.env.APEX_IDE_SECRET;
function authorized(req) {
  const given = req.headers['x-apex-ide-secret'];
  return typeof given === 'string' && typeof secret === 'string' && given.length === secret.length &&
    crypto.timingSafeEqual(Buffer.from(given), Buffer.from(secret));
}
module.exports = new ContainerModule(bind => {
  // Theia strips THEIA_* while forking the host. Set this after that pass so
  // its navigator shim does not defeat NODE_OPTIONS's genuinely absent API.
  bind(PluginHostEnvironmentVariable).toConstantValue({process(env) {
    env.THEIA_SUPPORT_NODE_GLOBAL_NAVIGATOR = 'true';
  }});
  bind(WsRequestValidatorContribution).toConstantValue({allowWsUpgrade: authorized});
  bind(BackendApplicationContribution).toDynamicValue(ctx => {
    let parentTimer;
    return {
      initialize() {
        if (!secret || !process.env.APEX_PARENT_PID) throw new Error('Start this IDE through Apex Code.');
        const extensions = ctx.container.get(PluginVSCodeEnvironment);
        const user = FileUri.create(process.env.APEX_IDE_USER_DIR);
        extensions.getUserExtensionsDirUri = async () => user.resolve('extensions');
        extensions.getDeploymentDirUri = async () => user.resolve('deployedPlugins');
        ctx.container.get(EarlyExpressMiddleware).handlers.push((req, res, next) => {
          if (!authorized(req)) return res.status(401).end('Open this IDE through Apex Code.');
          if (req.path === '/apex-ide-health') return res.json({engine:'apex-theia',version:'1.76.0'});
          next();
        });
        const parent = Number(process.env.APEX_PARENT_PID);
        parentTimer = setInterval(() => {try {process.kill(parent, 0);} catch {process.exit(0);}}, 2000);
        parentTimer.unref();
      },
      configure(app) {
        app.use('/webview',express.static(path.resolve(process.cwd(),'lib/webview/pre')));
      },
      onStop() {clearInterval(parentTimer);}
    };
  }).inSingletonScope();
});
module.exports.default = module.exports;
