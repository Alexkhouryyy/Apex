"""Verify the MCP SDK selected by either install profile, with a real stdio server.

--install installs only the profile's MCP requirement and its dependencies;
use a fresh virtual environment to check the resolver without desktop packages.
"""
import argparse
import asyncio
import re
import subprocess
import sys
from pathlib import Path


def sdk_requirement(path):
    requirements = [line.split("#", 1)[0].strip() for line in Path(path).read_text().splitlines()]
    matches = [line for line in requirements if re.match(r"^mcp(?=[<=>!~\s\[]|$)", line)]
    if len(matches) != 1:
        raise ValueError(f"Expected one MCP SDK requirement in {path}.")
    return matches[0]


async def check():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import Tool
    from importlib.metadata import version

    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    assert Tool(name="echo", inputSchema=schema).inputSchema == schema
    server = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), "--serve"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            echo = next(tool for tool in tools.tools if tool.name == "echo")
            assert echo.inputSchema["properties"]["text"]["type"] == "string"
            result = await session.call_tool("echo", {"text": "apex-sdk-compatible"})
            assert not result.isError
            assert any(getattr(block, "text", "") == "apex-sdk-compatible" for block in result.content)
    print(f"PASS MCP {version('mcp')}: Tool.inputSchema, initialization, discovery and real stdio call")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", default="requirements.txt")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        from mcp.server.fastmcp import FastMCP
        server = FastMCP("apex-sdk-compatibility")
        @server.tool()
        def echo(text: str) -> str:
            return text
        server.run(transport="stdio")
        return
    requirement = sdk_requirement(args.requirements)
    if args.install:
        subprocess.run([sys.executable, "-m", "pip", "install", requirement], check=True)
    asyncio.run(asyncio.wait_for(check(), timeout=30))


if __name__ == "__main__":
    main()
