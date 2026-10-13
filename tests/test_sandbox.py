"""Tests for the execution-backend sandbox seam (tools/sandbox.py)."""
import os
import shlex
import subprocess
import sys

import pytest

import config
from tools import sandbox
from tools.sandbox import LocalBackend, DockerBackend, _RefusingBackend


@pytest.fixture(autouse=True)
def reset_backend_cache():
    """Each test gets a clean backend/availability cache."""
    sandbox._backend = None
    sandbox._docker_available = None
    yield
    sandbox._backend = None
    sandbox._docker_available = None


# ---- LocalBackend: real execution -----------------------------------------

def test_local_shell_runs():
    res = LocalBackend().run_shell("echo hello", timeout=10)
    assert res["success"] is True
    assert res["stdout"] == "hello"
    assert res["returncode"] == 0


def test_local_python_runs():
    res = LocalBackend().run_python("print(40 + 2)", timeout=10)
    assert res["success"] is True
    assert res["stdout"] == "42"


def test_local_shell_nonzero_exit():
    res = LocalBackend().run_shell("exit 3", timeout=10)
    assert res["success"] is False
    assert res["returncode"] == 3


def test_local_shell_timeout():
    argv = [sys.executable, "-c", "import time; time.sleep(5)"]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    res = LocalBackend().run_shell(command, timeout=1)
    assert res["success"] is False
    assert "timed out" in res["stderr"]


# ---- DockerBackend: argv assembly (no daemon needed) ----------------------

def test_docker_shell_argv_has_isolation_flags():
    b = DockerBackend()
    argv = b.shell_argv("ls -la", timeout=30)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network" in argv and "none" in argv          # default no network
    assert "--cap-drop" in argv and "ALL" in argv
    assert "--security-opt" in argv and "no-new-privileges" in argv
    assert "--memory" in argv and "--cpus" in argv and "--pids-limit" in argv
    assert argv[-3:] == ["bash", "-c", "ls -la"]
    assert b.image in argv


def test_docker_python_argv_uses_python3_c():
    argv = DockerBackend().python_argv("print(1)", timeout=10)
    assert argv[-3:] == ["python3", "-c", "print(1)"]


def test_docker_mounts_single_workdir():
    b = DockerBackend()
    argv = b.shell_argv("pwd", timeout=10)
    assert "-w" in argv and "/work" in argv
    mount = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"][0]
    assert mount.endswith(":/work")


# ---- Selection + fallback policy ------------------------------------------

def test_get_backend_local_by_default(monkeypatch):
    monkeypatch.setattr(config, "EXECUTION_BACKEND", "local")
    assert isinstance(sandbox.get_backend(refresh=True), LocalBackend)


def test_docker_unavailable_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(config, "EXECUTION_BACKEND", "docker")
    monkeypatch.setattr(config, "SANDBOX_REQUIRE", False)
    monkeypatch.setattr(sandbox, "docker_available", lambda refresh=False: False)
    assert isinstance(sandbox.get_backend(refresh=True), LocalBackend)


def test_docker_required_but_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "EXECUTION_BACKEND", "docker")
    monkeypatch.setattr(config, "SANDBOX_REQUIRE", True)
    monkeypatch.setattr(sandbox, "docker_available", lambda refresh=False: False)
    backend = sandbox.get_backend(refresh=True)
    assert isinstance(backend, _RefusingBackend)
    res = backend.run_shell("echo hi", timeout=5)
    assert res["success"] is False and "refused" in res["stderr"].lower()


def test_docker_available_selects_docker(monkeypatch):
    monkeypatch.setattr(config, "EXECUTION_BACKEND", "docker")
    monkeypatch.setattr(sandbox, "docker_available", lambda refresh=False: True)
    assert isinstance(sandbox.get_backend(refresh=True), DockerBackend)


# ---- bash.py still works through the seam ---------------------------------

def test_bash_run_unchanged_shape(monkeypatch):
    monkeypatch.setattr(config, "EXECUTION_BACKEND", "local")
    from tools import bash
    res = bash.run("echo via_bash")
    assert set(res.keys()) == {"stdout", "stderr", "returncode", "success"}
    assert res["stdout"] == "via_bash"
