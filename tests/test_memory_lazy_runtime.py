"""Text-only memory must work without importing the native embedding runtime."""
import asyncio
import os
import subprocess
import sys
import threading

from agent import longterm, mcp_server


def test_text_memory_and_staging_work_with_numpy_import_forbidden(tmp_path):
    code = '''
import sys
class NoNumerics:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'torch', 'sentence_transformers'}:
            raise AssertionError('Text memory imported ' + fullname)
sys.meta_path.insert(0, NoNumerics())
from agent import longterm, approvals
longterm.init_db()
approvals.init_db()
message = approvals.stage('remember', {'content': 'Use short replies.', 'kind': 'preference'})
assert 'STAGED' in message
assert not longterm.recall('short', semantic=False)
assert approvals.list_pending()[0]['payload']['content'] == 'Use short replies.'
assert 'numpy' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code],
        env={**os.environ, 'DB_PATH': str(tmp_path / 'memory.db')},
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_mcp_sync_tool_runs_off_protocol_thread(monkeypatch):
    from agent import skill_md
    main_thread = threading.get_ident()
    threads = []
    monkeypatch.setattr(skill_md, 'list_skills', lambda: [])
    def output(tool, text, **kwargs):
        threads.append(threading.get_ident())
        return text
    monkeypatch.setattr(mcp_server, '_out', output)
    asyncio.run(mcp_server.mcp.call_tool('skills', {}))
    assert threads and threads[0] != main_thread


def test_semantic_vector_helpers_still_compute_cosines():
    import numpy as np
    vector = np.array([1, 0], dtype=np.float32)
    blobs = [vector.tobytes(), np.array([0, 1], dtype=np.float32).tobytes(), None]
    assert longterm._cosine_scores(vector, blobs) == [1.0, 0.0, 0.0]
