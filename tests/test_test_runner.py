"""The laptop test launcher must not inherit live provider settings or storage."""
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts import run_tests


def test_child_ignores_dotenv_and_gets_test_settings(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("AGENT_MODEL=deepseek-flash\nDEEPSEEK_API_KEY=private-test-value\n", encoding="utf-8")
    inherited = dict(os.environ, AGENT_MODEL="deepseek-flash", SUBSCRIPTION_ENABLED="true",
                     DEEPSEEK_API_KEY="private-test-value", DB_PATH="live.db",
                     VAULT_PATH="live-vault", ANTHROPIC_BASE_URL="https://live.invalid")
    env = run_tests.test_environment(tmp_path, inherited)
    program = """import json, os, sys
from dotenv import load_dotenv
loaded = load_dotenv(sys.argv[1])
import config
from agent import longterm
print(json.dumps([loaded, config.AGENT_MODEL, config.SUBSCRIPTION_ENABLED,
                  config.DEEPSEEK_API_KEY, longterm.DB_PATH, config.VAULT_PATH,
                  os.environ.get('ANTHROPIC_BASE_URL'), sys.flags.utf8_mode]))
"""
    result = subprocess.run([sys.executable, "-c", program, str(dotenv_path)], cwd=run_tests.ROOT,
                            env=env, capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout) == [False, "claude-opus-5", False, "",
                                       str(tmp_path / "apex.db"), str(tmp_path / "vault"), None, 1]
    assert inherited["AGENT_MODEL"] == "deepseek-flash"
    assert inherited["DB_PATH"] == "live.db"


def test_launcher_does_not_allow_reusing_a_basetemp(capsys):
    assert run_tests.main(["--basetemp=existing-folder"]) == 2
    assert "fresh --basetemp" in capsys.readouterr().err
