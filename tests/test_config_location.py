"""Configuration belongs to Apex even when started from a different directory."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_config_ignores_malformed_dotenv_in_launch_directory(tmp_path):
    root=Path(__file__).resolve().parents[1]
    app=tmp_path/'application';app.mkdir()
    elsewhere=tmp_path/'unrelated';elsewhere.mkdir()
    shutil.copyfile(root/'config.py',app/'config.py')
    (app/'.env').write_text('APEX_CFG_LOCATION=apex\n',encoding='utf-8')
    (elsewhere/'.env').write_bytes(b'FOREIGN=contains\0null\n')
    env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(app),str(root))))
    env.pop('APEX_CFG_LOCATION',None)
    env.pop('PYTHON_DOTENV_DISABLED',None)
    code='import config,os; print(os.environ.get("APEX_CFG_LOCATION"))'
    result=subprocess.run([sys.executable,'-c',code],cwd=elsewhere,env=env,
                          capture_output=True,text=True,check=True,timeout=30)
    assert result.stdout.strip() == 'apex'
