"""Run the smoke suite: boot Apex for real and check its answers are true.

One boot, shared across every check, because a boot costs ~30s. Each check is a
separate test so a failure names the thing that broke rather than "smoke failed".

Marked `slow`. Run just this: pytest tests/test_smoke.py
Skip it:                      pytest -m "not slow"

Validated against the four bugs found on the day it was written — each was
reintroduced and confirmed to turn the suite red:

  clock removed from the prompt   -> prompt_carries_todays_date
  budget_tokens hardcoded         -> no_removed_parameters_are_sent
  EOF returns "" (the 129MB spin) -> headless_stdin_does_not_spin
  SELECT of a missing column      -> no_silent_failures

Two of those four were missed by the first version, and both misses were
instructive: the spin check measured log size, which a 64KB pipe buffer caps, so
a runaway loop looked identical to a healthy run; and the boot did not pass
--think, so the thinking parameter was never built and the check passed over a
code path that never ran. A green check on an unexercised path is the same lie
this suite exists to catch.
"""
from __future__ import annotations

import os

import pytest

from tools import smoke

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    """One real boot of main.py --text --think against a scripted model."""
    startup = tmp_path_factory.mktemp("smoke-startup")
    (startup / "sitecustomize.py").write_text(
        'print("SMOKE OUTPUT BURST " + "x" * (128 * 1024), flush=True)\n', encoding="utf-8")
    pythonpath = os.pathsep.join([str(startup), *([os.environ["PYTHONPATH"]]
                                                 if os.environ.get("PYTHONPATH") else [])])
    return smoke.boot(
        say="remember that my name is Alex",
        extra_env={"PYTHONPATH": pythonpath},
        script=[
            {"content": [
                {"type": "text", "text": "Noting that."},
                {"type": "tool_use", "id": "t1", "name": "remember",
                 "input": {"content": "User is Alex", "kind": "fact",
                           "importance": 9}},
            ], "stop_reason": "tool_use"},
            {"content": [{"type": "text", "text": "Saved."}],
             "stop_reason": "end_turn"},
        ],
    )


@pytest.mark.parametrize("check", smoke.CHECKS, ids=lambda c: c.__name__)
def test_smoke_check(check, result):
    finding = check(result)
    assert finding.ok, f"{finding.check}: {finding.detail}\n{result.stdout[-6000:]}"


def test_boot_output_is_drained_before_the_pipe_can_block(result):
    assert "SMOKE OUTPUT BURST " in result.stdout
    assert result.log_bytes > 128 * 1024
    assert result.server.agent_requests(), "The turn must complete after the startup output burst."


def test_smoke_uses_local_catalogues_and_no_desktop_screenshot(result):
    assert {"/v1/models", "/ollama/v1/models"} <= set(result.server.model_requests)
    for request in result.server.agent_requests():
        for message in request.get("messages", []):
            content = message.get("content", [])
            if isinstance(content, list):
                assert not any(block.get("type") == "image" for block in content
                               if isinstance(block, dict))


def test_every_check_ran(result):
    """Guard the guard: a suite whose checks silently stopped being collected
    passes forever."""
    assert len(smoke.CHECKS) >= 10, (
        f"only {len(smoke.CHECKS)} checks registered — the @check decorator has "
        f"probably stopped being applied"
    )


# --------------------------------------------------------------------------
# The sweep's classifier, tested without booting anything
# --------------------------------------------------------------------------

class TestTheDashboardSweepClassifier:
    """`every_dashboard_tab_answers` decides which HTTP answers are faults.

    Nothing tested that decision before — the only coverage was a live boot,
    which exercises whatever the current machine happens to return. So the rule
    could be loosened to "any 5xx is fine" and the suite would stay green,
    which is how a sweep quietly stops sweeping.
    """

    def _verdict(self, entries):
        from pathlib import Path
        from tools.smoke import BootResult, every_dashboard_tab_answers
        r = BootResult(stdout="", db_path=Path("/tmp/x"), home=Path("/tmp"),
                       dashboard_port=0, server=None, returncode=0, log_bytes=0)
        r.dashboard_sweep = entries
        return every_dashboard_tab_answers(r)

    def test_a_healthy_route_passes(self):
        assert self._verdict([{"route": "/api/x", "status": 200, "body": '{"ok":1}'}]).ok

    def test_a_missing_id_is_not_a_fault(self):
        """A made-up id is legitimately absent."""
        assert self._verdict([{"route": "/api/x/1", "status": 404, "body": ""}]).ok

    def test_a_500_is_broken(self):
        assert not self._verdict(
            [{"route": "/api/x", "status": 500, "body": "no such column"}]).ok

    def test_a_503_that_names_the_absent_program_is_only_unconfigured(self):
        """Voicebox is a separate desktop app. "It is not running" is a
        correct, honest 503 from a healthy route, and every machine without
        that app — CI included — would otherwise fail this check forever."""
        f = self._verdict([{"route": "/api/voicebox/profiles", "status": 503,
                            "body": '{"error": "Keep Voicebox open on the Apex laptop."}'}])
        assert f.ok
        assert "unconfigured" in f.detail

    def test_an_opaque_503_is_still_broken(self):
        """The line that matters. A route swallowing a real error into a
        generic 503 is the exact fail-open shape this sweep exists to catch, so
        "it returned 503" is not by itself an excuse."""
        assert not self._verdict(
            [{"route": "/api/x", "status": 503, "body": "Service Unavailable"}]).ok

    def test_a_200_carrying_an_error_payload_is_not_a_pass(self):
        """The tab renders and shows nothing — a broken tab in an empty tab's
        clothes."""
        f = self._verdict([{"route": "/api/mail", "status": 200,
                            "body": '{"error": "Email not configured"}'}])
        assert "unconfigured" in f.detail

    def test_a_route_that_never_answered_is_broken(self):
        assert not self._verdict(
            [{"route": "/api/x", "status": None, "body": "TimeoutError: "}]).ok

    def test_a_401_is_broken_not_unconfigured(self):
        """The sweep authenticates. A 401 means the token was rejected, which
        is a fault in the sweep or the server, never a configuration answer."""
        assert not self._verdict(
            [{"route": "/api/x", "status": 401, "body": "Unauthorized"}]).ok


def test_the_redirected_home_keeps_the_real_model_cache(tmp_path):
    """On a slow line the boot re-downloaded the embedding model into the temp
    home and the scripted `remember` missed its deadline (seen on the owner's PC)."""
    from tools.smoke import model_cache_env
    assert model_cache_env({}, home=tmp_path) == {"HF_HOME": str(tmp_path / ".cache" / "huggingface")}
    assert model_cache_env({"HF_HOME": "D:/models"}, home=tmp_path) == {}, "a cache you chose is kept"


def test_the_boot_hands_apex_the_real_model_cache(monkeypatch, tmp_path):
    from pathlib import Path
    import tools.smoke as smoke
    seen = {}

    class Stop(Exception):
        pass

    def popen(*args, env=None, **kwargs):
        seen.update(env)
        raise Stop
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setattr(smoke.subprocess, "Popen", popen)
    with pytest.raises(Stop):
        smoke.boot()
    assert seen["HF_HOME"] == str(Path.home() / ".cache" / "huggingface")
    assert seen["HOME"] != str(Path.home()), "HOME itself is still redirected"
