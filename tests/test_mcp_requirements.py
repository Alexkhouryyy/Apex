"""Both install profiles must select the MCP API Apex's adapter uses."""
from pathlib import Path

from packaging.requirements import Requirement
from scripts.check_mcp_sdk import sdk_requirement


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_and_cloud_select_the_same_supported_sdk():
    desktop = Requirement(sdk_requirement(ROOT / "requirements.txt"))
    cloud = Requirement(sdk_requirement(ROOT / "requirements-cloud.txt"))
    assert desktop.specifier == cloud.specifier
    for profile in (desktop, cloud):
        assert profile.specifier.contains("1.10.0")
        assert not profile.specifier.contains("1.0.0")
        assert not profile.specifier.contains("2.0.0")
        assert not profile.specifier.contains("2.3.0")


def test_installed_tool_model_matches_discovery_adapter():
    from mcp.types import Tool
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    assert Tool(name="search", inputSchema=schema).inputSchema == schema
