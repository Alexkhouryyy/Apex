"""Setting the API key must not destroy the rest of .env.

Apex.bat wrote the key with `>".env" echo KEY=value`. A single `>` truncates.
The condition that triggered it was "the file contains the string
`your_key_here`" — which is exactly the state of a .env freshly copied from
.env.example, so the first-run path could delete a fully configured file.

What goes missing is not recoverable by re-typing: VAPID_PRIVATE_KEY cannot be
regenerated without invalidating every push subscription already issued, and
DASHBOARD_TOKEN and channel tokens are user-chosen secrets.

Moving the edit into Python is also what makes it testable; `>` was not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import set_env_key  # noqa: E402

FULL_ENV = (
    "ANTHROPIC_API_KEY=your_key_here\n"
    "DASHBOARD_TOKEN=whowantstobeking\n"
    "VAPID_PUBLIC_KEY=BO85Dfthavj4eQI7wRCJqfTzYBsZXavZ\n"
    "VAPID_PRIVATE_KEY=ivdILszBHFRqT-lndO62FO_Zlw\n"
    "TELEGRAM_BOT_TOKEN=123456:ABC\n"
    "# a comment\n"
    "OPENAI_API_KEY=your_openai_key_here\n"
)


def test_replacing_the_key_preserves_every_other_line(tmp_path):
    """The regression, stated directly."""
    env = tmp_path / ".env"
    env.write_text(FULL_ENV)

    set_env_key.set_key(env, "ANTHROPIC_API_KEY", "sk-ant-real")

    text = env.read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-real" in text
    assert "your_key_here" not in text
    for survivor in ("DASHBOARD_TOKEN=whowantstobeking",
                     "VAPID_PRIVATE_KEY=ivdILszBHFRqT-lndO62FO_Zlw",
                     "VAPID_PUBLIC_KEY=BO85Dfthavj4eQI7wRCJqfTzYBsZXavZ",
                     "TELEGRAM_BOT_TOKEN=123456:ABC",
                     "# a comment",
                     "OPENAI_API_KEY=your_openai_key_here"):
        assert survivor in text, f"lost {survivor!r}"


def test_a_backup_is_left_behind(tmp_path):
    env = tmp_path / ".env"
    env.write_text(FULL_ENV)
    set_env_key.set_key(env, "ANTHROPIC_API_KEY", "sk-ant-real")
    assert (tmp_path / ".env.bak").read_text() == FULL_ENV


def test_missing_file_is_created(tmp_path):
    env = tmp_path / ".env"
    assert set_env_key.set_key(env, "ANTHROPIC_API_KEY", "sk-ant-real") == "created"
    assert env.read_text() == "ANTHROPIC_API_KEY=sk-ant-real\n"


def test_absent_key_is_appended_not_substituted(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DASHBOARD_TOKEN=keepme\n")
    assert set_env_key.set_key(env, "ANTHROPIC_API_KEY", "sk-ant-real") == "added"
    text = env.read_text()
    assert "DASHBOARD_TOKEN=keepme" in text
    assert "ANTHROPIC_API_KEY=sk-ant-real" in text


def test_commented_out_key_is_left_alone(tmp_path):
    """`#ANTHROPIC_API_KEY=` is a comment, not the setting."""
    env = tmp_path / ".env"
    env.write_text("#ANTHROPIC_API_KEY=old-commented\nDASHBOARD_TOKEN=x\n")
    set_env_key.set_key(env, "ANTHROPIC_API_KEY", "sk-new")
    text = env.read_text()
    assert "#ANTHROPIC_API_KEY=old-commented" in text
    assert "ANTHROPIC_API_KEY=sk-new" in text


def test_similarly_named_keys_are_not_clobbered(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY_BACKUP=other\nANTHROPIC_API_KEY=old\n")
    set_env_key.set_key(env, "ANTHROPIC_API_KEY", "sk-new")
    text = env.read_text()
    assert "ANTHROPIC_API_KEY_BACKUP=other" in text
    assert "ANTHROPIC_API_KEY=sk-new" in text


def test_duplicate_assignments_collapse_to_one(tmp_path):
    """A second assignment further down wins at load time, so leaving it would
    make the file disagree with itself."""
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=first\nDASHBOARD_TOKEN=x\nANTHROPIC_API_KEY=second\n")
    set_env_key.set_key(env, "ANTHROPIC_API_KEY", "sk-new")
    text = env.read_text()
    assert text.count("ANTHROPIC_API_KEY=") == 1
    assert "sk-new" in text
    assert "DASHBOARD_TOKEN=x" in text


def test_crlf_files_stay_crlf(tmp_path):
    """Windows writes CRLF; rewriting as LF is a whole-file diff for no reason."""
    env = tmp_path / ".env"
    env.write_bytes(b"ANTHROPIC_API_KEY=old\r\nDASHBOARD_TOKEN=x\r\n")
    set_env_key.set_key(env, "ANTHROPIC_API_KEY", "sk-new")
    raw = env.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_empty_value_is_refused(tmp_path):
    """An empty key would boot Apex into 'ANTHROPIC_API_KEY not set' after
    reporting success."""
    env = tmp_path / ".env"
    env.write_text(FULL_ENV)
    assert set_env_key.main(["ANTHROPIC_API_KEY", "   ", "--env", str(env)]) == 1
    assert env.read_text() == FULL_ENV


def test_apex_bat_no_longer_truncates_env():
    """The batch file must not reacquire the `>` redirect."""
    bat = (Path(__file__).resolve().parent.parent / "Apex.bat").read_text(errors="replace")
    # Skip REM lines — the comment explaining this bug necessarily quotes it.
    code = [ln for ln in bat.splitlines()
            if not ln.strip().upper().startswith("REM")]
    offenders = [ln.strip() for ln in code if '>".env"' in ln or ">.env" in ln]
    assert not offenders, f"Apex.bat truncates .env again: {offenders}"
    assert "set_env_key.py" in bat
