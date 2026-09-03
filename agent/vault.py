"""Obsidian-compatible vault — plain Markdown files with YAML frontmatter and wikilinks.

The vault is a regular folder Obsidian can open directly. Apex writes notes into it;
the user reads/edits them visually. Both sides share the same source of truth.

Vault layout:
  ~/Documents/Apex/
  ├── .obsidian/          Obsidian config (auto-generated, safe to commit)
  ├── Memory/             APEX_MEMORY.md + APEX_USER.md (the bounded memory files)
  ├── Notes/              Apex-generated notes (research, summaries, observations)
  ├── People/             One note per person entity from the knowledge graph
  ├── Projects/           One note per project entity
  ├── Daily/              One note per day — Apex appends observations here
  └── Skills/             Mirrors of procedural skills (read-only copies for browsing)
"""
import json
import re
import time
from pathlib import Path
from typing import Optional

import config

VAULT_DIR: Path = Path(getattr(config, "VAULT_PATH", "~/Documents/Apex")).expanduser()

_FOLDERS = ["Memory", "Notes", "People", "Projects", "Daily", "Skills", "Planets"]

_SAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(name: str) -> str:
    return _SAFE_RE.sub("-", name).strip("-")


def _ensure() -> None:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    for f in _FOLDERS:
        (VAULT_DIR / f).mkdir(exist_ok=True)
    _write_obsidian_config()


def _write_obsidian_config() -> None:
    od = VAULT_DIR / ".obsidian"
    od.mkdir(exist_ok=True)
    cfg = od / "app.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({
            "useMarkdownLinks": False,
            "newFileLocation": "folder",
            "newFileFolderPath": "Notes",
            "defaultViewMode": "source",
            "strictLineBreaks": False,
            "spellcheck": False,
        }, indent=2))
    # Minimal community plugins list (empty — no plugins required)
    plugins = od / "community-plugins.json"
    if not plugins.exists():
        plugins.write_text("[]")


def _build_frontmatter(title: str, tags: list[str] = None, extra: dict = None) -> str:
    today = time.strftime("%Y-%m-%d")
    lines = ["---", f"title: {title}", f"created: {today}", f"updated: {today}", "source: apex"]
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {t}" for t in tags)
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


def _note_path(title: str, folder: str) -> Path:
    return VAULT_DIR / folder / f"{_safe(title)}.md"


def _after_write(path: Path) -> None:
    """Index a note the moment Apex writes it, so its own writing is findable
    immediately rather than at the next full pass.

    Imported here rather than at module scope because agent/vault_index.py
    imports this module — and swallowed deliberately: a note that saved but did
    not index leaves a stale index, which `vault_index.status()` reports and
    `reindex()` repairs. A note that failed to SAVE because indexing raised is
    lost writing. Those are not the same size of mistake.
    """
    try:
        from agent import vault_index
        vault_index.index_note(path)
    except Exception as e:
        print(f"[Vault] Note saved but not indexed: {e}")


def write_note(
    title: str,
    content: str,
    folder: str = "Notes",
    tags: list[str] = None,
    links: list[str] = None,
    extra_fm: dict = None,
    _bypass_approval: bool = False,
) -> str:
    """Create or overwrite a note. Returns a one-line status string."""
    # Write-approval gate: stage instead of writing when enabled.
    if not _bypass_approval and getattr(config, "MEMORY_WRITE_APPROVAL", False):
        try:
            from agent import approvals as _appr
            return _appr.stage("note", {
                "title": title, "content": content, "folder": folder,
                "tags": tags, "links": links,
            })
        except Exception:
            pass
    _ensure()
    path = _note_path(title, folder)
    today = time.strftime("%Y-%m-%d")

    link_section = ""
    if links:
        link_section = "## See also\n" + "  ".join(f"[[{l}]]" for l in links) + "\n\n"

    if path.exists():
        existing = path.read_text()
        # Preserve frontmatter, bump updated date, replace body
        fm_match = re.match(r"^---\n.*?\n---\n", existing, re.DOTALL)
        if fm_match:
            old_fm = fm_match.group(0)
            new_fm = re.sub(r"updated: \d{4}-\d{2}-\d{2}", f"updated: {today}", old_fm)
            path.write_text(new_fm + link_section + content.strip() + "\n")
        else:
            path.write_text(_build_frontmatter(title, tags, extra_fm) + link_section + content.strip() + "\n")
        _after_write(path)
        return f"Updated note: {path.relative_to(VAULT_DIR)}"

    path.write_text(_build_frontmatter(title, tags, extra_fm) + link_section + content.strip() + "\n")
    _after_write(path)
    return f"Created note: {path.relative_to(VAULT_DIR)}"


def append_note(title: str, text: str, folder: str = "Notes") -> str:
    """Append a paragraph to a note, creating it if missing."""
    _ensure()
    path = _note_path(title, folder)
    if not path.exists():
        return write_note(title, text, folder)
    path.write_text(path.read_text().rstrip() + "\n\n" + text.strip() + "\n")
    _after_write(path)
    return f"Appended to: {path.relative_to(VAULT_DIR)}"


def daily_note_path() -> Path:
    """Return (and create if needed) today's daily note."""
    _ensure()
    today = time.strftime("%Y-%m-%d")
    path = VAULT_DIR / "Daily" / f"{today}.md"
    if not path.exists():
        path.write_text(
            _build_frontmatter(today, tags=["daily"]) +
            f"# {today}\n\n"
        )
    return path


def append_daily(text: str) -> str:
    """Append an observation to today's daily note."""
    path = daily_note_path()
    ts = time.strftime("%H:%M")
    path.write_text(path.read_text().rstrip() + f"\n\n**{ts}** — {text.strip()}\n")
    _after_write(path)
    return f"Daily note updated ({path.name})."


def list_notes(folder: str = None) -> list[dict]:
    """List notes — all vault notes if folder is None, else one folder."""
    _ensure()
    root = VAULT_DIR / folder if folder else VAULT_DIR
    out = []
    for p in sorted(root.rglob("*.md")):
        if ".obsidian" in str(p):
            continue
        out.append({
            "path": str(p.relative_to(VAULT_DIR)),
            "name": p.stem,
            "folder": p.parent.name,
            "bytes": p.stat().st_size,
        })
    return out


def read_note(title: str, folder: str = "Notes") -> Optional[str]:
    """Return note content, or None if it doesn't exist."""
    path = _note_path(title, folder)
    return path.read_text() if path.exists() else None


def entity_to_note(name: str, kind: str, properties: dict) -> str:
    """Mirror a knowledge-graph entity as an Obsidian note."""
    folder = "People" if kind in ("person", "contact") else "Projects" if kind in ("project", "company") else "Notes"
    lines = [f"## {name}\n"]
    for k, v in (properties or {}).items():
        lines.append(f"- **{k}**: {v}")
    return write_note(name, "\n".join(lines), folder=folder, tags=[kind])


def init_vault() -> str:
    """Create vault structure, write README, migrate memory files. Called at startup."""
    _ensure()

    # Write README so Obsidian users know what they're looking at
    readme = VAULT_DIR / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Apex Brain\n\n"
            "This vault is Apex's external memory — readable and editable by both you and the AI.\n\n"
            "| Folder | Contents |\n"
            "|--------|----------|\n"
            "| Memory/ | APEX_MEMORY.md and APEX_USER.md — Apex's always-on facts |\n"
            "| Notes/ | Research summaries, observations, ad-hoc notes |\n"
            "| People/ | One note per person Apex knows about |\n"
            "| Projects/ | One note per project |\n"
            "| Daily/ | Daily observations from Apex (one note per day) |\n"
            "| Skills/ | Copies of procedural skills for browsing |\n"
            "| Planets/ | One journal per Constellation expert — what each specialist has learned |\n\n"
            "Open this folder in Obsidian to see Apex's mind as a graph.\n"
        )

    # Migrate memory files from old ~/.apex/memory/ location if they exist there
    old_dir = Path.home() / ".apex" / "memory"
    new_mem_dir = VAULT_DIR / "Memory"
    migrated = []
    for fname in ("APEX_MEMORY.md", "APEX_USER.md"):
        old = old_dir / fname
        new = new_mem_dir / fname
        if old.exists() and not new.exists():
            new.write_text(old.read_text())
            old.write_text(f"# Moved\n\nThis file has moved to:\n`{new}`\n")
            migrated.append(fname)

    note_count = sum(1 for _ in VAULT_DIR.rglob("*.md") if ".obsidian" not in str(_))
    result = f"Vault ready: {VAULT_DIR} ({note_count} notes)"
    if migrated:
        result += f" — migrated: {', '.join(migrated)}"
    return result
