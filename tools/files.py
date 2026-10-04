"""File system tools."""
import os
import glob


def read(path: str) -> str:
    path = os.path.expanduser(path)
    from agent import doc_convert
    if os.path.splitext(path)[1].lower() in doc_convert.CONVERTIBLE:
        return doc_convert.read_text(path)     # Word, PowerPoint, Excel, PDF as text, not bytes
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"Error reading {path}: {e}"


def write(path: str, content: str) -> str:
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


def append(path: str, content: str) -> str:
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended to {path}"
    except Exception as e:
        return f"Error appending {path}: {e}"


def list_dir(path: str = ".") -> str:
    path = os.path.expanduser(path)
    try:
        entries = os.listdir(path)
        result = []
        for e in sorted(entries):
            full = os.path.join(path, e)
            kind = "dir" if os.path.isdir(full) else "file"
            size = os.path.getsize(full) if kind == "file" else ""
            result.append(f"[{kind}] {e}" + (f" ({size} bytes)" if size != "" else ""))
        return "\n".join(result) or "(empty directory)"
    except Exception as e:
        return f"Error listing {path}: {e}"


def find(pattern: str, base: str = ".") -> str:
    base = os.path.expanduser(base)
    matches = glob.glob(os.path.join(base, "**", pattern), recursive=True)
    return "\n".join(matches) or "No matches found"


def delete(path: str) -> str:
    path = os.path.expanduser(path)
    try:
        os.remove(path)
        return f"Deleted {path}"
    except Exception as e:
        return f"Error deleting {path}: {e}"
