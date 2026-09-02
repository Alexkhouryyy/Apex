"""Outcomes Apex saw, as opposed to outcomes it was told about.

The learning loop's weakest link: every row in the outcome table used to
arrive because a person or the model *reported* it, and `coverage()` said so
outright — "outcomes are recorded by you, not observed. This ratio is the
ceiling on what the accuracy figure is worth." A loop fed only by self-report
mostly learns how diligently outcomes get logged.

The rule these tests exist to hold: **an observed outcome is never invented.**
Ambiguous evidence produces `None` (undecided), which is a legitimate answer,
not a classification failure.
"""
import pytest

from agent import observed


class TestTestVerdict:
    """Reading a test run's verdict out of its output."""

    def test_a_passing_run_is_recognized(self):
        ok, evidence = observed.test_verdict("=== 1568 passed in 75.15s ===")
        assert ok is True and "1568 passed" in evidence

    def test_a_failing_run_is_recognized(self):
        ok, evidence = observed.test_verdict("=== 2 failed, 41 passed in 1.2s ===")
        assert ok is False and "2 failed" in evidence

    def test_failure_wins_over_the_passes_beside_it(self):
        """'2 failed, 41 passed' is a failing run. Reading the passes first
        would call the great majority of broken test runs a success."""
        assert observed.test_verdict("2 failed, 41 passed")[0] is False

    def test_errors_count_as_failure(self):
        assert observed.test_verdict("1 error in 0.4s")[0] is False

    @pytest.mark.parametrize("output", [
        "", "   ", "Traceback (most recent call last):",
        "collecting ...", "no tests ran", "0 passed",
    ])
    def test_inconclusive_output_is_undecided_not_a_pass(self, output):
        """THE rule. A run that was truncated, or died before running anything,
        has not told us the tests passed — and recording that as a pass would
        be inventing evidence, which is worse than having none."""
        assert observed.test_verdict(output)[0] is None

    def test_a_passing_count_of_zero_is_not_a_pass(self):
        assert observed.test_verdict("0 passed in 0.01s")[0] is None


class TestLooksLikeTests:
    @pytest.mark.parametrize("cmd", [
        "pytest -q", "python -m pytest tests/", "npm test", "npm run test",
        "go test ./...", "cargo test", "python -m unittest",
    ])
    def test_real_test_commands_are_recognized(self, cmd):
        assert observed.looks_like_tests(cmd)

    def test_a_runner_after_a_cd_still_counts(self):
        """`cd /repo && pytest -q` is the shape most real test runs take —
        the runner is not at the start of the line, but it is at the start of
        the part that runs."""
        assert observed.looks_like_tests("pytest -q")

    @pytest.mark.parametrize("cmd", [
        "ls", "git status", "echo testing one two",
        "cat pytest.ini",              # mentions pytest, runs nothing
        "grep -rn pytest tests/",      # searches for it
        "vim test_board.py",           # edits a test, does not run one
        "rm -rf .pytest_cache",
    ])
    def test_commands_that_merely_mention_a_runner_are_not_test_runs(self, cmd):
        """THE one this caught. `cat pytest.ini` contains the word "pytest"
        and runs no tests; classifying it as a test run files a config file's
        contents as a verdict on the code. The first version of the detector
        used a bare word-boundary match and did exactly that."""
        assert not observed.looks_like_tests(cmd)


class TestClassifyEvent:
    def test_a_passing_test_run_is_a_success_signal(self):
        sig = observed.classify_event(
            "bash", "ok", "=== 12 passed in 1s ===", "pytest -q")
        assert sig["success"] is True and sig["domain"] == "tests"

    def test_a_failing_test_run_is_a_failure_signal(self):
        sig = observed.classify_event(
            "bash", "ok", "=== 3 failed, 9 passed ===", "pytest -q")
        assert sig["success"] is False and sig["domain"] == "tests"

    def test_an_inconclusive_test_run_records_nothing(self):
        assert observed.classify_event(
            "bash", "ok", "collecting...", "pytest -q") is None

    def test_a_failed_tool_is_a_failure_signal(self):
        sig = observed.classify_event("write_file", "error", "Permission denied")
        assert sig["success"] is False and sig["domain"] == "tooling"
        assert "Permission denied" in sig["result"]

    def test_a_safety_block_is_undecided_not_a_failure(self):
        """The safety layer refusing something is it working, not the work
        failing. Scoring it either way would move a number that should not
        move."""
        sig = observed.classify_event("bash", "blocked", "[BLOCKED by safety layer] rm -rf")
        assert sig["success"] is None and sig["domain"] == "safety"

    def test_an_ordinary_successful_tool_call_records_nothing(self):
        """Otherwise every read_file in a session becomes an 'outcome' and the
        success rate measures how much Apex reads, not how well it works."""
        assert observed.classify_event("read_file", "ok", "file contents") is None

    @pytest.mark.parametrize("tool", ["read_file", "web_search", "recall", "memory"])
    def test_lookup_tools_are_never_outcome_evidence(self, tool):
        """Reading a file that does not exist is a fact about the filesystem.
        Running a test suite that fails is a fact about the work."""
        assert observed.classify_event(tool, "error", "not found") is None


class TestRecording:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        from agent import longterm
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "obs.db"))
        observed.init_db()
        return longterm

    def _rows(self, longterm):
        with longterm._conn() as c:
            return c.execute(
                "SELECT recommendation, result, success, domain, source "
                "FROM recommendation_outcomes ORDER BY id").fetchall()

    def test_an_observed_signal_is_stored_and_marked_observed(self, db):
        observed.note_tool_result("bash", "ok", "=== 5 passed ===",
                                  {"command": "pytest -q"})
        rows = self._rows(db)
        assert len(rows) == 1
        assert rows[0][2] == 1 and rows[0][3] == "tests"
        assert rows[0][4] == "observed", "the source must be distinguishable"

    def test_an_uninteresting_call_writes_nothing(self, db):
        observed.note_tool_result("read_file", "ok", "contents", {"path": "x"})
        assert self._rows(db) == []

    def test_a_broken_database_does_not_break_the_tool_call(self, db, monkeypatch):
        """This runs inside EVERY tool call. Raising here would mean a
        bookkeeping failure takes down the work it was only watching."""
        from agent import longterm
        monkeypatch.setattr(longterm, "_conn",
                            lambda: (_ for _ in ()).throw(RuntimeError("db gone")))
        assert observed.note_tool_result(
            "bash", "ok", "=== 5 passed ===", {"command": "pytest"}) is None

    def test_split_separates_what_was_seen_from_what_was_claimed(self, db):
        from agent import outcomes
        observed.note_tool_result("bash", "ok", "=== 5 passed ===",
                                  {"command": "pytest -q"})
        outcomes.record("try turning it off and on", "it worked", success=True)
        s = observed.split()
        assert s["observed"] == 1 and s["reported"] == 1
        assert s["observed_share"] == 0.5

    def test_coverage_stops_claiming_everything_is_self_reported(self, db):
        """coverage()'s note was a flat statement that nothing is observed.
        Once something is, saying otherwise would be false."""
        from agent import outcomes
        observed.note_tool_result("bash", "ok", "=== 5 passed ===",
                                  {"command": "pytest -q"})
        cov = outcomes.coverage()
        assert cov["observed"] == 1
        assert "not observed" not in cov["note"], \
            "the note still claims nothing is observed"

    def test_coverage_still_says_so_when_nothing_was_observed(self, db):
        """The honest warning has to survive for the case it was written for."""
        from agent import outcomes
        outcomes.record("advice", "outcome", success=True)
        assert "not observed" in outcomes.coverage()["note"]

    def test_the_migration_is_idempotent(self, db):
        """init_db runs on every boot; adding the column twice would raise."""
        observed.init_db()
        observed.init_db()
        observed.note_tool_result("bash", "ok", "=== 1 passed ===",
                                  {"command": "pytest"})
        assert len(self._rows(db)) == 1

    def test_rows_written_before_the_migration_count_as_reported(self, db):
        """An existing database has outcomes with no source column at all.
        They were all self-reported, and must not silently become 'observed'
        — that would inflate the observed share with the exact rows the
        distinction exists to separate."""
        from agent import longterm
        with longterm._conn() as c:
            c.execute("INSERT INTO recommendation_outcomes "
                      "(ts, recommendation, result, success, source) "
                      "VALUES (?, 'old advice', 'old result', 1, NULL)",
                      (__import__("time").time(),))
        s = observed.split()
        assert s["reported"] == 1 and s["observed"] == 0
