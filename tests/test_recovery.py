"""Tests for tool self-recovery (agent/recovery.py)."""
import os

from agent import recovery as rec


# --- near-miss file suggestions ---------------------------------------------

def test_suggests_close_filename(tmp_path):
    (tmp_path / "plans.md").write_text("x")
    (tmp_path / "notes.md").write_text("x")
    out = rec.enrich("read", {"path": str(tmp_path / "plan.md")},
                     f"Error reading {tmp_path}/plan.md: [Errno 2] No such file or directory")
    assert "[recovery]" in out and "plans.md" in out


def test_suggests_substring_match_difflib_would_miss(tmp_path):
    (tmp_path / "my_plan_v2_final.md").write_text("x")
    out = rec.enrich("read", {"path": str(tmp_path / "plan.md")},
                     "Error reading x: [Errno 2] No such file or directory")
    assert "my_plan_v2_final.md" in out


def test_points_at_nearest_existing_dir_when_nothing_similar(tmp_path):
    (tmp_path / "totally_unrelated.bin").write_text("x")
    out = rec.enrich("read", {"path": str(tmp_path / "zzzzz.md")},
                     "Error reading x: [Errno 2] No such file or directory")
    assert "[recovery]" in out and str(tmp_path) in out


def test_original_text_is_always_preserved(tmp_path):
    (tmp_path / "plans.md").write_text("x")
    original = f"Error reading {tmp_path}/plan.md: [Errno 2] No such file or directory"
    out = rec.enrich("read", {"path": str(tmp_path / "plan.md")}, original)
    assert out.startswith(original)   # additive only — never rewrites the result


# --- hallucinated tool names -------------------------------------------------

def test_unknown_tool_suggests_closest(monkeypatch):
    monkeypatch.setattr(rec, "_known_tools", lambda: ["read", "write", "web_search", "bash"])
    out = rec.enrich("web_serch", {}, "Unknown tool: web_serch")
    assert "web_search" in out


def test_unknown_tool_with_no_close_match_is_left_alone(monkeypatch):
    monkeypatch.setattr(rec, "_known_tools", lambda: ["read", "write"])
    out = rec.enrich("qqqqzzz", {}, "Unknown tool: qqqqzzz")
    assert out == "Unknown tool: qqqqzzz"


# --- empty search ------------------------------------------------------------

def test_empty_search_shows_whats_there_and_looser_pattern(tmp_path):
    (tmp_path / "alpha.py").write_text("x")
    (tmp_path / "beta.py").write_text("x")
    out = rec.enrich("find", {"pattern": "gamma.py", "base": str(tmp_path)},
                     "No matches found")
    assert "alpha.py" in out
    assert "*gamma.py*" in out          # suggests a looser glob


# --- oversized output spill --------------------------------------------------

def test_large_output_spills_to_file(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, "_SPILL_DIR", str(tmp_path / "spill"))
    big = "A" * (rec.SPILL_THRESHOLD + 5000)
    out = rec.enrich("bash", {"command": "cat huge"}, big)
    assert len(out) < len(big)                  # context is protected
    assert "[recovery] Full output saved to" in out
    path = out.split("Full output saved to ")[1].split(" ")[0]
    assert os.path.exists(path)
    assert open(path).read() == big             # nothing lost


def test_normal_output_is_untouched():
    assert rec.enrich("read", {"path": "x"}, "file contents here") == "file contents here"


# --- robustness --------------------------------------------------------------

def test_enrich_never_raises(monkeypatch):
    monkeypatch.setattr(rec, "suggest_paths",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    original = "Error reading x: No such file or directory"
    assert rec.enrich("read", {"path": "x"}, original) == original


def test_non_string_result_passes_through():
    assert rec.enrich("x", {}, None) is None


def test_trajectory_classifies_original_not_the_hint(test_db, tmp_path):
    """The recovery hint must not change how the outcome is recorded."""
    from agent import trajectory as traj
    traj.init_db()
    original = "Unknown tool: web_serch"
    assert traj.classify(original)[0] == traj.UNKNOWN_TOOL
    enriched = rec.enrich("web_serch", {}, original)
    # Even enriched, the sentinel prefix still classifies correctly.
    assert traj.classify(enriched)[0] == traj.UNKNOWN_TOOL
