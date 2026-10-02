"""Start Apex's MCP server on stdio, for Claude Code, Codex, Claude Desktop or Cursor.

    python scripts/apex_mcp.py --print-config     the exact setup for Claude Code, Codex,
                                                  Claude Desktop and Cursor, with your paths

stdout carries the protocol, and Apex's modules print progress to stdout. So
the real stdout is kept for the protocol only and everything else written to
it (Python prints, native library chatter) is pointed at stderr, which MCP
clients show as the server's log.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def print_config():
    import json
    python, script = str(Path(sys.executable).absolute()), str(Path(__file__).resolve())   # not resolve(): a venv's python is a symlink to the base one
    entry = {'command': python, 'args': [script]}
    print('Claude Code (run once in a terminal):')
    print(f'  claude mcp add --scope user apex -- "{python}" "{script}"\n')
    print('Codex (add to ~/.codex/config.toml):')
    print('  [mcp_servers.apex]')
    print(f'  command = {json.dumps(python)}')
    print(f'  args = [{json.dumps(script)}]\n')
    print('Claude Desktop (claude_desktop_config.json) or Cursor (~/.cursor/mcp.json):')
    print('  ' + json.dumps({'mcpServers': {'apex': entry}}, indent=2).replace('\n', '\n  '))


def main():
    if '--print-config' in sys.argv:
        print_config()
        return
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8', newline='\n')
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    os.chdir(ROOT)                       # config.py reads .env from here
    sys.path.insert(0, str(ROOT))
    import anyio
    from mcp.server.stdio import stdio_server
    from agent.mcp_server import mcp

    async def serve():
        async with stdio_server(stdout=anyio.wrap_file(protocol)) as (read, write):
            await mcp._mcp_server.run(read, write, mcp._mcp_server.create_initialization_options())

    anyio.run(serve)


if __name__ == '__main__':
    main()
