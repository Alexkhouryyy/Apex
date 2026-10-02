"""Prepare local weights and the pinned NOMAD stack before going offline.

Native Ollama uses its own port/model folder. NOMAD is optional and requires
Docker Linux containers. No Windows services, registry settings or .env edits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
from zipfile import ZipFile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import apocalypse


def ensure_ollama(home, base, offline=False):
    base = apocalypse.loopback_url(base)
    with httpx.Client(trust_env=False, timeout=1) as client:
        try:
            response = client.get(base+'/api/tags')
            if response.status_code == 200:
                if not offline:
                    # Downloading into a daemon we did not launch could fill C:.
                    raise RuntimeError('Apocalypse Ollama is already running. Stop its offline launcher before preparing new downloads.')
                return None
        except httpx.HTTPError:
            pass
    executable = shutil.which('ollama')
    if not executable:
        raise RuntimeError('Install Ollama from https://ollama.com first.')
    home.mkdir(parents=True, exist_ok=True)
    (home/'models').mkdir(exist_ok=True)
    address = urlsplit(base)
    if address.path not in ('', '/'):
        raise RuntimeError('Use the base Ollama address, without /v1.')
    env = dict(os.environ)
    env.update(OLLAMA_HOST=address.netloc, OLLAMA_MODELS=str(home/'models'),
               OLLAMA_NO_CLOUD='1', OLLAMA_CONTEXT_LENGTH='16384',
               OLLAMA_NUM_PARALLEL='1')
    for key in list(env):
        if key.lower() in {'http_proxy', 'https_proxy', 'all_proxy'}:
            env.pop(key)
    with (home/'ollama.log').open('ab') as log:
        process = subprocess.Popen([executable, 'serve'], env=env, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    try:
        deadline = time.monotonic()+30
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError('Local Ollama stopped. Check the Apocalypse ollama.log file.')
                try:
                    if client.get(base+'/api/tags').status_code==200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(.5)
        raise RuntimeError('Local Ollama did not become ready within 30 seconds.')
    except BaseException:
        process.terminate()
        process.wait(timeout=10)
        raise


def stop_owned(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def prepare_model(home, base, name):
    if ':cloud' in name.lower() or not name or len(name)>160:
        raise RuntimeError('Choose a local model, not an Ollama cloud alias.')
    home.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(home).free<8*1024**3:
        raise RuntimeError('Keep at least 8 GB free on the offline library drive before preparing a model.')
    process = ensure_ollama(home, base)
    try:
        print(f'Downloading {name} into {home / "models"}. Initial preparation requires internet.', flush=True)
        with httpx.Client(trust_env=False, timeout=httpx.Timeout(1800, connect=10)) as client:
            with client.stream('POST', base+'/api/pull', json={'model': name, 'stream': True}) as response:
                response.raise_for_status()
                last=-1
                for line in response.iter_lines():
                    if not line:
                        continue
                    row=json.loads(line)
                    if row.get('error'):
                        raise RuntimeError(row['error'])
                    if row.get('total'):
                        progress=int(row.get('completed',0)/row['total']*100)//10*10
                        if progress!=last:
                            print(f'Model download: {progress}%', flush=True)
                            last=progress
            apocalypse.verify_model('ollama/'+name, base+'/v1')
            result=client.post(base+'/api/generate', json={'model':name, 'prompt':'Reply with READY only.',
                'think':False, 'stream':False, 'options':{'num_predict':16}, 'keep_alive':0})
            result.raise_for_status()
            if not result.json().get('response','').strip():
                raise RuntimeError('Model downloaded but did not produce a local test response.')
        (home/'prepared-model.json').write_text(json.dumps({'model':name,'prepared_at':int(time.time()),
            'local_response_tested':True},indent=2)+'\n',encoding='utf-8')
        print('Local model downloaded and answered the preparation test.',flush=True)
    finally:
        stop_owned(process)


def nomad_files(home):
    """Build original NOMAD source; keep generated secrets/data outside Apex Git."""
    manifest=json.loads((ROOT/'integrations/project-nomad/UPSTREAM.json').read_text())
    archive=ROOT/'integrations/project-nomad-source.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest['archive_sha256']:
        raise RuntimeError('Pinned NOMAD source archive changed. Stopped before extracting or executing it.')
    folder=home/'nomad'
    folder.mkdir(parents=True,exist_ok=True)
    source=folder/('source-'+manifest['revision'][:12])
    if not source.exists():
        source.mkdir()
        with ZipFile(archive) as z:
            for row in z.infolist():
                target=(source/row.filename).resolve()
                target.relative_to(source.resolve())
                if row.external_attr>>16 & 0o170000==0o120000:
                    raise RuntimeError('Linked source entries are not supported.')
            z.extractall(source)
    # Do not execute an old, interrupted or locally modified source extraction.
    with ZipFile(archive) as z:
        for row in z.infolist():
            if row.is_dir():
                continue
            target=(source/row.filename).resolve()
            target.relative_to(source.resolve())
            if not target.is_file() or hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(z.read(row)).digest():
                raise RuntimeError('Extracted NOMAD source changed. Preserve and review it before building.')
    envfile=folder/'.env'
    if not envfile.exists():
        envfile.write_text('APP_KEY='+secrets.token_urlsafe(32)+'\nDB_PASSWORD='+secrets.token_urlsafe(32)+
                          '\nMYSQL_ROOT_PASSWORD='+secrets.token_urlsafe(32)+'\n',encoding='utf-8')
    storage=(folder/'storage').as_posix()
    for directory in ('storage','mysql','redis'):
        (folder/directory).mkdir(exist_ok=True)
    services={
      'admin':{'image':'apex-nomad:'+manifest['revision'][:12],
        'build':{'context':source.as_posix(),'args':{'VCS_REF':manifest['revision'],'VERSION':manifest['version']}},
        'container_name':'nomad_admin','restart':'unless-stopped','ports':['127.0.0.1:8080:8080'],
        'extra_hosts':['host.docker.internal:host-gateway'],
        'volumes':[storage+':/app/storage','/var/run/docker.sock:/var/run/docker.sock','apex-nomad-shared:/app/update-shared'],
        'environment':{'NODE_ENV':'production','PORT':'8080','HOST':'0.0.0.0','URL':'http://127.0.0.1:8080',
          'APP_KEY':'${APP_KEY}','NOMAD_STORAGE_PATH':storage,'DB_HOST':'mysql','DB_PORT':'3306',
          'DB_DATABASE':'nomad','DB_NAME':'nomad','DB_USER':'nomad_user','DB_PASSWORD':'${DB_PASSWORD}',
          'DB_SSL':'false','REDIS_HOST':'redis','REDIS_PORT':'6379','LOG_LEVEL':'info'},
        'depends_on':{'mysql':{'condition':'service_healthy'},'redis':{'condition':'service_healthy'}}},
      'mysql':{'image':'mysql:8.0','container_name':'nomad_mysql','restart':'unless-stopped',
        'environment':{'MYSQL_ROOT_PASSWORD':'${MYSQL_ROOT_PASSWORD}','MYSQL_DATABASE':'nomad',
          'MYSQL_USER':'nomad_user','MYSQL_PASSWORD':'${DB_PASSWORD}'},
        'volumes':[(folder/'mysql').as_posix()+':/var/lib/mysql'],
        'healthcheck':{'test':['CMD','mysqladmin','ping','-h','localhost'],'interval':'10s','retries':15}},
      'redis':{'image':'redis:7-alpine','container_name':'nomad_redis','restart':'unless-stopped',
        'volumes':[(folder/'redis').as_posix()+':/data'],
        'healthcheck':{'test':['CMD','redis-cli','ping'],'interval':'10s','retries':10}}}
    compose=folder/'compose.json'
    compose.write_text(json.dumps({'name':'apex-apocalypse-nomad','services':services,
                                  'volumes':{'apex-nomad-shared':{}}},indent=2)+'\n',encoding='utf-8')
    return folder,compose


def nomad(home, prepare=False):
    executable=shutil.which('docker')
    if not executable:
        raise RuntimeError('NOMAD needs Docker with Linux containers. Read docs/APEX_APOCALYPSE.md; native offline Apex does not need Docker.')
    info=subprocess.run([executable,'info','--format','{{.OSType}}'],capture_output=True,text=True,timeout=15,check=True)
    if info.stdout.strip()!='linux':
        raise RuntimeError('Switch Docker to Linux containers before preparing NOMAD.')
    folder,compose=nomad_files(home)
    command=[executable,'compose','--project-directory',str(folder),'-f',str(compose)]
    subprocess.run(command+['config','--quiet'],check=True)
    if prepare:
        print('Building pinned NOMAD source and downloading its database images. Configure Docker image storage on D: first.',flush=True)
        subprocess.run(command+['pull','mysql','redis'],check=True)
        subprocess.run(command+['build','admin'],check=True)
    subprocess.run(command+['up','-d','--pull','never','--no-build'],check=True)
    print('NOMAD started from cached images at http://127.0.0.1:8080. Download desired apps and content while online.',flush=True)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--nomad',action='store_true',help='Prepare and start full NOMAD (Docker required)')
    p.add_argument('--nomad-start',action='store_true',help='Start cached NOMAD without pulls or builds')
    p.add_argument('--model',default=apocalypse.model_name())
    args=p.parse_args()
    home=apocalypse.library_root()
    if args.nomad or args.nomad_start:
        nomad(home,prepare=args.nomad)
        return
    if apocalypse.enabled():
        raise RuntimeError('Model downloads are a preparation step. Run this setup outside Apocalypse mode.')
    (home/'documents').mkdir(parents=True,exist_ok=True)
    prepare_model(home,apocalypse.ollama_url(),args.model)
    print('Ready for Start-Apex-Apocalypse.cmd. Full library/maps/courses are optional NOMAD downloads.',flush=True)


if __name__=='__main__':
    try:
        main()
    except (RuntimeError,OSError,ValueError,httpx.HTTPError,subprocess.CalledProcessError,KeyboardInterrupt) as exc:
        print(f'Preparation stopped: {exc}',file=sys.stderr)
        raise SystemExit(1)
