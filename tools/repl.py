"""Persistent IPython kernel for the agent's analyst work.

Variables, imports, dataframes, plots — everything survives across calls.
Image outputs (matplotlib, PIL) are captured as base64 PNG and returned
so Claude can see them via vision.
"""
import base64
import json
import threading
from queue import Empty
from typing import Optional

import config

_km = None    # KernelManager
_kc = None    # KernelClient
_lock = threading.Lock()


def _check_execution_policy() -> None:
    # This kernel is a host process, not a Docker kernel. Refuse rather than
    # silently bypass the execution policy, even if a host kernel already exists.
    if config.EXECUTION_BACKEND != "local" or config.SANDBOX_REQUIRE:
        raise RuntimeError(
            "Persistent Python requires EXECUTION_BACKEND=local and SANDBOX_REQUIRE=false. "
            "Host execution is refused under the configured isolation policy; "
            "use sandboxed bash for isolated Python execution."
        )


def _ensure_kernel():
    global _km, _kc
    _check_execution_policy()
    if _kc is not None:
        return _kc
    from jupyter_client.manager import KernelManager
    _km = KernelManager(kernel_name="python3")
    _km.start_kernel()
    _kc = _km.client()
    _kc.start_channels()
    _kc.wait_for_ready(timeout=30)

    # Inline matplotlib setup so plots flow back as images
    setup_code = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "from IPython.display import display\n"
        "%matplotlib inline\n"
    )
    _kc.execute(setup_code)
    # Drain setup output
    _drain(_kc, timeout=10)
    return _kc


def _drain(kc, timeout: float = 0.5) -> list:
    """Drain pending IOPub messages, return list of (msg_type, content)."""
    msgs = []
    while True:
        try:
            msg = kc.get_iopub_msg(timeout=timeout)
            msgs.append((msg["msg_type"], msg["content"]))
        except Empty:
            break
    return msgs


def execute(code: str, timeout: float = 60.0) -> dict:
    """Run Python code in the persistent kernel.

    Returns a dict with keys:
      stdout, stderr, result, images (list of {"type":"image","data":b64,"media_type":"image/png"}),
      error (None or {"name", "value", "traceback"})
    """
    with _lock:
        kc = _ensure_kernel()
        msg_id = kc.execute(code)

        stdout_parts = []
        stderr_parts = []
        result_text = None
        images = []
        error = None

        # Wait for the matching 'idle' status to know we're done
        import time
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = kc.get_iopub_msg(timeout=1.0)
            except Empty:
                continue

            parent_id = msg.get("parent_header", {}).get("msg_id")
            if parent_id != msg_id:
                continue

            mtype = msg["msg_type"]
            content = msg["content"]

            if mtype == "stream":
                if content.get("name") == "stdout":
                    stdout_parts.append(content.get("text", ""))
                elif content.get("name") == "stderr":
                    stderr_parts.append(content.get("text", ""))

            elif mtype in ("execute_result", "display_data"):
                data = content.get("data", {})
                if "image/png" in data:
                    images.append({"media_type": "image/png", "data": data["image/png"]})
                if "text/plain" in data and result_text is None:
                    result_text = data["text/plain"]

            elif mtype == "error":
                error = {
                    "name": content.get("ename", ""),
                    "value": content.get("evalue", ""),
                    "traceback": "\n".join(content.get("traceback", [])),
                }

            elif mtype == "status" and content.get("execution_state") == "idle":
                break

        return {
            "stdout": "".join(stdout_parts).rstrip(),
            "stderr": "".join(stderr_parts).rstrip(),
            "result": result_text,
            "images": images,
            "error": error,
        }


def reset() -> str:
    """Restart the kernel — all state lost."""
    global _km, _kc
    with _lock:
        _check_execution_policy()
        try:
            if _km:
                _km.shutdown_kernel(now=True)
        except Exception:
            pass
        _km = None
        _kc = None
        _ensure_kernel()
    return "Python kernel reset. All variables cleared."


def shutdown() -> None:
    """Clean shutdown — called at agent exit."""
    global _km, _kc
    with _lock:
        try:
            if _kc:
                _kc.stop_channels()
            if _km:
                _km.shutdown_kernel(now=True)
        except Exception:
            pass
        _km = None
        _kc = None


def format_result(result: dict) -> str:
    """Convert REPL result dict to a string summary (image refs separate)."""
    parts = []
    if result["stdout"]:
        parts.append(f"STDOUT:\n{result['stdout']}")
    if result["stderr"]:
        parts.append(f"STDERR:\n{result['stderr']}")
    if result["result"]:
        parts.append(f"RESULT:\n{result['result']}")
    if result["error"]:
        e = result["error"]
        parts.append(f"ERROR: {e['name']}: {e['value']}\n{e['traceback']}")
    if result["images"]:
        parts.append(f"[{len(result['images'])} image(s) produced — attached]")
    return "\n\n".join(parts) or "(no output)"
