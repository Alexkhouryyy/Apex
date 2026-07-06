"""Execution backends — run shell commands and Python on the host OR in a sandbox.

Apex runs bash and auto-executes ``run_python`` (the autonomous cortex does this
without asking). On an always-on, internet-exposed host that is the single largest
blast radius in the system: a bad command, a prompt-injected web page, or a
misbehaving forged skill executes with full user permissions.

This module puts a seam in front of every code-execution call site:

    backend = get_backend()
    backend.run_shell("ls -la", timeout=30, cwd="~")
    backend.run_python("print(40 + 2)", timeout=10)

Two backends:
  - LocalBackend  — current behaviour: subprocess on the host. Default.
  - DockerBackend — runs the command inside a throwaway container with no network
                    (by default), memory/CPU/pid limits, dropped capabilities, and
                    a single mounted work directory.

Selection is by ``config.EXECUTION_BACKEND`` ("local" | "docker"). If docker is
selected but unavailable, behaviour depends on ``config.SANDBOX_REQUIRE``:
refuse (fail-closed) when True, fall back to local with a loud warning when False.

The return shape is identical across backends and matches ``tools/bash.py``:
``{"stdout", "stderr", "returncode", "success"}``.
"""
from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from typing import Optional

import config


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------

class Backend(ABC):
    name = "base"

    @abstractmethod
    def run_shell(self, command: str, timeout: int, cwd: Optional[str] = None) -> dict:
        ...

    @abstractmethod
    def run_python(self, code: str, timeout: int) -> dict:
        ...


def _timeout_result(timeout: int) -> dict:
    return {"stdout": "", "stderr": f"Command timed out after {timeout}s",
            "returncode": -1, "success": False}


def _error_result(msg: str) -> dict:
    return {"stdout": "", "stderr": msg, "returncode": -1, "success": False}


def _ok(proc: subprocess.CompletedProcess) -> dict:
    return {
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "returncode": proc.returncode,
        "success": proc.returncode == 0,
    }


# ---------------------------------------------------------------------------
# Local (host) backend — the historical behaviour
# ---------------------------------------------------------------------------

class LocalBackend(Backend):
    name = "local"

    def run_shell(self, command: str, timeout: int, cwd: Optional[str] = None) -> dict:
        cwd = os.path.expanduser(cwd or "~")
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd,
            )
            return _ok(proc)
        except subprocess.TimeoutExpired:
            return _timeout_result(timeout)
        except Exception as e:
            return _error_result(str(e))

    def run_python(self, code: str, timeout: int) -> dict:
        import sys
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=timeout,
            )
            return _ok(proc)
        except subprocess.TimeoutExpired:
            return _timeout_result(timeout)
        except Exception as e:
            return _error_result(str(e))


# ---------------------------------------------------------------------------
# Docker backend — throwaway container, no network, resource-capped
# ---------------------------------------------------------------------------

class DockerBackend(Backend):
    name = "docker"

    def __init__(self):
        self.image = config.SANDBOX_DOCKER_IMAGE
        self.network = config.SANDBOX_NETWORK            # "none" | "bridge"
        self.memory = config.SANDBOX_MEMORY              # e.g. "512m"
        self.cpus = config.SANDBOX_CPUS                  # e.g. "1.0"
        self.pids = config.SANDBOX_PIDS_LIMIT            # e.g. 256
        self.workdir = os.path.expanduser(config.SANDBOX_WORKDIR)
        os.makedirs(self.workdir, exist_ok=True)
        self._image_ready = False

    # -- container argv assembly (kept pure for unit testing) --------------
    def _base_args(self, timeout: int) -> list[str]:
        return [
            "docker", "run", "--rm", "-i",
            "--network", self.network,
            "--memory", self.memory,
            "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-v", f"{self.workdir}:/work",
            "-w", "/work",
            self.image,
        ]

    def shell_argv(self, command: str, timeout: int) -> list[str]:
        return self._base_args(timeout) + ["bash", "-c", command]

    def python_argv(self, code: str, timeout: int) -> list[str]:
        return self._base_args(timeout) + ["python3", "-c", code]

    # -- image presence ----------------------------------------------------
    def _ensure_image(self) -> None:
        if self._image_ready:
            return
        try:
            inspect = subprocess.run(
                ["docker", "image", "inspect", self.image],
                capture_output=True, text=True, timeout=10,
            )
            if inspect.returncode != 0:
                # Pull needs the registry regardless of the sandbox network setting.
                subprocess.run(["docker", "pull", self.image],
                               capture_output=True, text=True, timeout=300)
        except Exception:
            pass
        self._image_ready = True

    def _run(self, argv: list[str], timeout: int) -> dict:
        self._ensure_image()
        # Host-side timeout gets a small grace margin over the in-container one.
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout + 5,
            )
            return _ok(proc)
        except subprocess.TimeoutExpired:
            return _timeout_result(timeout)
        except FileNotFoundError:
            return _error_result("docker binary not found")
        except Exception as e:
            return _error_result(str(e))

    def run_shell(self, command: str, timeout: int, cwd: Optional[str] = None) -> dict:
        # cwd is intentionally ignored: the container always runs in /work (the mount).
        return self._run(self.shell_argv(command, timeout), timeout)

    def run_python(self, code: str, timeout: int) -> dict:
        return self._run(self.python_argv(code, timeout), timeout)


# ---------------------------------------------------------------------------
# Availability + selection
# ---------------------------------------------------------------------------

_docker_available: Optional[bool] = None
_backend: Optional[Backend] = None


def docker_available(refresh: bool = False) -> bool:
    """True if the Docker daemon is reachable. Cached after first probe."""
    global _docker_available
    if _docker_available is not None and not refresh:
        return _docker_available
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=8,
        )
        _docker_available = proc.returncode == 0
    except Exception:
        _docker_available = False
    return _docker_available


def get_backend(refresh: bool = False) -> Backend:
    """Return the configured execution backend (cached).

    EXECUTION_BACKEND=docker with a reachable daemon → DockerBackend.
    Otherwise → LocalBackend. If docker is required but unavailable and
    SANDBOX_REQUIRE is set, a _RefusingBackend is returned that fails closed.
    """
    global _backend
    if _backend is not None and not refresh:
        return _backend

    choice = (config.EXECUTION_BACKEND or "local").strip().lower()
    if choice == "docker":
        if docker_available(refresh=refresh):
            _backend = DockerBackend()
        elif config.SANDBOX_REQUIRE:
            print("[sandbox] EXECUTION_BACKEND=docker but Docker is unavailable "
                  "and SANDBOX_REQUIRE is set — refusing to execute on host.")
            _backend = _RefusingBackend()
        else:
            print("[sandbox] WARNING: EXECUTION_BACKEND=docker but Docker is "
                  "unavailable — falling back to LOCAL (host) execution. "
                  "Set SANDBOX_REQUIRE=true to fail closed instead.")
            _backend = LocalBackend()
    else:
        _backend = LocalBackend()
    return _backend


class _RefusingBackend(Backend):
    """Fail-closed backend: used when docker is required but missing."""
    name = "refusing"

    def run_shell(self, command: str, timeout: int, cwd: Optional[str] = None) -> dict:
        return _error_result("Execution refused: sandbox required but Docker is unavailable.")

    def run_python(self, code: str, timeout: int) -> dict:
        return _error_result("Execution refused: sandbox required but Docker is unavailable.")


class SandboxUnavailable(Exception):
    """Raised when self-initiated execution is requested but Docker isn't reachable.

    Callers MUST NOT fall back to host execution — they stage for approval or refuse.
    """


def autonomous_backend(refresh: bool = False) -> Backend:
    """Backend for SELF-INITIATED execution (cortex run_python, forged-skill
    validation, autonomous skill runs). DECISION (a): origin-based sandboxing —
    anything Apex starts on its own runs in Docker, unconditionally, regardless of
    EXECUTION_BACKEND. If Docker is unreachable this RAISES SandboxUnavailable so the
    caller degrades to staged-for-approval; it never silently runs on the host.
    """
    if docker_available(refresh=refresh):
        return DockerBackend()
    raise SandboxUnavailable(
        "Docker is required for autonomous execution but is unavailable."
    )


def active_backend_name() -> str:
    """Human-readable name of the backend that would be used right now."""
    return get_backend().name
