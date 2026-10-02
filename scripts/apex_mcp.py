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


def self_test():
    """Start this server the way Claude Code would, against YOUR Apex memory,
    and time each read-only tool. Nothing is written. Says which step stalls."""
    import asyncio
    import time
    sys.path.insert(0, str(ROOT))
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve())], env=dict(os.environ))
    steps = [('initialize', None), ('list_tools', None), ('context', {}), ('recall', {'query': 'project'}),
             ('lessons', {}), ('skills', {})]

    failed = []

    async def run():
        async with stdio_client(params, errlog=sys.stderr) as (read, write):
            async with ClientSession(read, write) as session:
                for name, args in steps:
                    started = time.perf_counter()
                    print(f'  {name:<12}', end='', flush=True)
                    try:
                        if name == 'initialize':
                            call = session.initialize()
                        elif name == 'list_tools':
                            call = session.list_tools()
                        else:
                            call = session.call_tool(name, args)
                        result = await asyncio.wait_for(call, 90)
                    except asyncio.TimeoutError:
                        print(f'STALLED (no answer in 90 s). Send this output to Claude.')
                        return False
                    took = time.perf_counter() - started
                    error = getattr(result, 'isError', False)
                    print(f'{"ERROR" if error else "ok"}  {took:5.1f} s')
                    if error:
                        failed.append(name)
                        print('    ' + result.content[0].text[:300])
        return not failed

    print('Apex MCP self-test (read-only, your real memory):')
    ok = asyncio.run(run())
    print('All tools answered.' if ok else f"Not working yet{': ' + ', '.join(failed) + ' returned an error' if failed else ''}. Send this output to Claude.")
    return ok


def main():
    if '--print-config' in sys.argv:
        print_config()
        return
    if '--self-test' in sys.argv:
        sys.exit(0 if self_test() else 1)
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
