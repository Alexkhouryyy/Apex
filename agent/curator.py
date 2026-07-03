"""Background skill curator — runs every 7 days when the agent has been idle.

Covers both Python skills in /skills/ (via skill_usage SQLite table) and
Markdown procedural skills in ~/.apex/skills/ (via .usage.json sidecar).
"""
import json
import tarfile
import time
from pathlib import Path
from typing import Optional

import config
from agent import longterm

_SKILLS_MD_DIR = Path.home() / ".apex" / "skills"
_SKILLS_PY_DIR = Path(__file__).parent.parent / "skills"
_BACKUPS_DIR = Path.home() / ".apex" / "skills" / ".curator_backups"
_LOGS_DIR = Path.home() / ".apex" / "logs" / "curator"
_STATE_KEY = "curator_last_run"

STALE_DAYS: int = getattr(config, "CURATOR_STALE_DAYS", 30)
ARCHIVE_DAYS: int = getattr(config, "CURATOR_ARCHIVE_DAYS", 90)
INTERVAL_DAYS: int = getattr(config, "CURATOR_INTERVAL_DAYS", 7)
MIN_IDLE_HOURS: float = getattr(config, "CURATOR_MIN_IDLE_HOURS", 2)


def _last_run_ts() -> float:
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT value FROM world_state WHERE key = ?", (_STATE_KEY,)
            ).fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def _set_last_run() -> None:
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO world_state(key, value, updated_at) VALUES(?,?,?)",
                (_STATE_KEY, str(time.time()), time.time()),
            )
    except Exception:
        pass


def should_run(last_active_ts: float) -> bool:
    """Return True when idle long enough and interval has passed."""
    idle_ok = (time.time() - last_active_ts) >= MIN_IDLE_HOURS * 3600
    interval_ok = (time.time() - _last_run_ts()) >= INTERVAL_DAYS * 86400
    return idle_ok and interval_ok


def _backup(ts: str) -> Optional[Path]:
    """Tar the Markdown skills dir to a timestamped backup. Returns archive path."""
    if not _SKILLS_MD_DIR.exists():
        return None
    _BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    archive = _BACKUPS_DIR / f"{ts}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(_SKILLS_MD_DIR, arcname="skills")
    return archive


def _load_usage() -> dict:
    usage_file = _SKILLS_MD_DIR / ".usage.json"
    return json.loads(usage_file.read_text()) if usage_file.exists() else {}


def _archive_md(name: str) -> None:
    src = _SKILLS_MD_DIR / name / "SKILL.md"
    dst = _SKILLS_MD_DIR / ".archive" / name / "SKILL.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        src.rename(dst)


def _mark_stale_md(path: Path) -> None:
    text = path.read_text()
    if "state: stale" not in text:
        text = text.replace("use_count:", "state: stale\nuse_count:", 1)
        path.write_text(text)


def _llm_dedup(client, skills: list[dict]) -> list[str]:
    """Ask Haiku to identify near-duplicate skills. Returns list of suggestion strings."""
    try:
        import anthropic as _ant
        skill_list = "\n".join(f"- {s['name']}: {s['description']}" for s in skills)
        prompt = (
            "You are a skill library curator. Below is a list of procedural skills. "
            "Identify any near-duplicates or skills that could be merged. "
            "Reply with a short bullet list of consolidation suggestions, or 'None found.' if clean.\n\n"
            f"{skill_list}"
        )
        resp = client.messages.create(
            model=config.PROACTIVE_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        return [line for line in text.splitlines() if line.strip()]
    except Exception as e:
        return [f"LLM dedup skipped: {e}"]


def _list_all_names_and_descs() -> list[dict]:
    """Combine Python + Markdown skill names/descriptions for dedup review."""
    out = []
    # Markdown skills
    if _SKILLS_MD_DIR.exists():
        for p in sorted(_SKILLS_MD_DIR.glob("*/SKILL.md")):
            if ".archive" in str(p):
                continue
            try:
                from agent.skill_md import _parse_frontmatter
                fm = _parse_frontmatter(p.read_text())
                out.append({"name": fm.get("name", p.parent.name), "description": fm.get("description", "")})
            except Exception:
                pass
    # Python skills
    try:
        from agent import skills as _skills_py
        for s in _skills_py.list_skills():
            out.append({"name": f"py:{s['name']}", "description": s.get("description", "")})
    except Exception:
        pass
    return out


def status() -> dict:
    """Return curator status for the API endpoint."""
    md_count = 0
    py_count = 0
    if _SKILLS_MD_DIR.exists():
        md_count = sum(1 for p in _SKILLS_MD_DIR.glob("*/SKILL.md") if ".archive" not in str(p))
    try:
        from agent import skills as _skills_py
        py_count = len(_skills_py.list_skills())
    except Exception:
        pass

    usage = _load_usage()
    now = time.time()
    lru = sorted(usage.items(), key=lambda x: x[1].get("last_used_at") or 0)[:5]

    return {
        "last_run": _last_run_ts(),
        "next_run_in_hours": max(0.0, (INTERVAL_DAYS * 86400 - (now - _last_run_ts())) / 3600),
        "md_skill_count": md_count,
        "py_skill_count": py_count,
        "lru_skills": [{"name": k, "last_used_at": v.get("last_used_at")} for k, v in lru],
    }


def run(dry_run: bool = False, client=None) -> str:
    """Run the full curation cycle. Returns a report string."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_lines = [f"# Curator Report — {ts}", f"dry_run={dry_run}", ""]

    if not dry_run:
        archive = _backup(ts)
        if archive:
            report_lines.append(f"Backup: {archive}")

    # Phase 1: deterministic stale/archive pass on Markdown skills
    if _SKILLS_MD_DIR.exists():
        usage = _load_usage()
        now = time.time()
        for p in sorted(_SKILLS_MD_DIR.glob("*/SKILL.md")):
            if ".archive" in str(p):
                continue
            name = p.parent.name
            last_used = usage.get(name, {}).get("last_used_at") or 0
            age_days = (now - last_used) / 86400 if last_used else 999.0
            if age_days >= ARCHIVE_DAYS:
                report_lines.append(f"ARCHIVE: {name} ({age_days:.0f}d unused)")
                if not dry_run:
                    _archive_md(name)
            elif age_days >= STALE_DAYS:
                report_lines.append(f"STALE:   {name} ({age_days:.0f}d unused)")
                if not dry_run:
                    _mark_stale_md(p)

    # Phase 2: LLM dedup pass
    if client:
        all_skills = _list_all_names_and_descs()
        if len(all_skills) > 1:
            report_lines.append("\n## LLM Dedup Suggestions")
            report_lines.extend(_llm_dedup(client, all_skills))
    else:
        report_lines.append("\n(LLM dedup skipped — no client provided)")

    report_str = "\n".join(report_lines)

    if not dry_run:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_dir = _LOGS_DIR / ts
        log_dir.mkdir(exist_ok=True)
        (log_dir / "REPORT.md").write_text(report_str)
        _set_last_run()

    return report_str


def rollback() -> str:
    """Restore from the most recent backup tarball."""
    if not _BACKUPS_DIR.exists():
        return "No backups found."
    archives = sorted(_BACKUPS_DIR.glob("*.tar.gz"))
    if not archives:
        return "No backups found."
    latest = archives[-1]
    try:
        dest = _SKILLS_MD_DIR.parent.resolve()
        with tarfile.open(latest, "r:gz") as tf:
            # Guard against tar-slip: refuse any member that resolves outside dest.
            safe = []
            for m in tf.getmembers():
                target = (dest / m.name).resolve()
                if target == dest or dest in target.parents:
                    safe.append(m)
                else:
                    print(f"[Curator] Skipping unsafe tar member: {m.name!r}")
            try:
                tf.extractall(dest, members=safe, filter="data")  # py>=3.12
            except TypeError:
                tf.extractall(dest, members=safe)                 # py<3.12 fallback
        return f"Restored from {latest.name}."
    except Exception as e:
        return f"Rollback failed: {e}"
