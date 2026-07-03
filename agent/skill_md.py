"""Markdown procedural skills — human-readable runbooks the agent creates and consults."""
import json
import re
import time
from pathlib import Path

_SKILLS_DIR = Path.home() / ".apex" / "skills"
_USAGE_FILE = _SKILLS_DIR / ".usage.json"


def _load_usage() -> dict:
    return json.loads(_USAGE_FILE.read_text()) if _USAGE_FILE.exists() else {}


def _save_usage(data: dict) -> None:
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    _USAGE_FILE.write_text(json.dumps(data, indent=2))


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_name(name: str) -> str:
    """Reject path-traversal in skill names (blocks manage(name='../../x'))."""
    if not isinstance(name, str) or not _SAFE_NAME_RE.match(name):
        raise ValueError(f"Invalid skill name {name!r} (letters, digits, - and _ only).")
    return name


def _skill_path(name: str) -> Path:
    return _SKILLS_DIR / _safe_name(name) / "SKILL.md"


def _parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def list_skills() -> list[dict]:
    """Return [{name, description}] for all non-archived skills."""
    if not _SKILLS_DIR.exists():
        return []
    out = []
    for p in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        if ".archive" in str(p):
            continue
        fm = _parse_frontmatter(p.read_text())
        out.append({"name": fm.get("name", p.parent.name), "description": fm.get("description", "")})
    return out


def manage(
    action: str,
    name: str = None,
    description: str = None,
    content: str = None,
    old_text: str = None,
    new_text: str = None,
    _bypass_approval: bool = False,
) -> str:
    """Dispatch a skill_manage action. Returns a string result."""
    # Path-traversal guard: every action that names a skill must use a safe name.
    if name is not None and action in ("create", "edit", "patch", "delete", "view"):
        try:
            _safe_name(name)
        except ValueError as e:
            return f"[skill_manage] {e}"
    # Write-approval gate: stage skill creation when enabled.
    if action == "create" and not _bypass_approval:
        try:
            import config as _cfg
            if getattr(_cfg, "SKILL_WRITE_APPROVAL", False):
                from agent import approvals as _appr
                return _appr.stage("skill", {
                    "name": name, "description": description, "content": content,
                })
        except Exception:
            pass
    if action == "list":
        skills = list_skills()
        if not skills:
            return "No procedural skills yet."
        return "\n".join(f"- **{s['name']}**: {s['description']}" for s in skills)

    if action == "create":
        if not name or not description or not content:
            return "create requires name, description, and content."
        path = _skill_path(name)
        if path.exists():
            return f"Skill {name!r} already exists. Use 'edit' to update it."
        path.parent.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y-%m-%d")
        header = (
            f"---\nname: {name}\ndescription: {description}\n"
            f"created: {today}\nuse_count: 0\nlast_used_at: null\n---\n\n"
        )
        path.write_text(header + content.strip() + "\n")
        return f"Skill {name!r} created at {path}."

    if action == "view":
        if not name:
            return "name required for view."
        path = _skill_path(name)
        if not path.exists():
            return f"No skill named {name!r}."
        usage = _load_usage()
        entry = usage.get(name, {})
        entry["use_count"] = entry.get("use_count", 0) + 1
        entry["last_used_at"] = time.time()
        usage[name] = entry
        _save_usage(usage)
        return path.read_text()

    if action == "edit":
        if not name or not content:
            return "name and content required for edit."
        path = _skill_path(name)
        if not path.exists():
            return f"No skill named {name!r}."
        existing = path.read_text()
        fm_match = re.match(r"^(---\n.*?\n---\n)", existing, re.DOTALL)
        header = fm_match.group(1) if fm_match else ""
        path.write_text(header + "\n" + content.strip() + "\n")
        return f"Skill {name!r} updated."

    if action == "patch":
        if not name or old_text is None:
            return "name and old_text required for patch."
        path = _skill_path(name)
        if not path.exists():
            return f"No skill named {name!r}."
        text = path.read_text()
        if old_text not in text:
            return f"Text not found in {name!r}."
        path.write_text(text.replace(old_text, new_text or "", 1))
        return f"Skill {name!r} patched."

    if action == "delete":
        if not name:
            return "name required for delete."
        path = _skill_path(name)
        if not path.exists():
            return f"No skill named {name!r}."
        archive = _SKILLS_DIR / ".archive" / name / "SKILL.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        path.rename(archive)
        return f"Skill {name!r} archived (not deleted permanently)."

    return f"Unknown action: {action!r}. Valid: list, create, view, edit, patch, delete."
