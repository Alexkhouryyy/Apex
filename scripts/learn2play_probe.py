"""Probe a pinned external Learn2Play checkout; no upstream code is redistributed.

This only checks the game interface. It never measures agent learning/transfer.
The operator supplies a reviewed checkout. A subprocess is not a security sandbox.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PIN='5e5fdf9db65d6ab45314f2baa0f7bcddcfc75624'


def probe(repo, output):
    repo=repo.resolve()
    head=subprocess.run(['git','-C',str(repo),'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
    if head!=PIN:raise ValueError('only the reviewed upstream snapshot may be probed')
    dirty=subprocess.run(['git','-C',str(repo),'status','--porcelain','--untracked-files=no'],check=True,capture_output=True,text=True).stdout
    if dirty:raise ValueError('upstream source has local modifications')
    if output.resolve().is_relative_to(repo):raise ValueError('output must remain outside the upstream checkout')
    seeds=json.loads((repo/'default_seeds.json').read_text())['seeds']
    results=[]
    with tempfile.TemporaryDirectory() as work:
        for game,seed in seeds.items():
            # Strip host credentials; upstream uses only stdlib game imports.
            env={'PATH':os.environ.get('PATH',''),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
            try:
                result=subprocess.run([sys.executable,'-I',str(repo/'play.py'),game,'--seed',str(seed),'--episode','1','--lang','en'],
                    input='status\nvalid\nscore\nquit\n',text=True,capture_output=True,cwd=work,env=env,timeout=10)
                results.append({'game':game,'seed':seed,'returncode':result.returncode,'protocol_completed':result.returncode==0,
                                'output_sha':hashlib.sha256(result.stdout.encode()).hexdigest(),
                                'error':result.stderr[-300:] if result.returncode else None})
            except subprocess.TimeoutExpired:
                results.append({'game':game,'seed':seed,'protocol_completed':False,'error':'timeout'})
    report={'schema':'apex-learn2play-probe/v1','revision':head,'license':'not found; external checkout only',
            'real_model_calls':0,'agent_learning_measured':False,'games':results}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();report=probe(args.repo,args.output)
    print(f"{sum(r['protocol_completed'] for r in report['games'])}/{len(report['games'])} game protocols completed; no agent performance claim")


if __name__=='__main__':main()
