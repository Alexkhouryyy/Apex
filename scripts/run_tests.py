"""Run pytest with fresh storage and test settings, independently of laptop .env."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_environment(run_dir: Path, inherited: dict[str, str]) -> dict[str, str]:
    # Do not let the owner's provider, keys, background services or data paths
    # select different branches in unit tests. Preserve OS and proxy settings.
    tree = ast.parse((ROOT / "config.py").read_text(encoding="utf-8"))
    # os.getenv("X", ...) and the yes/no helper _flag("X", ...)
    settings = {node.args[0].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and node.args
                and ((isinstance(node.func, ast.Attribute) and node.func.attr == "getenv")
                     or (isinstance(node.func, ast.Name) and node.func.id == "_flag"))
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)}
    prefixes = ("APEX_", "ANTHROPIC_", "OPENAI_", "GEMINI_", "DEEPSEEK_",
                "OPENROUTER_", "CLAUDE_", "RELAY_")
    env = {key: value for key, value in inherited.items()
           if key not in settings and not key.startswith(prefixes)}
    env.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "ANTHROPIC_API_KEY": "sk-ant-placeholder-for-ci-tests-only",
        "DB_PATH": str(run_dir / "apex.db"),
        "VAULT_PATH": str(run_dir / "vault"),
        "SANDBOX_WORKDIR": str(run_dir / "sandbox"),
        "APEX_STUDY_DIR": str(run_dir / "study"),
    })
    # This is an explicit test opt-in, not a live Apex setting.
    if inherited.get("APEX_IDE_INTEGRATION") == "1":
        env["APEX_IDE_INTEGRATION"] = "1"
    return env


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--basetemp" in argv or any(arg.startswith("--basetemp=") for arg in argv):
        print("This launcher chooses a fresh --basetemp for every run.", file=sys.stderr)
        return 2
    # python-dotenv >= 1.2 (in requirements.txt) supports this isolation flag.
    from importlib.metadata import version
    if tuple(int(part) for part in version("python-dotenv").split(".")[:2]) < (1, 2):
        print("Update python-dotenv to >=1.2 before using this test launcher.", file=sys.stderr)
        return 2
    parent = None
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        parent = Path(os.environ["LOCALAPPDATA"]) / "ApexTestRuns"
        parent.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="apex-tests-", dir=parent)).resolve()
    command = [sys.executable, "-X", "utf8", "-m", "pytest", "-q",
               "-p", "no:cacheprovider", "--basetemp", str(run_dir / "pytest"), *argv]
    print(f"Test storage: {run_dir}", flush=True)
    print("Using test settings; laptop .env is not loaded.", flush=True)
    try:
        return subprocess.call(command, cwd=ROOT, env=test_environment(run_dir, dict(os.environ)))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
