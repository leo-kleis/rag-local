from unittest.mock import patch

from fastmcp import FastMCP

from rag_local.mcp.server import main, mcp
from rag_local.services.locks import ProjectLockManager, lock_manager


def test_lock_manager_instance():
    assert isinstance(lock_manager, ProjectLockManager)


def test_mcp_instance():
    assert isinstance(mcp, FastMCP)
    assert mcp.name == "rag-local"


def test_main():
    with patch.object(mcp, "run") as mock_run:
        main()
        mock_run.assert_called_once_with(transport="stdio", show_banner=False)

