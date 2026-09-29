"""Precise edits to a build, by voice: "make the nose red", "move this up 5 cm",
"remove the fins", "add a shaft", "make it twice as big".

A build is a recipe of named parts (agent/build3d.py). An edit changes the
recipe and saves it as the next version, so every edit can be undone from the
build's history and nothing is ever edited in place. The model says WHICH part
by its name ("left fin"), its number ("part 3"), or "this" — the part the user
selected or pinch-tapped on the board — and WHAT to do with one operation.
Several parts can share a name prefix ("fin" matches "left fin" and "right fin")
only when the model asks for all of them.
"""
from __future__ import annotations

import copy
import re
from typing import Optional

from agent import build3d

OPS = ("move", "resize", "scale", "rotate", "color", "metal", "rename", "remove", "duplicate", "add", "shape")
DIRECTIONS = {"up": (0, 1, 0), "down": (0, -1, 0), "left": (-1, 0, 0), "right": (1, 0, 0),
              "forward": (0, 0, 1), "front": (0, 0, 1), "back": (0, 0, -1), "backward": (0, 0, -1)}
THIS = {"this", "that", "it", "selected", "the selected part", "this part", "that part"}


class EditError(ValueError):
    """An edit that cannot be made, as a sentence the model can act on."""


def _names(parts):
    return ", ".join(f"{i + 1}. {p.get('name') or p['shape']}" for i, p in enumerate(parts))


def resolve(parts: list[dict], target, referent: Optional[int] = None, every: bool = False) -> list[int]:
    """The part indices a target means. `referent` is the part the user pointed
    at or selected, used for "this"; `every` lets a name match several parts."""
    if target is None or target == "":
        raise EditError("Say which part: its name, its number, or 'this' for the one you selected. Parts: " + _names(parts))
    if isinstance(target, bool):
        raise EditError("Part must be a name, a number or 'this'.")
    if isinstance(target, (int, float)):
        if target != int(target) or not 1 <= target <= len(parts):
            raise EditError(f"There is no part {target}; this build has {len(parts)}: {_names(parts)}")
        return [int(target) - 1]
    text = " ".join(str(target).split()).lower()
    if text in ("all", "everything", "whole", "the whole thing", "it all"):
        return list(range(len(parts)))
    if text in THIS:
        if referent is None or not 0 <= referent < len(parts):
            raise EditError("Nothing is selected. Tap the part with a quick pinch (in parts mode) or click it, "
                            "or say its name. Parts: " + _names(parts))
        return [referent]
    m = re.fullmatch(r"(?:part\s*#?\s*)?(\d+)", text)
    if m:
        return resolve(parts, int(m.group(1)))
    names = [(p.get("name") or p["shape"]).lower() for p in parts]
    exact = [i for i, n in enumerate(names) if n == text]
    if exact:
        return exact if every else exact[:1] if len(exact) == 1 else _ambiguous(parts, exact, text)
    words = [i for i, n in enumerate(names) if re.search(r"\b" + re.escape(text.rstrip("s")) + r"s?\b", n)]
    if not words:
        raise EditError(f"No part called '{target}'. Parts: " + _names(parts))
    if len(words) > 1 and not every:
        return _ambiguous(parts, words, text)
    return words


def _ambiguous(parts, found, text):
    raise EditError(f"'{text}' matches {len(found)} parts ("
                    + ", ".join(f"{i + 1}. {parts[i].get('name')}" for i in found)
                    + "). Name one, give its number, or set every=true to change them all.")


def _vec3(value, what):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise EditError(f"{what} must be three numbers [x, y, z]")
    return [float(v) for v in value]


def apply(parts: list[dict], indices: list[int], op: str, args: dict) -> tuple[list[dict], str]:
    """The edited recipe and a short description of what changed."""
    if op not in OPS:
        raise EditError(f"Unknown edit '{op}'. Use one of: {', '.join(OPS)}")
    out = copy.deepcopy(parts)
    names = ", ".join(out[i].get("name") or out[i]["shape"] for i in indices) if indices else ""
    if op == "move":
        if args.get("by") is not None:
            delta = _vec3(args["by"], "by (cm)")
        else:
            d = str(args.get("direction", "")).lower()
            if d not in DIRECTIONS:
                raise EditError("Say how far to move: by [x, y, z] cm, or a direction (up, down, left, right, forward, back) and an amount in cm.")
            amount = args.get("amount", 5)
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise EditError("amount must be a number of centimetres")
            delta = [c * float(amount) for c in DIRECTIONS[d]]
        for i in indices:
            out[i]["at"] = [round(a + b, 3) for a, b in zip(out[i]["at"], delta)]
        what = f"moved {names} by {', '.join(f'{v:g}' for v in delta)} cm"
    elif op == "resize":
        size = args.get("size")
        size = [size] * 3 if isinstance(size, (int, float)) and not isinstance(size, bool) else _vec3(size, "size (cm)")
        for i in indices:
            out[i]["size"] = [float(v) for v in size]
        what = f"resized {names} to {' x '.join(f'{v:g}' for v in size)} cm"
    elif op == "scale":
        factor = args.get("factor")
        f = [factor] * 3 if isinstance(factor, (int, float)) and not isinstance(factor, bool) else _vec3(factor, "factor")
        if any(not 0.05 <= v <= 20 for v in f):
            raise EditError("factor must be between 0.05 and 20 (2 = twice as big)")
        whole = len(indices) == len(out) and len(out) > 1
        if whole:        # the whole build grows about its own centre, so the parts stay together
            lo = [min(p["at"][k] - p["size"][k] / 2 for p in out) for k in range(3)]
            hi = [max(p["at"][k] + p["size"][k] / 2 for p in out) for k in range(3)]
            centre = [(a + b) / 2 for a, b in zip(lo, hi)]
            centre[1] = lo[1]                       # keep it standing on the same floor
        for i in indices:
            out[i]["size"] = [round(v * k, 3) for v, k in zip(out[i]["size"], f)]
            if whole:
                out[i]["at"] = [round(c + (a - c) * k, 3) for a, c, k in zip(out[i]["at"], centre, f)]
        what = f"scaled {'the whole build' if whole else names} by {' x '.join(f'{v:g}' for v in f) if len(set(f)) > 1 else f'{f[0]:g}'}"
    elif op == "rotate":
        delta = _vec3(args.get("by"), "by (degrees about x, y, z)")
        for i in indices:
            out[i]["rotate"] = [round((a + b + 180) % 360 - 180, 3) for a, b in zip(out[i]["rotate"], delta)]
        what = f"turned {names} by {', '.join(f'{v:g}' for v in delta)}°"
    elif op == "color":
        from agent.blender_bridge import resolve_color
        rgba = resolve_color(args.get("color") or "")
        if rgba is None:
            raise EditError(f"colour '{args.get('color')}' — use a plain name like red, or '#rrggbb'")
        for i in indices:
            out[i]["color"] = [round(c, 4) for c in rgba]
        what = f"painted {names} {args.get('color')}"
    elif op == "metal":
        on = args.get("metal", True)
        if type(on) is not bool:
            raise EditError("metal must be true or false")
        for i in indices:
            out[i]["metal"] = on
        what = f"made {names} {'metallic' if on else 'matte'}"
    elif op == "rename":
        new = " ".join(str(args.get("name") or "").split())[:60]
        if not new or len(indices) != 1:
            raise EditError("Rename one part at a time, with a new name.")
        out[indices[0]]["name"] = new
        what = f"renamed {names} to {new}"
    elif op == "remove":
        if len(indices) >= len(out):
            raise EditError("That would remove every part. Keep at least one, or build something new.")
        out = [p for i, p in enumerate(out) if i not in set(indices)]
        what = f"removed {names}"
    elif op == "duplicate":
        offset = _vec3(args["by"], "by (cm)") if args.get("by") is not None else None
        copies = []
        for i in indices:
            c = copy.deepcopy(out[i])
            shift = offset if offset is not None else [c["size"][0] + 2.0, 0.0, 0.0]
            c["at"] = [round(a + b, 3) for a, b in zip(c["at"], shift)]
            c["name"] = (args.get("name") or (c.get("name") or c["shape"]) + " copy")[:60]
            copies.append(c)
        out += copies
        what = f"duplicated {names}"
    elif op == "add":
        spec = args.get("part")
        if not isinstance(spec, dict):
            raise EditError("add needs a part: {shape, size, at, color, name}")
        out.append(spec)
        what = f"added {spec.get('name') or spec.get('shape')}"
    elif op == "shape":
        shape = str(args.get("shape") or "").lower()
        for i in indices:
            out[i]["shape"] = shape
            for k in ("teeth", "hole"):
                if k in args:
                    out[i][k] = args[k]
                elif k in out[i] and shape not in ("gear", "tube"):
                    out[i].pop(k)
        what = f"made {names} a {shape}"
    try:
        clean = build3d.validate(out)
    except build3d.BuildError as e:
        raise EditError(str(e)) from e
    return clean, what
