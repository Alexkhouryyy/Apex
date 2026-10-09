import multiprocessing
import time
from pathlib import Path
from agent.state_lock import writer


def increment(path,loops):
    for _ in range(loops):
        with writer(path):
            file=Path(path);current=int(file.read_text())
            time.sleep(.001)
            file.write_text(str(current+1))


def test_hermes_kernel_lease_serializes_processes(tmp_path):
    path=tmp_path/'counter';path.write_text('0')
    context=multiprocessing.get_context('spawn')
    workers=[context.Process(target=increment,args=(str(path),10)) for _ in range(3)]
    for p in workers:p.start()
    for p in workers:
        p.join(15)
        assert p.exitcode==0
    assert path.read_text()=='30'
