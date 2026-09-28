from types import SimpleNamespace
import pytest
from agent.file_events import DEFAULT_IGNORES, FileEvents, ignored


@pytest.mark.parametrize('path', [r'C:\repo\.wrangler\state\db',r'C:\repo\node_modules\a.js',
    'package-lock.json','a/b/pnpm-lock.yaml','a/uv.lock','.git/index','x/.venv/lib/a.py',
    'x/NODE_MODULES/a.js','y/bun.lockb','x/build/out.js'])
def test_noise_is_ignored(path): assert ignored(path,DEFAULT_IGNORES)


def test_source_and_custom_globs():
    assert not ignored('repo/src/app.py',DEFAULT_IGNORES)
    assert not ignored('repo/src/building.py',DEFAULT_IGNORES)
    assert ignored('repo/generated/data.json',DEFAULT_IGNORES+('**/generated/**',))


def test_filter_before_log_moves_and_dedup():
    entries=[];clock=[0]
    f=FileEvents(SimpleNamespace(add=lambda *args:entries.append(args)),clock=lambda:clock[0])
    def send(kind,src,dest='',directory=False):
        f.handle(SimpleNamespace(event_type=kind,src_path=src,dest_path=dest,is_directory=directory))
    send('modified','repo/node_modules/a.js');send('modified','repo/.wrangler/a');send('modified','repo',directory=True)
    assert not entries
    send('modified','repo/app.py');send('modified','repo/app.py')
    assert entries==[('file','Modified: repo/app.py')]
    clock[0]=1.1;send('modified','repo/app.py');assert len(entries)==2
    send('moved','repo/node_modules/a','repo/src/a');assert entries[-1][1]=='Created: repo/src/a'
    send('moved','repo/src/a','repo/node_modules/a');assert entries[-1][1]=='Deleted: repo/src/a'
    send('moved','repo/src/a','repo/src/b');assert entries[-1][1]=='Moved: repo/src/a → repo/src/b'
