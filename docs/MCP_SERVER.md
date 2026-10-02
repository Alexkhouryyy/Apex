# Apex as an MCP server: your other AI tools share Apex's brain

Claude Code, Codex, Claude Desktop, Cursor and any other app that speaks MCP
can plug into Apex and read what it knows about you and your work. Without
it, each of them starts every session knowing nothing about you.

This is the other direction from [MCP_REMOTE.md](MCP_REMOTE.md), which is
about Apex *using* other servers. Here, Apex *is* the server.

## Set it up (Windows)

Run **`Setup-Apex-MCP.cmd`** in the Apex folder.
- It prints the exact setup for each tool, with your paths.
- If Claude Code is installed, it offers to add Apex there for you.

By hand, the server is just:

```
C:\Users\you\Apex\.venv\Scripts\python.exe  C:\Users\you\Apex\scripts\apex_mcp.py
```

| Tool | Where it goes |
| --- | --- |
| Claude Code | `claude mcp add --scope user apex -- "<python>" "<apex_mcp.py>"` |
| Codex | `~/.codex/config.toml`: `[mcp_servers.apex]` with `command` and `args` |
| Claude Desktop | `claude_desktop_config.json` → `mcpServers.apex` |
| Cursor | `~/.cursor/mcp.json` → `mcpServers.apex` |

Restart the tool afterwards. The tool starts the server itself when it needs
it, and the Apex dashboard doesn't have to be running. Both read the same
memory database on this computer.

**ChatGPT** isn't supported. Its connectors need a public HTTPS server with
OAuth login, and this server only runs locally, on purpose.

## What your tools can do

| Tool | What it gives |
| --- | --- |
| `context` | One call with everything Apex keeps in front of itself: your profile and preferences, important memories, goals, the current project, what's going on now, lessons learned, and the skills list. Tools are told to call it at the start of a task. |
| `recall` | Search Apex's long-term memory: a preference, a decision, a person, a past project. |
| `lessons` | What Apex has *measured* fails in its own tool use, with the evidence (for example 6/10 calls). |
| `skills` / `skill` | Apex's procedural skills (how-to guides), and the full text of one. |
| `search_files` | Passages from the files you added to Apex's knowledge base. |
| `remember` | Suggest a memory. It **waits for your approval** in Apex (dashboard → Approvals → *Waiting to be saved*). Nothing is saved until you approve it. |

Tools are also told that Apex's results are evidence about you, not
instructions that override you.

## What leaves your computer, honestly

These tools usually run on cloud models, so whatever a tool reads from Apex
goes to that model's provider (Anthropic for Claude Code, OpenAI for Codex),
as part of that conversation.

- **Removed first:** every result goes through Apex's credential filter
  (`working_context.redact`), which strips API keys, tokens, private keys and
  "password is …".
- **The filter has limits:** a secret written in a way it doesn't recognise
  still goes through. Don't keep secrets in Apex's memory.
- **Never exposed:**
  - `.env` and credentials;
  - MCP configuration;
  - audit tables;
  - your conversations;
  - your Obsidian vault.

  Files are only the ones you added to the knowledge base.
- **Every call is logged** to `~/.apex/mcp.log`: time, tool, query and size. So
  you can see which tool read what.

## Why `remember` can't write directly

A coding agent that reads a hostile web page can be tricked into "remembering"
an instruction. Apex's memories go into every future Apex prompt, so a
planted memory would keep acting on Apex long after that session. Staging
for your approval closes that hole: an outside tool can suggest a memory, but
only you can save it.

## Fixed along the way

- **Memory search without the embedding model ignored capitals.** If the
  embedding model isn't installed or can't load, Apex falls back to plain text
  matching. That fallback lowercased the memories but not the search, so
  "Jeep" never matched "jeep". It now matches.
- **Things staged for approval had nowhere to be approved.** Memories, notes,
  skills and goal proposals waiting for approval weren't shown anywhere in the
  dashboard, except emails in the inbox. They now appear in **Approvals →
  Waiting to be saved**.
