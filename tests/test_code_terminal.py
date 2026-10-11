"""Persistent real shells without models; Windows uses ConPTY, Unix uses PTY."""
import os
import sys
import time
import psutil
from pathlib import Path
from types import SimpleNamespace
import pytest
from agent import code_terminal as terminal


def eventually(sid, text, timeout=8):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        data=terminal.read(sid)['data']
        if text in data:
            return data
        time.sleep(.03)
    raise AssertionError(f'Terminal did not print {text!r}: {terminal.read(sid)}')


def test_directory_changes_and_interactive_input_survive_commands(tmp_path):
    sid=99101
    (tmp_path/'sub').mkdir()
    try:
        terminal.start(sid,str(tmp_path),dict(os.environ))
        terminal.write(sid,'cd sub\r')
        command=f'"{sys.executable}" -u -c "from pathlib import Path; print(Path.cwd().name); print(\'answer=\'+input())"\r'
        terminal.write(sid,command)
        eventually(sid,'sub')
        terminal.write(sid,'forty-two\r')
        eventually(sid,'answer=forty-two')
        offset=terminal.read(sid)['offset']
        terminal.write(sid,'echo __NEXT_COMMAND__\r')
        eventually(sid,'__NEXT_COMMAND__')
        assert terminal.read(sid,offset)['offset']>=offset
        assert terminal.read(sid,-1)['reset'] is False
        terminal.resize(sid,30,100)
        assert terminal.read(sid)['running']
    finally:
        terminal.stop(sid)
    assert not terminal.read(sid)['running']


def test_close_ends_a_running_child_and_allows_a_fresh_shell(tmp_path):
    sid=99102
    try:
        terminal.start(sid,str(tmp_path),dict(os.environ))
        terminal.write(sid,f'"{sys.executable}" -u -c "import os,time; from pathlib import Path; Path(\'child.pid\').write_text(str(os.getpid())); time.sleep(60)"\r')
        deadline=time.monotonic()+8
        marker=tmp_path/'child.pid'
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.03)
        assert marker.exists(), 'Child process actually started (command echo is insufficient)'
        child=psutil.Process(int(marker.read_text()))
        assert terminal.stop(sid)['stopped']
        assert not terminal.read(sid)['running']
        deadline=time.monotonic()+5
        while child.is_running() and child.status()!=psutil.STATUS_ZOMBIE and time.monotonic()<deadline:
            time.sleep(.03)
        assert not child.is_running() or child.status()==psutil.STATUS_ZOMBIE
        terminal.start(sid,str(tmp_path),dict(os.environ))
        terminal.write(sid,'echo __RESTARTED__\r')
        eventually(sid,'__RESTARTED__')
    finally:
        terminal.stop(sid)


def test_windows_uses_cmd_and_passes_paths_and_size_without_shell_quoting(monkeypatch,tmp_path):
    called=[]
    fake=SimpleNamespace(spawn=lambda *a,**k:called.append((a,k)))
    monkeypatch.setitem(sys.modules,'winpty',SimpleNamespace(PtyProcess=fake))
    terminal._spawn(str(tmp_path/'path with spaces'),{'PATH':'test'},25,90,windows=True)
    assert called[0][0][0][1:]==['/d','/q']
    assert called[0][1]['cwd']==str(tmp_path/'path with spaces')
    assert called[0][1]['dimensions']==(25,90)


def test_internal_windows_wakeup_does_not_end_the_output_reader():
    packets=iter(['','real output'])
    def read(size):
        try:
            return next(packets)
        except StopIteration:
            raise EOFError
    process=SimpleNamespace(pid=123,read=read,isalive=lambda:True)
    shell=terminal.Shell(process)
    shell.reader.join(timeout=2)
    assert shell.snapshot(0)['data']=='real output'


@pytest.mark.parametrize('rows,cols',[(0,80),(24,0),(True,80),(24,1000),('24',80)])
def test_invalid_terminal_sizes_cannot_start_a_process(tmp_path,rows,cols):
    with pytest.raises(ValueError):
        terminal.start(99103,str(tmp_path),{},rows,cols)
