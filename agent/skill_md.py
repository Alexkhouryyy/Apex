"""Markdown procedural skills — human-readable runbooks the agent creates and consults."""
import json
import re
import time
import shutil
import tempfile
from pathlib import Path

_SKILLS_DIR = Path.home() / ".apex" / "skills"
_USAGE_FILE = _SKILLS_DIR / ".usage.json"


def _load_usage() -> dict:
    return json.loads(_USAGE_FILE.read_text(encoding='utf-8')) if _USAGE_FILE.exists() else {}


def _save_usage(data: dict) -> None:
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    _USAGE_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


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


_BUNDLED_DIR = Path(__file__).resolve().parent.parent / "apex_skills"


def install_bundled() -> int:
    """Install skills that ship with Apex into ~/.apex/skills, skipping any that
    already exist. Returns how many were installed.

    Bundled skills live in the repo so they are versioned and survive a rebuild;
    ~/.apex/skills is runtime state and is not backed up. Usage is seeded on
    install for the same reason `create` does it — otherwise the curator sees
    age_days=999 and archives them before first use.
    """
    installed = 0
    if not _BUNDLED_DIR.exists():
        return 0
    try:
        usage = _load_usage()
        for src in sorted(_BUNDLED_DIR.glob("*/SKILL.md")):
            name = src.parent.name
            try:
                dest = _skill_path(name)          # validates the name
            except ValueError:
                continue
            if dest.parent.exists():
                continue
            # Keep source notices, compatibility manifests and supporting files.
            # Publish the complete directory together; never expose half a skill.
            if any(p.is_symlink() for p in src.parent.rglob('*')):
                continue
            dest.parent.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix='.bundled-', dir=dest.parent.parent))
            try:
                shutil.copytree(src.parent, staging, dirs_exist_ok=True)
                staging.rename(dest.parent)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            usage.setdefault(name, {"use_count": 0})
            usage[name]["last_used_at"] = time.time()
            installed += 1
        if installed:
            _save_usage(usage)
    except Exception as e:
        print(f"[Skills] bundled install skipped: {e}")
    return installed


def list_skills() -> list[dict]:
    """Return [{name, description}] for all non-archived skills."""
    if not _SKILLS_DIR.exists():
        return []
    out = []
    for p in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        from agent.skill_imports import available
        if p.parent.name.startswith('.') or not available(p.parent):
            continue
        fm = _parse_frontmatter(p.read_text(encoding='utf-8'))
        out.append({"name": p.parent.name, "description": fm.get("description", "")})
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
    if name and action in ('view', 'edit', 'patch'):
        folder = _skill_path(name).parent
        if (folder / '.apex-import.json').exists():
            from agent.skill_imports import available
            if action != 'view':
                return 'Imported skills are immutable. Preview a new revision and install under a new name.'
            if not available(folder):
                return 'This imported skill is disabled or its reviewed files changed. Review it again in Apex Home.'
    if action == "create" and not _bypass_approval:
        try:
            import config as _cfg
            approval_on = getattr(_cfg, "SKILL_WRITE_APPROVAL", False)
        except Exception:
            approval_on = False
        if approval_on:
            # Fail CLOSED: if staging errors, do NOT fall through to the direct write.
            from agent import approvals as _appr
            try:
                return _appr.stage("skill", {
                    "name": name, "description": description, "content": content,
                })
            except Exception as e:
                return f"Skill blocked: approval is required but staging failed ({e})."
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
        path.write_text(header + content.strip() + "\n", encoding='utf-8')
        # Seed the usage sidecar. Without this there is no `last_used_at`, so
        # curator computes age_days = 999.0 (curator.py:176-177) and ARCHIVES the
        # skill on its very next run — every freshly authored skill would silently
        # vanish before it was ever used. Creation counts as touching it.
        try:
            usage = _load_usage()
            usage.setdefault(name, {})
            usage[name].setdefault("use_count", 0)
            usage[name]["last_used_at"] = time.time()
            _save_usage(usage)
        except Exception:
            pass
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
        text = path.read_text(encoding='utf-8')
        if (path.parent / '.apex-import.json').exists():
            manifest = json.loads((path.parent / '.apex-import.json').read_text(encoding='utf-8'))
            text = (f'Reviewed skill directory: {path.parent}\n'
                    'Resolve relative support-file references against this directory using read_file. '
                    'Instructions do not grant additional tool permissions.\n\n' + text)
            text = 'APEX COMPATIBILITY REVIEW:\n' + manifest['review_notes'] + '\n\n' + text
        return text

    if action == "edit":
        if not name or not content:
            return "name and content required for edit."
        path = _skill_path(name)
        if not path.exists():
            return f"No skill named {name!r}."
        existing = path.read_text(encoding='utf-8')
        fm_match = re.match(r"^(---\n.*?\n---\n)", existing, re.DOTALL)
        header = fm_match.group(1) if fm_match else ""
        path.write_text(header + "\n" + content.strip() + "\n", encoding='utf-8')
        return f"Skill {name!r} updated."

    if action == "patch":
        if not name or old_text is None:
            return "name and old_text required for patch."
        path = _skill_path(name)
        if not path.exists():
            return f"No skill named {name!r}."
        text = path.read_text(encoding='utf-8')
        if old_text not in text:
            return f"Text not found in {name!r}."
        path.write_text(text.replace(old_text, new_text or "", 1), encoding='utf-8')
        return f"Skill {name!r} patched."

    if action == "delete":
        if not name:
            return "name required for delete."
        path = _skill_path(name)
        if not path.exists():
            return f"No skill named {name!r}."
        archive = _SKILLS_DIR / ".archive" / (name + '-' + str(time.time_ns()))
        archive.parent.mkdir(parents=True, exist_ok=True)
        path.parent.rename(archive)
        return f"Skill {name!r} archived (not deleted permanently)."

    return f"Unknown action: {action!r}. Valid: list, create, view, edit, patch, delete."
