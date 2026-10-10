'use strict';
const { ContainerModule } = require('@theia/core/shared/inversify');
const { FrontendApplicationContribution } = require('@theia/core/lib/browser/frontend-application-contribution');
const { ApplicationShell } = require('@theia/core/lib/browser/shell/application-shell');
const { Saveable } = require('@theia/core/lib/browser/saveable');
const { EditorManager } = require('@theia/editor/lib/browser/editor-manager');
const URI = require('@theia/core/lib/common/uri').default;
const { WebviewEnvironment } = require('@theia/plugin-ext/lib/main/browser/webview/webview-environment');
const { EnvVariablesServer } = require('@theia/core/lib/common/env-variables');
module.exports = new ContainerModule(bind => {
  bind(FrontendApplicationContribution).toDynamicValue(ctx => {
    const shell = ctx.container.get(ApplicationShell);
    let timer, listener;
    const send = message => {if (parent !== window) parent.postMessage({channel:'apex-ide',...message},location.origin);};
    const dirty = () => shell.widgets.some(w => Saveable.isDirty(w));
    return {
      async onStart() {
        const webviews=ctx.container.get(WebviewEnvironment);
        const port=await ctx.container.get(EnvVariablesServer).getValue('APEX_IDE_WEBVIEW_PORT');
        const endpoint=await webviews.externalEndpointUrl();
        webviews.externalEndpointUrl=async()=>endpoint.withAuthority(location.hostname+':'+port.value).withScheme('http');
        const publish = () => send({type:'state',dirty:dirty()});
        timer=setInterval(publish,400);
        listener=async e => {
          if(e.source!==parent || e.origin!==location.origin || e.data?.channel!=='apex-ide')return;
          const id=e.data.id;
          try {
            if(e.data.type==='save') {
              await shell.saveAll();
              if(dirty()) throw new Error('Some IDE files are still unsaved. Resolve the save dialog in the IDE.');
              send({type:'saved',id,ok:true});
            } else if(e.data.type==='open-file' && typeof e.data.uri==='string') {
              await ctx.container.get(EditorManager).open(new URI(e.data.uri));
            }
          } catch(error) {send({type:'saved',id,ok:false,error:error.message});}
          publish();
        };
        window.addEventListener('message',listener);
        window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
        send({type:'ready',dirty:dirty()});
      },
      onStop() {clearInterval(timer);window.removeEventListener('message',listener);}
    };
  }).inSingletonScope();
});
module.exports.default = module.exports;
