"""The props jail — the only files Apex's board is allowed to show.

The board serves files to a browser over HTTP. That makes "which file?" a
security question, not a convenience one: a path arriving from a model, a tool
call or a URL must never be able to walk out of the props folder and hand
somebody `.env`, the SQLite brain, or an SSH key.

barehands solved this with a `media/` airlock and its README calls the jail a
safety feature rather than a limitation, which is exactly right. Apex needs its
own because its board serves from its own folder.

Everything here is pure path arithmetic against a root — no HTTP, no rendering —
so the containment rules can be attacked directly in tests. A jail whose only
exercise is "it seems to work in the browser" is not a jail.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# What the board can render. Deliberately short: every entry is a file type a
# browser will parse, and each one is a decision to trust that parser.
MODEL_EXTS = (".glb", ".gltf")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
ALLOWED_EXTS = MODEL_EXTS + IMAGE_EXTS

MIME = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def props_root() -> Path:
    """Where props live. Created on demand so the folder exists to drop into."""
    root = Path(os.path.expanduser("~/.apex/props"))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return root


def resolve(rel: str, root: Optional[Path] = None) -> Optional[Path]:
    """Turn a caller-supplied path into a real file inside the jail, or None.

    Refuses, in order: nothing, an absolute path, a Windows drive or UNC path, a
    symlink escape, a walk outside the root, an extension not on the list, and
    anything that is not a regular file.

    `Path.resolve()` before the containment check, not after — `root/../../etc`
    only looks like an escape once it is normalized, and comparing the unresolved
    string would pass it straight through. Symlinks resolve too, so a link
    planted inside the folder cannot point out of it.
    """
    if not rel or not isinstance(rel, str):
        return None
    rel = rel.strip().replace("\\", "/").lstrip("/")
    if not rel:
        return None
    # A drive letter or UNC prefix would make the join absolute on Windows and
    # silently discard the root.
    if ":" in rel.split("/")[0] or rel.startswith("//"):
        return None

    base = (root or props_root())
    try:
        base = base.resolve()
        target = (base / rel).resolve()
    except Exception:
        return None

    if base != target and base not in target.parents:
        return None
    if target.suffix.lower() not in ALLOWED_EXTS:
        return None
    if not target.is_file():
        return None
    return target


def media_type(path) -> str:
    return MIME.get(Path(path).suffix.lower(), "application/octet-stream")


def is_model(rel: str) -> bool:
    return str(rel).lower().endswith(MODEL_EXTS)


def listing(root: Optional[Path] = None) -> list[str]:
    """Every usable prop, as jail-relative paths, at any depth.

    Read live off the filesystem rather than cached: dropping a file in and
    having to restart Apex to see it would make the folder feel broken.
    """
    base = root or props_root()
    out: list[str] = []
    try:
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTS:
                try:
                    out.append(p.relative_to(base).as_posix())
                except ValueError:
                    continue
    except Exception:
        return []
    return sorted(out)


def describe(root: Optional[Path] = None) -> str:
    """The folder as something a model can read and choose from."""
    base = root or props_root()
    props = listing(base)
    if not props:
        return (f"No props yet. Drop .glb or .gltf models (or png/jpg images) "
                f"into {base} — subfolders are fine, and they show up without "
                f"restarting Apex. Free models: Sketchfab (filter by CC "
                f"licence), Poly Pizza, or Khronos' glTF-Sample-Models.")
    models = [p for p in props if is_model(p)]
    images = [p for p in props if not is_model(p)]
    lines = [f"{len(props)} prop(s) in {base}:"]
    if models:
        lines.append("  3D models:")
        lines += [f"    {p}" for p in models[:40]]
    if images:
        lines.append("  images:")
        lines += [f"    {p}" for p in images[:40]]
    return "\n".join(lines)
