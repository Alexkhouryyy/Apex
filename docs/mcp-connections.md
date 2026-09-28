# Apex Apps and MCP connections

## Broad app catalog

Restart Apex after updating the code, then open **Apps & connections** in the
sidebar (`/apps`). Sign in with the owner dashboard token. Device tokens cannot
manage credentials or accounts.

1. Open **Catalog settings**, enter a Composio project API key, and choose
   **Verify & save key**. Get the key from https://platform.composio.dev.
2. Search **Discover**, filter by category, or load further pages. These are live
   provider results, not a hardcoded list or a promise that every app is enabled.
3. Choose **Connect** and complete sign-in in the new tab. Use **Continue sign-in**
   if the browser blocks the popup. Apex polls the provider to verify the account;
   returning from sign-in alone does not mark it connected.
4. **My apps** shows connection status, actions and their input schemas. Disable
   pauses Apex access; Enable checks the connection again; Disconnect removes
   the account from Composio. Revoke any remaining grant in the app's own settings
   if desired. A failed disconnect leaves Apex access disabled and can be retried.

The key stays in ignored `.env`; owner identity, session and account references
stay in ignored `.mcp-runtime/apps.json`. Back up both together if moving the same
Apex installation. Changing project keys creates a separate owner identity and
requires reconnecting apps. Accounts associated with the old project can be
removed in its Composio dashboard. Do not share these local files.

Composio handles hosted sign-in and token refresh. Its availability, supported
authentication methods, app permissions and plan limits apply. Some apps require
project-specific auth configuration. Premium actions and the provider's workbench,
proxy and autonomous connection tools are not exposed by Apex. No plan is bought
or upgraded by this integration.

The agent receives three discovery tools: `search_connected_tools`,
`describe_connected_tool`, and `call_connected_tool`. Schemas are fetched on demand
and cached briefly, rather than adding the whole catalog to every model request.
Large local MCP catalogs use the same path. An app outage still allows local MCP
discovery. Execution rechecks the enabled account and uses its specific account ID.

Existing MCP policy, audit and worker-role boundaries apply to underlying actions.
Managed app policy names use `mcp__apps_<app>__<action>`; for example,
`mcp__apps_github__delete_repository`. Configure `MCP_POLICY`, `MCP_ALLOW`, and
`MCP_DENY` in Control. Unknown actions require write handling; provider hints cannot
turn a write into a read. Writes are never automatically retried after a timeout.

This integrates the provider's catalog through its REST API alongside direct MCP
servers. It does not merge the OpenClaw or Hermes runtimes into Apex. Reference:
https://docs.composio.dev/examples/harness-integration.

## Navigation

Screen Companion, Spatial Workspace and Apps are primary destinations. The obsolete
Vision dashboard and its polling/avatar module were removed. Phone, Inbox, Calendar
and the duplicate Sub-agents shortcuts were removed from the main menu; their
existing backend data is preserved. Team remains prominent, while specialist and
diagnostic pages sit in a collapsed Advanced group. Optional graphics no longer
block the dashboard from loading when a CDN is slow.

Apex supports local stdio servers and remote HTTP/SSE servers. Account access
belongs to this Apex installation; connectors signed in inside Codex do not
automatically sign Apex in.

## Prepare and connect

From the Apex folder, using its Python environment:

```powershell
.\.venv\Scripts\python.exe -m scripts.setup_mcp prepare
.\.venv\Scripts\python.exe -m scripts.setup_mcp status
```

This stages GitHub, Google Drive, Google Calendar, Notion, Slack and Blender as
disabled connections in ignored `mcp_servers.local.json`. Existing project
connections are preserved. The tracked `mcp_servers.json` remains a template.

Restart Apex to load the updated code. Open **Apps → Local MCP**, complete
the connection's setup, and click **Connect**. Enter tokens only in the password
fields there or in Apex's ignored `.env`, never in chat or tracked config files.
After a successful connection, Apps reloads the running agent's MCP tools.
**Refresh status** in Local MCP also reloads them. If no agent is attached, the
page reports that Apex must be started before its tools can be used.

You can also connect from the Apex folder:

```powershell
.\.venv\Scripts\python.exe -m scripts.setup_mcp connect notion
.\.venv\Scripts\python.exe -m scripts.setup_mcp connect blender
```

The supported IDs are `github`, `google-drive`, `google-calendar`, `notion`,
`slack`, and `blender`. A failed check leaves a prepared connection disabled.
Removing an inherited project connection creates a local disabled override;
it does not rewrite the original file or delete credentials.

## Account and application setup

| Connection | Required setup | Current implementation |
|---|---|---|
| GitHub | Set `GITHUB_PERSONAL_ACCESS_TOKEN` with access to the repositories you want. | Official remote server; read-only repos, issues and pull requests. |
| Google Drive | Google Workspace Developer Preview access, Cloud project, Drive and Drive MCP APIs, OAuth consent; set `GOOGLE_DRIVE_MCP_ACCESS_TOKEN`. | Official remote server with bearer authentication. Prefer `drive.readonly`. |
| Google Calendar | Same preview/project setup, Calendar and Calendar MCP APIs; set `GOOGLE_CALENDAR_MCP_ACCESS_TOKEN`. | Official remote server. Use Calendar calendar-list, event-read and free/busy scopes. |
| Notion | Complete the browser OAuth flow when connecting, on the machine running Apex. | Official Notion endpoint via pinned `mcp-remote@0.14.3`. Cached authorization lives in ignored `.mcp-runtime/auth`. |
| Slack | An approved internal or Marketplace Slack app with user OAuth consent; set `SLACK_MCP_ACCESS_TOKEN`. | Official remote server. Requires a user access token with appropriate scopes, not a bot token. |
| Blender | Enable the matching MCP add-on in Blender, then click **Start MCP Server** in its sidebar. | Pinned community `mcp-for-blender==2.1.1`, loopback port 9876, safe mode enabled, telemetry disabled. |

Google and Slack bearer tokens expire. **This implementation does not obtain or
refresh them automatically.** Supply refreshed tokens through `.env` and restart
Apex, or remove/reconnect through the catalog. Google servers also require
Developer Preview membership; installing this catalog does not grant it.

Blender is checked with a read-only scene query after its MCP handshake. Installing
the server alone is insufficient. The add-on installer preserves a `.bak` when
updating an existing add-on. Enable only one copy of the MCP add-on in Blender.
Safe mode is a script check, not an operating-system sandbox.

The local Notion and Blender runtimes can be installed separately from Apex's
main dependencies:

```powershell
npm.cmd install --prefix .mcp-runtime/node --ignore-scripts --no-fund --no-audit mcp-remote@0.14.3
.\.venv\Scripts\python.exe -m venv .mcp-runtime/blender
.\.mcp-runtime\blender\Scripts\python.exe -m pip install mcp-for-blender==2.1.1
.\.mcp-runtime\blender\Scripts\mcp-for-blender.exe install-addon
```

The catalog prefers these local runtimes when present; otherwise it uses pinned
`npx`/`uvx` commands. On other platforms use the environment's `bin` directory.
MCP subprocesses receive the SDK's standard environment plus their explicit
config variables, not all of Apex's provider credentials. If a custom server
needs another variable (including proxy or certificate settings), reference it
explicitly in its `env` configuration.

Apex's existing MCP write policy and audit remain in effect. Installing a
connection does not authorize sending messages, changing events or editing
external accounts without the user's instruction.

## Team completion checks

Team submissions now accept an optional existing `goal_id`. Add completion
contracts to that goal first, then enter its ID in Constellation's Team form or
pass it to `start_team_task`. The task snapshots the criteria at submission and
checks for changes before and after verification.

Execution and verification have separate states. A completed model response is
not proof that the task succeeded. File and command checks can produce verified
or failed results; manual and model judgments remain pending. Team verification
does not start an extra, unbudgeted judge-model call. Commands retain the existing
Docker-only autonomous verification path; a missing sandbox cannot pass.

Tasks without criteria stay unverified. Interrupted runs stay unknown and are
never replayed automatically. Each terminal task stores a receipt with its run
ID, models, calls, cost, tool return states and verification state. Returning a
tool result does not itself mean success. Linking a goal never closes it
automatically.

This extends Apex's own execution and verification modules. It does not install
or merge OpenClaw or Hermes runtimes, nor provide their full feature sets.

## Maintainer sources

- [GitHub MCP server](https://github.com/github/github-mcp-server)
- [Google Workspace MCP setup](https://developers.google.com/workspace/guides/configure-mcp-servers)
- [Notion MCP connection guide](https://developers.notion.com/guides/mcp/get-started-with-mcp)
- [Slack MCP server](https://docs.slack.dev/ai/slack-mcp-server/)
- [Community MCP for Blender](https://github.com/ahujasid/mcp-for-blender)
