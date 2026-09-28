"""MCP connection catalog with explicit setup and handshake states.

The tracked mcp_servers.json remains an example template. Installation writes
ignored mcp_servers.local.json, using credential placeholders; actual submitted
credentials go only to ignored .env. prepare() saves disabled connections without
starting OAuth. install() activates a connection only after initialize/list_tools.
Account access and downstream apps may still require further setup.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

# Each entry: what it is, how to launch it, and what it needs from you.
# `env` maps a variable name to a one-line description of what to paste there.
# An empty `env` means no pasted credential; OAuth or a local app may still be needed.
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
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}",
                    "X-MCP-Readonly": "true", "X-MCP-Toolsets": "repos,issues,pull_requests"},
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN":
                "A GitHub personal access token (Settings → Developer settings)."},
        "docs": "https://github.com/github/github-mcp-server",
    },
    {
        "id": "slack",
        "name": "Slack",
        "blurb": "Read channels, search, post messages.",
        "url": "https://mcp.slack.com/mcp",
        "headers": {"Authorization": "Bearer ${SLACK_MCP_ACCESS_TOKEN}"},
        "env": {"SLACK_MCP_ACCESS_TOKEN": "Slack MCP user OAuth access token from your approved internal or Marketplace app; not a bot token."},
        "note": "Requires Slack app OAuth setup and user consent. Renew the access token when it expires. Apex's tool policy still gates writes.",
        "docs": "https://docs.slack.dev/ai/slack-mcp-server/",
    },
    {
        "id": "notion", "name": "Notion",
        "blurb": "Search and work with your authorized Notion workspace.",
        "command": "npx", "args": ["-y", "mcp-remote@0.14.3", "https://mcp.notion.com/mcp"],
        "env": {},
        "note": "Click Connect and finish Notion authorization in the browser on the Apex host. OAuth credentials stay in .mcp-runtime/auth.",
        "docs": "https://developers.notion.com/guides/mcp/get-started-with-mcp",
    },
    {
        "id": "google-drive", "name": "Google Drive",
        "blurb": "Find and read files through Google's official remote MCP server.",
        "url": "https://drivemcp.googleapis.com/mcp/v1",
        "headers": {"Authorization": "Bearer ${GOOGLE_DRIVE_MCP_ACCESS_TOKEN}"},
        "env": {"GOOGLE_DRIVE_MCP_ACCESS_TOKEN": "Google OAuth access token with drive.readonly scope."},
        "note": "Developer Preview membership, a Cloud project with Drive and Drive MCP APIs enabled, and OAuth consent are required. Access tokens expire; this connector does not refresh them automatically.",
        "docs": "https://developers.google.com/workspace/guides/configure-mcp-servers",
    },
    {
        "id": "google-calendar", "name": "Google Calendar",
        "blurb": "Read calendars and events through Google's official remote MCP server.",
        "url": "https://calendarmcp.googleapis.com/mcp/v1",
        "headers": {"Authorization": "Bearer ${GOOGLE_CALENDAR_MCP_ACCESS_TOKEN}"},
        "env": {"GOOGLE_CALENDAR_MCP_ACCESS_TOKEN": "Google OAuth access token with Calendar read scopes."},
        "note": "Developer Preview membership, a Cloud project with Calendar and Calendar MCP APIs enabled, and OAuth consent are required. Access tokens expire; this connector does not refresh them automatically.",
        "docs": "https://developers.google.com/workspace/guides/configure-mcp-servers",
    },
    {
        "id": "blender", "name": "Blender",
        "blurb": "Inspect and edit Blender scenes through the community MCP add-on.",
        "command": "uvx", "args": ["--python", "3.11", "mcp-for-blender==2.1.1"],
        "env": {},
        "launch_env": {"BLENDER_HOST": "127.0.0.1", "BLENDER_PORT": "9876", "BLENDER_MCP_SAFE_MODE": "1", "BLENDER_MCP_DISABLE_TELEMETRY": "1"},
        "note": "Enable the matching Blender add-on and start its MCP server on localhost:9876, then Connect. Apex checks that the scene can be read before activating this connection. Telemetry is disabled.",
        "docs": "https://github.com/ahujasid/mcp-for-blender",
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

CONFIG_NAME = "mcp_servers.local.json"
DEFAULT_CONNECTIONS = ("github", "google-drive", "google-calendar", "notion", "slack", "blender")

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


def _configured(root: Optional[Path] = None) -> dict:
    base = root or Path.cwd()
    servers = {}
    for name in ('mcp_servers.json', '.mcp.json', CONFIG_NAME):
        servers.update(_load(base / name).get('mcpServers', {}))
    return servers


def installed(root: Optional[Path] = None) -> list[str]:
    """Server keys actually in effect — the `_`-prefixed examples are skipped by
    mcp_client, so counting them would overstate what is configured."""
    servers = _configured(root)
    return sorted(k for k, v in servers.items() if not k.startswith("_") and not v.get("disabled"))


def listing(root: Optional[Path] = None) -> list[dict]:
    """The catalogue, annotated with what is already installed and what each
    entry still needs from you."""
    have = set(installed(root))
    prepared = _configured(root)
    out = []
    for e in CATALOG:
        missing = [v for v in e["env"] if not os.getenv(v)]
        out.append({**e, "installed": e["id"] in have,
                    "prepared": e["id"] in prepared,
                    "setup_note": prepared.get(e["id"], {}).get("setup_note", ""),
                    "needs": list(e["env"]), "missing": missing})
    return out


def _check_secrets(values: dict) -> None:
    for key, val in (values or {}).items():
        v = str(val)
        if _PLACEHOLDER.match(v):
            continue
        if _SECRETISH.search(v):
            raise InstallRefused(
                f"'{key}' looks like a real credential. Configs may be tracked in git. Secrets "
                f"go in .env and are referenced as ${{{key}}} — which is what "
                f"install() writes for you.")


def launch_config(entry: dict, root: Optional[Path] = None) -> dict:
    root = (root or Path.cwd()).resolve()
    if entry.get("url"):
        return {"type": "http", "url": entry["url"], "headers": dict(entry.get("headers", {}))}
    import shutil
    launch = {"command": shutil.which(entry["command"]) or entry["command"],
              "args": list(entry["args"]),
              "env": {**entry.get("launch_env", {}), **{k: "${%s}" % k for k in entry["env"]}}}
    if entry["id"] == "filesystem":
        launch["args"][-1] = str(root)
    if entry["id"] == "notion":
        proxy = root / ".mcp-runtime" / "node" / "node_modules" / "mcp-remote" / "dist" / "proxy.js"
        if proxy.is_file():
            launch.update(command=shutil.which("node") or "node", args=[str(proxy), "https://mcp.notion.com/mcp"])
        launch["env"].update(MCP_REMOTE_CONFIG_DIR=str(root / ".mcp-runtime" / "auth"),
                             npm_config_cache=str(root / ".mcp-runtime" / "npm-cache"))
    if entry["id"] == "blender":
        launch['healthcheck'] = 'blender_scene'
        executable = root / ".mcp-runtime" / "blender" / ("Scripts/mcp-for-blender.exe" if os.name == "nt" else "bin/mcp-for-blender")
        if executable.is_file():
            launch.update(command=str(executable), args=[])
        launch["env"]["UV_CACHE_DIR"] = str(root / ".mcp-runtime" / "uv-cache")
    return launch


def prepare(server_ids=DEFAULT_CONNECTIONS, *, root: Optional[Path] = None) -> list[dict]:
    """Stage requested connections without starting OAuth or advertising tools.

    Existing user configuration is retained. Only install() can activate a
    staged entry, after a successful initialize/list_tools handshake.
    """
    path = config_path(root)
    data = _load(path)
    existing = _configured(root)
    for sid in server_ids:
        if sid not in CATALOG_BY_ID:
            raise InstallRefused(f"Unknown connection: {sid}")
    for sid in server_ids:
        if sid not in existing:
            entry = CATALOG_BY_ID[sid]
            data["mcpServers"][sid] = {**launch_config(entry, root), "disabled": True,
                "setup_note": entry.get("note") or "Add the required credentials in the MCP catalog, then Connect."}
    _save(path, data)
    return listing(root)


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
    unexpected = set(secrets) - set(entry["env"])
    if unexpected:
        raise InstallRefused("Unexpected credential fields: " + ", ".join(sorted(unexpected)))
    # Parse before saving credentials or launching a process.
    path = config_path(root)
    data = _load(path)
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

    launch = launch_config(entry, root)
    _check_secrets(launch.get("env", {}))

    ok, detail = (verify or _verify)(launch)
    if not ok:
        raise InstallRefused(
            f"{entry['name']} did not start: {detail}\n"
            f"Nothing was activated or changed in {CONFIG_NAME}. "
            "Complete the connection's setup and try again."
            + (f"\n({', '.join(written)} was saved to .env and will be reused.)"
               if written else ""))

    data["mcpServers"][entry["id"]] = launch
    _save(path, data)
    return {"ok": True, "id": entry["id"], "name": entry["name"],
            "saved_to_env": written,
            "note": "Handshake passed. Restart Apex to load this connection's tools. "
                    + (entry.get("note", "") if entry["id"] == "blender" else "")}


def uninstall(server_id: str, *, root: Optional[Path] = None) -> dict:
    """Remove an entry. Credentials in .env are deliberately left alone — this
    is a config change, and silently deleting a token you may use elsewhere is
    not something a Remove button should do."""
    path = config_path(root)
    data = _load(path)
    existing = _configured(root)
    if server_id not in existing:
        return {"ok": False, "error": f"'{server_id}' is not installed."}
    data["mcpServers"].pop(server_id, None)
    # Keep an inherited project connection disabled without rewriting its file.
    base = root or Path.cwd()
    if any(server_id in _load(base / name).get('mcpServers', {}) for name in ('mcp_servers.json', '.mcp.json')):
        data["mcpServers"][server_id] = {**existing[server_id], "disabled": True,
                                       "setup_note": "Disabled from the MCP catalog."}
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
