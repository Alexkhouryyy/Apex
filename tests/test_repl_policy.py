"""The persistent kernel must not silently escape the execution policy."""
from unittest.mock import Mock

import pytest

import config
from tools import repl


@pytest.fixture
def kernel(monkeypatch):
    client = Mock()
    client.execute.return_value = "execution-id"
    client.get_iopub_msg.return_value = {
        "parent_header": {"msg_id": "execution-id"},
        "msg_type": "status", "content": {"execution_state": "idle"},
    }
    manager = Mock()
    manager.client.return_value = client
    factory = Mock(return_value=manager)
    monkeypatch.setattr("jupyter_client.manager.KernelManager", factory)
    monkeypatch.setattr(repl, "_drain", lambda *a, **k: [])
    monkeypatch.setattr(repl, "_km", None)
    monkeypatch.setattr(repl, "_kc", None)
    return factory, manager, client


@pytest.mark.parametrize("backend,required", [
    ("docker", True), ("docker", False), ("local", True), ("unknown", False),
])
@pytest.mark.parametrize("operation", ["execute", "reset"])
def test_isolated_policy_never_starts_host_kernel(monkeypatch, kernel, backend, required, operation):
    factory, manager, client = kernel
    monkeypatch.setattr(config, "EXECUTION_BACKEND", backend)
    monkeypatch.setattr(config, "SANDBOX_REQUIRE", required)
    with pytest.raises(RuntimeError, match="Host execution is refused"):
        repl.execute("print('test')") if operation == "execute" else repl.reset()
    factory.assert_not_called()
    manager.start_kernel.assert_not_called()
    client.execute.assert_not_called()


def test_policy_change_blocks_existing_host_kernel(monkeypatch, kernel):
    factory, manager, client = kernel
    monkeypatch.setattr(config, "EXECUTION_BACKEND", "local")
    monkeypatch.setattr(config, "SANDBOX_REQUIRE", False)
    assert repl.execute("x = 1")["error"] is None
    client.execute.reset_mock()
    monkeypatch.setattr(config, "EXECUTION_BACKEND", "docker")
    with pytest.raises(RuntimeError, match="Host execution is refused"):
        repl.execute("x += 1")
    client.execute.assert_not_called()
    assert factory.call_count == 1


def test_local_kernel_is_persistent_and_can_reset(monkeypatch, kernel):
    factory, manager, client = kernel
    monkeypatch.setattr(config, "EXECUTION_BACKEND", "local")
    monkeypatch.setattr(config, "SANDBOX_REQUIRE", False)
    repl.execute("x = 1")
    repl.execute("x += 1")
    assert factory.call_count == 1
    repl.reset()
    manager.shutdown_kernel.assert_called_once_with(now=True)
    assert factory.call_count == 2
