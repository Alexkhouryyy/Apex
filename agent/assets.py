"""Versioned 3D assets — an object is an artifact with a history, not a file.

The design document is explicit about this (§22): *"Apex should treat a 3D
object as a versioned engineering artifact, not as a single mesh file. Every
saved object should have an authoritative source, a fast preview, provenance,
validation and a retrievable history."* Before this module, `board_create`
copied a `.glb` into the props folder under a random name and recorded nothing
— no version, no lineage, and no record of the request that produced it. A
week later there would be no way to answer "why does this exist" or "what did
it look like before the recolor", which is precisely the retrieval §23 asks
for.

## The layout

Each asset is a folder, not a loose file::

    ~/.apex/props/created/phone-stand/
        asset.json      identity, provenance, and every version
        v1.glb          immutable
        v2.glb          immutable — a recolor is a NEW version, never an edit

`asset.json` is deliberately a plain, human-readable file next to the meshes
rather than only rows in SQLite. It travels with the asset if the folder is
copied, it can be read without Apex running, and it is inspectable by the
person whose design it describes — the same reasoning that put the knowledge
vault in Markdown.

## The versioning rules, and which line enforces each

From §22.1, quoted so a future reader can check the code against them:

* *"Every committed edit receives an immutable version identifier."* —
  `add_version` only ever appends, and the file it names is written once.
* *"Record the initiating voice command or structured action and the tool
  result."* — every version carries the `command` dict that produced it.
* *"A failed generation or export never replaces the last valid asset."* —
  nothing here mutates or deletes an existing version; a failure simply never
  gets to call `add_version`, and `current` keeps pointing at the last good one.
* *"Every fabricated candidate must link back to the exact immutable source
  version."* — `parent` records lineage, so a chain of derivations resolves
  back to its origin.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

# The folder inside the props jail where generated assets live. Inside the jail
# on purpose: these are served to a browser like any other prop, and must obey
# the same containment rules (see agent/props.py).
CREATED_DIR = "created"

MANIFEST = "asset.json"


def asset_root(slug: str, props_root: Optional[Path] = None) -> Path:
    from agent import props as _props
    base = props_root or _props.props_root()
    return base / CREATED_DIR / slug


def manifest_path(slug: str, props_root: Optional[Path] = None) -> Path:
    return asset_root(slug, props_root) / MANIFEST


def load(slug: str, props_root: Optional[Path] = None) -> Optional[dict]:
    """The asset's manifest, or None if there isn't one.

    Returns None rather than raising on damaged JSON: a corrupt manifest must
    not make the board unusable, and the caller can decide whether that means
    "no such asset" or "this one needs attention".
    """
    p = manifest_path(slug, props_root)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(slug: str, data: dict, props_root: Optional[Path] = None) -> None:
    root = asset_root(slug, props_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(
        json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def create(slug: str, title: str, *, command: dict, units: str = "mm",
           props_root: Optional[Path] = None) -> dict:
    """Start a new asset. Returns the fresh manifest (version list empty).

    Idempotent by design: creating over an existing asset keeps its history
    rather than truncating it, because the alternative — silently discarding
    every prior version because a name was reused — is exactly the data loss
    the versioning rules exist to prevent.
    """
    existing = load(slug, props_root)
    if existing is not None:
        return existing
    data = {
        "id": slug,
        "title": title,
        "units": units,
        "created": time.time(),
        "origin": command,
        "current_version": None,
        "versions": [],
    }
    _write(slug, data, props_root)
    return data


def add_version(slug: str, filename: str, *, command: dict,
                parent: Optional[int] = None,
                props_root: Optional[Path] = None) -> Optional[dict]:
    """Append an immutable version. Returns the version record, or None if
    the asset has no manifest to append to.

    `parent` is the version this one was derived from — a recolor of v2 is a
    v3 whose parent is 2 — so lineage survives even when versions are not
    strictly sequential in meaning.
    """
    data = load(slug, props_root)
    if data is None:
        return None
    n = len(data["versions"]) + 1
    record = {
        "version": n,
        "file": filename,
        "created": time.time(),
        "command": command,
        "parent": parent,
    }
    data["versions"].append(record)
    data["current_version"] = n
    _write(slug, data, props_root)
    return record


def current_file(slug: str, props_root: Optional[Path] = None) -> Optional[str]:
    """The jail-relative path of the current version, for a board card's src."""
    data = load(slug, props_root)
    if not data or not data.get("versions"):
        return None
    cur = data.get("current_version") or len(data["versions"])
    for v in data["versions"]:
        if v["version"] == cur:
            return f"{CREATED_DIR}/{slug}/{v['file']}"
    return None


def version_file(slug: str, version: int,
                 props_root: Optional[Path] = None) -> Optional[str]:
    data = load(slug, props_root)
    if not data:
        return None
    for v in data["versions"]:
        if v["version"] == version:
            return f"{CREATED_DIR}/{slug}/{v['file']}"
    return None


def next_filename(slug: str, props_root: Optional[Path] = None) -> str:
    """`v3.glb` — named by position, so the file itself states its version.

    Reads the manifest rather than counting files on disk: a stray file
    dropped in the folder by hand must not silently renumber the history.
    """
    data = load(slug, props_root)
    n = (len(data["versions"]) + 1) if data else 1
    return f"v{n}.glb"


def listing(props_root: Optional[Path] = None) -> list[dict]:
    """Every saved asset, newest first — for board_restore and board_history."""
    from agent import props as _props
    base = (props_root or _props.props_root()) / CREATED_DIR
    out = []
    try:
        for child in base.iterdir():
            if not child.is_dir():
                continue
            data = load(child.name, props_root)
            if data:
                out.append(data)
    except Exception:
        return []
    return sorted(out, key=lambda d: d.get("created", 0), reverse=True)


def find_by_title(title: str, props_root: Optional[Path] = None) -> Optional[dict]:
    """Match on what the user actually says — the title they named it, not the
    slug. Case- and space-insensitive, because "Test Object", "test object"
    and "Test  Object" are one thing to the person saying them."""
    want = " ".join(str(title or "").lower().split())
    if not want:
        return None
    for data in listing(props_root):
        if " ".join(str(data.get("title", "")).lower().split()) == want:
            return data
    return None


def describe(data: dict) -> str:
    """An asset's history as something a model can read back to the user."""
    if not data:
        return "No such asset."
    lines = [f"'{data.get('title')}' ({data.get('id')}) — "
             f"{len(data.get('versions', []))} version(s), "
             f"units {data.get('units', '?')}:"]
    for v in data.get("versions", []):
        cmd = v.get("command") or {}
        what = cmd.get("tool") or "?"
        detail = ", ".join(
            f"{k}={cmd[k]}" for k in ("shape", "color", "dims_mm")
            if cmd.get(k) is not None)
        marker = "  <- current" if v["version"] == data.get("current_version") else ""
        parent = f" (from v{v['parent']})" if v.get("parent") else ""
        lines.append(f"  v{v['version']}{parent}: {what}"
                     + (f" — {detail}" if detail else "") + marker)
    return "\n".join(lines)
