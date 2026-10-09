"""Controlled self-modification.

The agent can extend its own system prompt and register new Python tools
at runtime. All modifications persist to ~/.voice_agent_overlay.json so they
survive restarts.

Safety: every self-mod goes through the existing voice-confirm safety layer
(see agent/safety.py — patterns include "update_system_prompt" and
"register_new_tool").
"""
import json
import os
from pathlib import Path
from typing import Callable, Optional

OVERLAY_PATH = Path.home() / ".voice_agent_overlay.json"
BACKUP_PATH = Path.home() / ".voice_agent_overlay.backup.json"

_dynamic_tool_handlers: dict = {}  # tool_name -> Callable(inputs) -> str


def _load() -> dict:
    if not OVERLAY_PATH.exists():
        return {"prompt_addition": "", "tools": []}
    try:
        return json.loads(OVERLAY_PATH.read_text())
    except Exception:
        return {"prompt_addition": "", "tools": []}


def _save(overlay: dict) -> None:
    # backup the current before overwriting
    if OVERLAY_PATH.exists():
        try:
            BACKUP_PATH.write_text(OVERLAY_PATH.read_text())
        except Exception:
            pass
    OVERLAY_PATH.write_text(json.dumps(overlay, indent=2))


def get_prompt_addition() -> str:
    return _load().get("prompt_addition", "")


def get_dynamic_tools() -> list[dict]:
    """Return the tool definitions (as Claude-compatible dicts) for all dynamic tools."""
    overlay = _load()
    out = []
    for t in overlay.get("tools", []):
        out.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": t.get("input_schema", {"type": "object", "properties": {}, "required": []}),
        })
    return out


def load_dynamic_handlers() -> int:
    """Compile and register Python code for each dynamic tool. Returns count loaded."""
    overlay = _load()
    count = 0
    for t in overlay.get("tools", []):
        try:
            handler = _evaluated_handler(t["name"], t["code"]) if t.get("evaluation_required") else _compile_handler(t["name"], t["code"])
            _dynamic_tool_handlers[t["name"]] = handler
            count += 1
        except Exception as e:
            print(f"[SelfMod] Failed to compile {t['name']}: {e}")
    return count


def _compile_handler(name: str, code: str) -> Callable:
    """Compile a Python function called `run(inputs)` from a code string."""
    ns: dict = {}
    exec(code, ns)
    if "run" not in ns or not callable(ns["run"]):
        raise ValueError(f"Tool {name!r} code must define a `def run(inputs):` function")
    return ns["run"]


def _evaluated_handler(name: str, code: str) -> Callable:
    """Guard before module execution and again before each evaluated tool call."""
    from agent import learning_registry
    learning_registry.require_evaluated(name, code)
    compiled = _compile_handler(name, code)

    def guarded(inputs):
        learning_registry.require_evaluated(name, code)
        return compiled(inputs)
    return guarded


def update_system_prompt(addition: str, replace: bool = False) -> str:
    """Append (or replace) the user-defined addition to the system prompt."""
    overlay = _load()
    if replace:
        overlay["prompt_addition"] = addition
    else:
        existing = overlay.get("prompt_addition", "")
        sep = "\n\n" if existing else ""
        overlay["prompt_addition"] = existing + sep + addition
    _save(overlay)
    return f"Prompt overlay updated ({len(overlay['prompt_addition'])} chars). Takes effect on next turn."


def register_new_tool(name: str, description: str, input_schema: dict, code: str, *, evaluation_required=False) -> str:
    """Register a Python tool. `code` must define `def run(inputs): -> str`."""
    if not name.isidentifier():
        return f"Invalid tool name: {name!r}"
    if name.startswith("mcp__") or name in {"screenshot", "bash"}:
        return f"Reserved name: {name!r}"
    # Validate code compiles and defines run()
    try:
        handler = _evaluated_handler(name, code) if evaluation_required else _compile_handler(name, code)
    except Exception as e:
        return f"Tool code invalid: {e}"

    overlay = _load()
    # Replace if name exists
    overlay["tools"] = [t for t in overlay.get("tools", []) if t["name"] != name]
    overlay["tools"].append({
        "name": name,
        "description": description,
        "input_schema": input_schema,
        "code": code,
        "evaluation_required": evaluation_required,
    })
    _save(overlay)
    _dynamic_tool_handlers[name] = handler
    return f"Registered dynamic tool {name!r}. Available immediately."


def dispatch(name: str, inputs: dict) -> Optional[str]:
    """If `name` is a dynamic tool, call its handler and return result. Else None."""
    handler = _dynamic_tool_handlers.get(name)
    if handler is None:
        return None
    try:
        return str(handler(inputs))
    except Exception as e:
        return f"Dynamic tool {name!r} error: {e}"


def revert(restore_backup: bool = False) -> str:
    """Clear all self-mods (or restore the previous backup)."""
    if restore_backup and BACKUP_PATH.exists():
        OVERLAY_PATH.write_text(BACKUP_PATH.read_text())
        _dynamic_tool_handlers.clear()
        load_dynamic_handlers()
        return "Restored previous overlay from backup."
    # Hard reset
    overlay = {"prompt_addition": "", "tools": []}
    _save(overlay)
    _dynamic_tool_handlers.clear()
    return "All self-modifications cleared."


def show() -> dict:
    overlay = _load()
    return {
        "prompt_addition_chars": len(overlay.get("prompt_addition", "")),
        "prompt_addition_preview": overlay.get("prompt_addition", "")[:300],
        "dynamic_tools": [t["name"] for t in overlay.get("tools", [])],
    }
