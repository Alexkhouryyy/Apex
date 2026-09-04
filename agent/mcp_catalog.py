"""A catalogue of MCP servers you can add without editing JSON.

Apex could always *use* any MCP server. It had no way to *add* one except
hand-editing `mcp_servers.json` with the correct npm package name, which is why
the shipped file still contained nothing but `_example_` entries — and
`mcp_client._load_mcp_configs` skips every key starting with `_`, so the honest
count of configured servers was zero.

## The problem this file had to solve first

`mcp_servers.json` is **tracked in git**. Writing a Slack token into it would
commit the token, and the first person to do that would not find out until it
was in the history. So nothing here ever writes a secret value into that file.

Servers that need credentials get an **indirection**:

    "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}"}

The `${...}` is expanded from the environment at launch (see
`mcp_client._expand_env`), and the actual value goes to `.env`, which is
git-ignored, through `scripts/set_env_key.py`. The config file stays safe to
commit by construction rather than by everyone remembering.

`install()` refuses a literal-looking secret outright, because "be careful" is
not a mechanism.

## The catalogue is a starting point, not an authority

Package names move, projects are renamed, and a catalogue is a snapshot of the
day it was written. So `install()` **launches the server and waits for it to
answer** before writing anything. A wrong package name fails at the moment you
click, with the error, instead of becoming a silently broken entry that looks
configured — the exact failure this codebase keeps finding.

Which also means: entries here need no maintenance to stay *honest*. They can
go stale and the install will simply refuse and say so.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

# Each entry: what it is, how to launch it, and what it needs from you.
# `env` maps a variable name to a one-line description of what to paste there.
# An empty `env` means it needs no credential and installing it is one click.
CATALOG: list[dict] = [
    {
        "id": "filesystem",
        "name": "Filesystem",
        "blurb": "Read and write files in folders you choose.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "~"],
        "env": {},
        "note": "Edit the last argument to pick which folders it may touch. "
                "It gets exactly those and nothing else.",
        "docs": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "github",
        "name": "GitHub",
        "blurb": "Issues, pull requests, code search, releases.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN":
                "A GitHub personal access token (Settings → Developer settings)."},
        "docs": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "slack",
        "name": "Slack",
        "blurb": "Read channels, search, post messages.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": {"SLACK_BOT_TOKEN": "A bot token, starting xoxb-.",
                "SLACK_TEAM_ID": "Your workspace id, starting T."},
        "docs": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "brave-search",
        "name": "Brave Search",
        "blurb": "Web search that does not need a browser.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": "A free key from brave.com/search/api."},
        "docs": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "postgres",
        "name": "PostgreSQL",
        "blurb": "Query a Postgres database, read-only.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres",
                 "${POSTGRES_URL}"],
        "env": {"POSTGRES_URL": "postgresql://user:pass@host:5432/dbname"},
        "docs": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "puppeteer",
        "name": "Puppeteer",
        "blurb": "Drive a real browser — click, fill, screenshot.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "env": {},
        "note": "Apex already has its own browser tools; this is for pages "
                "those cannot reach.",
        "docs": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "home-assistant",
        "name": "Home Assistant",
        "blurb": "Lights, locks, sensors, scenes.",
        "command": "uvx",
        "args": ["mcp-server-home-assistant"],
        "env": {"HASS_URL": "http://homeassistant.local:8123",
                "HASS_TOKEN": "A long-lived access token from your HA profile."},
        "note": "Apex also has native IoT support with its own kill switch — "
                "see IOT_ENABLED. This is the MCP route to the same thing.",
        "docs": "https://github.com/modelcontextprotocol/servers",
    },
]

CATALOG_BY_ID = {e["id"]: e for e in CATALOG}

CONFIG_NAME = "mcp_servers.json"

# What a credential tends to look like. Used to refuse writing one into a
# tracked file, never to validate one — a token that does not match these is
# still a token, which is why the rule is "no literal values at all" and this
# only sharpens the error message.
_SECRETISH = re.compile(
    r"^(xox[baprs]-|ghp_|github_pat_|sk-|secret_|Bearer\s|eyJ)|"
    r"^[A-Za-z0-9_\-]{32,}$")

_PLACEHOLDER = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class InstallRefused(ValueError):
    """Refused for a reason the caller has to fix, not retry."""


def config_path(root: Optional[Path] = None) -> Path:
    return (root or Path.cwd()) / CONFIG_NAME


def _load(path: Path) -> dict:
    if not path.exists():
        return {"mcpServers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise InstallRefused(
            f"{path.name} is not valid JSON ({e}). Refusing to rewrite it — "
            f"that would throw away whatever is in there.")
    data.setdefault("mcpServers", {})
    return data


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def installed(root: Optional[Path] = None) -> list[str]:
    """Server keys actually in effect — the `_`-prefixed examples are skipped by
    mcp_client, so counting them would overstate what is configured."""
    servers = _load(config_path(root)).get("mcpServers", {})
    return sorted(k for k in servers if not k.startswith("_"))


def listing(root: Optional[Path] = None) -> list[dict]:
    """The catalogue, annotated with what is already installed and what each
    entry still needs from you."""
    have = set(installed(root))
    out = []
    for e in CATALOG:
        missing = [v for v in e["env"] if not os.getenv(v)]
        out.append({**e, "installed": e["id"] in have,
                    "needs": list(e["env"]), "missing": missing})
    return out


def _check_secrets(values: dict) -> None:
    for key, val in (values or {}).items():
        v = str(val)
        if _PLACEHOLDER.match(v):
            continue
        if _SECRETISH.search(v):
            raise InstallRefused(
                f"'{key}' looks like a real credential. {CONFIG_NAME} is tracked "
                f"in git, so a value written there would be committed. Secrets "
                f"go in .env and are referenced as ${{{key}}} — which is what "
                f"install() writes for you.")


def install(server_id: str, secrets: Optional[dict] = None, *,
            root: Optional[Path] = None, verify=None) -> dict:
    """Add one catalogue entry, after proving it starts.

    `secrets` are written to `.env`, never to the config file. The config gets
    `${VAR}` placeholders, so it stays safe to commit whatever anyone does next.
    """
    entry = CATALOG_BY_ID.get(str(server_id or "").strip())
    if not entry:
        raise InstallRefused(f"'{server_id}' is not in the catalogue.")

    secrets = {k: str(v).strip() for k, v in (secrets or {}).items()
               if str(v).strip()}
    missing = [v for v in entry["env"] if v not in secrets and not os.getenv(v)]
    if missing:
        raise InstallRefused(
            f"{entry['name']} needs {', '.join(missing)}. Fill those in and try "
            f"again — installing it without them would add a server that fails "
            f"to start every time Apex boots.")

    # Credentials to .env FIRST. If the launch check then fails, the key is
    # already saved and a retry does not ask for it again.
    from scripts.set_env_key import set_key
    env_path = (root or Path.cwd()) / ".env"
    written = []
    for key, val in secrets.items():
        set_key(env_path, key, val)
        os.environ[key] = val          # so the verify below can see it
        written.append(key)

    launch = {"command": entry["command"], "args": list(entry["args"]),
              "env": {k: "${%s}" % k for k in entry["env"]}}
    _check_secrets(launch["env"])

    ok, detail = (verify or _verify)(launch)
    if not ok:
        raise InstallRefused(
            f"{entry['name']} did not start: {detail}\n"
            f"Nothing was added to {CONFIG_NAME}. A catalogue entry is a "
            f"snapshot of the day it was written — package names move — so this "
            f"is checked rather than assumed."
            + (f"\n({', '.join(written)} was saved to .env and will be reused.)"
               if written else ""))

    path = config_path(root)
    data = _load(path)
    data["mcpServers"][entry["id"]] = launch
    _save(path, data)
    return {"ok": True, "id": entry["id"], "name": entry["name"],
            "saved_to_env": written,
            "note": "Restart Apex, or press Refresh on the Control tab, to "
                    "connect to it."}


def uninstall(server_id: str, *, root: Optional[Path] = None) -> dict:
    """Remove an entry. Credentials in .env are deliberately left alone — this
    is a config change, and silently deleting a token you may use elsewhere is
    not something a Remove button should do."""
    path = config_path(root)
    data = _load(path)
    if server_id not in data.get("mcpServers", {}):
        return {"ok": False, "error": f"'{server_id}' is not installed."}
    data["mcpServers"].pop(server_id)
    _save(path, data)
    return {"ok": True, "id": server_id,
            "note": "Removed. Any credentials stay in .env — delete them there "
                    "if you want them gone."}


def _verify(launch: dict, timeout: float = 180.0) -> tuple:
    """Start the server and wait for an MCP handshake. (ok, detail)."""
    try:
        from agent import mcp_client
        return mcp_client.probe(launch, timeout=timeout)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
