import asyncio

from rag_local.mcp.server import mcp

EXPECTED_TOOLS = {
    "get_config",
    "export_project_graph",
    "ingest_codebase",
    "get_code_metrics",
    "get_project_map",
    "query_codebase",
    "get_styles_map",
}


def test_all_seven_mcp_tools_registered():
    """Verify that FastMCP server registers exactly all 7 expected tools."""
    tools = asyncio.run(mcp.list_tools())
    registered_names = {t.name for t in tools}

    assert registered_names == EXPECTED_TOOLS, (
        f"Mismatch in registered MCP tools. Expected: {EXPECTED_TOOLS}, Got: {registered_names}"
    )


def test_mcp_tool_metadata():
    """Verify metadata (description and name) of each registered tool."""
    tools = asyncio.run(mcp.list_tools())
    tool_map = {t.name: t for t in tools}

    for name in EXPECTED_TOOLS:
        assert name in tool_map
        tool = tool_map[name]
        assert tool.description is not None and len(tool.description.strip()) > 0
