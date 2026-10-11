"""Run the real IDE browser acceptance in a disposable project, without model calls."""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'local-ide-acceptance-owner'
WORK_FOLDER = 'work with spaces ü'


def serve(state, port):
    sys.path.insert(0, str(ROOT))
    os.environ.update(DB_PATH=str(state/'brain.db'), APEX_STUDY_DIR=str(state/'study'),
        VAULT_PATH=str(state/'vault'), SANDBOX_WORKDIR=str(state/'sandbox'),
        DASHBOARD_TOKEN=TOKEN, ANTHROPIC_API_KEY='sk-ant-placeholder-for-local-ide-test')
    from agent import code_studio, longterm, notify, work, work_agent, work_engines
    work.WORK_DIR = state/WORK_FOLDER
    work_engines.check = lambda *a, **k: {'ok':False,'why':'No model account used in IDE acceptance','how':''}
    work_engines.binary = lambda *a, **k: None
    work_agent.available = lambda: {e:'No model account used in IDE acceptance' for e in code_studio.ENGINES}
    longterm._embed = lambda text: None
    notify.notify = lambda *a, **k: None
    longterm.init_db()
    code_studio.init_db()
    code_studio.add_project(str(state/'IDE project with spaces'), 'IDE acceptance')
    from dashboard.server import app
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='error')


def fixture(state):
    project = state/'IDE project with spaces'
    project.mkdir()
    files = {'README.md':'# IDE acceptance\n',
        'language.py':'def twice(value: int) -> int:\n    return value * 2\n\nanswer = twice(21)\nfinal = answer + 1\nprint(final)\n',
        'language.js':'const first = 21;\nconst answer = first * 2;\nconst final = answer + 1;\nconsole.log(final);\n',
        'language.ts':'const label: string = 42;\nfunction greet(name: string): string { return `Hello ${name}`; }\nconst message = greet("Apex");\nconsole.log(message);\n',
        'terminal-input.js':'const readline = require("node:readline").createInterface({input:process.stdin,output:process.stdout});\nreadline.question("Your name: ", name => { console.log("hello " + name); readline.close(); });\n',
        '.vscode/tasks.json':json.dumps({'version':'2.0.0','tasks':[
            {'label':'Acceptance task','type':'process','command':'node','args':['-e',
             'require("node:fs").writeFileSync("task-result.txt", "task works")'],
             'options':{'cwd':'${workspaceFolder}'},'problemMatcher':[]}]},indent=2),
        '.vscode/launch.json':json.dumps({'version':'0.2.0','configurations':[
            {'name':'Python acceptance','type':'debugpy','request':'launch','program':'${workspaceFolder}/language.py','console':'internalConsole','justMyCode':True},
            {'name':'Node acceptance','type':'pwa-node','request':'launch','program':'${workspaceFolder}/language.js','console':'internalConsole'}]},indent=2)}
    for name, text in files.items():
        path = project/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    for arguments in (['init','-q','-b','main'],['config','core.autocrlf','false'],['add','-A'],
                      ['-c','user.name=IDE acceptance','-c','user.email=ide@localhost','commit','-qm','IDE fixture']):
        subprocess.run(['git',*arguments], cwd=project, check=True)
    extension = state/'acceptance.vsix'
    with zipfile.ZipFile(extension, 'w') as archive:
        archive.writestr('extension.vsixmanifest','<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"><Metadata><Identity Id="echo" Version="1.0.0" Publisher="apex-acceptance"/><DisplayName>Acceptance Echo</DisplayName></Metadata></PackageManifest>')
        archive.writestr('extension/package.json',json.dumps({'publisher':'apex-acceptance','name':'echo','version':'1.0.0',
            'engines':{'vscode':'^1.80.0'},'main':'./extension.js','activationEvents':['*'],
            'contributes':{'commands':[{'command':'apex.acceptance.echo','title':'Acceptance Echo'},
                {'command':'apex.acceptance.webview','title':'Acceptance Webview'}]}}))
        archive.writestr('extension/extension.js', "const vscode=require('vscode');exports.activate=c=>{c.subscriptions.push(vscode.commands.registerCommand('apex.acceptance.echo',()=> 'extension host works'),vscode.commands.registerCommand('apex.acceptance.webview',()=>{const p=vscode.window.createWebviewPanel('apexAcceptance','Acceptance Webview',vscode.ViewColumn.One,{enableScripts:true});p.webview.html='<html><body><h1>Extension webview works</h1><button onclick=\"this.textContent=42\">Run</button></body></html>';}));};")
    return extension


def main(output):
    import httpx
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='apex-ide-') as temporary:
        state = Path(temporary)
        extension = fixture(state)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with (output/'server.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--serve',
                '--state',str(state),'--port',str(port)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            sid = None
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=120,
                                  headers={'Authorization':'Bearer '+TOKEN}) as client:
                    deadline = time.monotonic()+60
                    while time.monotonic()<deadline and process.poll() is None:
                        try:
                            if client.get('/health').status_code==200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.2)
                    else:
                        raise RuntimeError(f'Acceptance server failed. See {output}/server.log')
                    response = client.post('/api/code/workspaces',json={'project_id':1})
                    response.raise_for_status()
                    sid = response.json()['id']
                    env = {**os.environ,'APEX_IDE_TEST_URL':str(client.base_url).rstrip('/'),
                        'APEX_IDE_TEST_SESSION':str(sid),'APEX_IDE_TEST_TOKEN':TOKEN,
                        'APEX_IDE_TEST_VSIX':extension.as_uri().replace('file:','local-file:',1),
                        'APEX_IDE_TEST_OUTPUT':str(output.resolve())}
                    try:
                        subprocess.run([shutil.which('node'),str(ROOT/'scripts/check_code_ide_browser.cjs')],
                                       env=env,cwd=ROOT,check=True,timeout=300)
                    finally:
                        runtime_log = state/WORK_FOLDER/'ide'/str(sid)/'server.log'
                        if runtime_log.exists():
                            shutil.copyfile(runtime_log, output/'ide-server.log')
                        if sid is not None:
                            client.delete(f'/api/code/sessions/{sid}/ide')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--state',type=Path)
    parser.add_argument('--port',type=int)
    parser.add_argument('--output-dir',type=Path,default=Path(tempfile.gettempdir())/'apex-ide-acceptance')
    args=parser.parse_args()
    if args.serve:
        serve(args.state,args.port)
    else:
        main(args.output_dir)
