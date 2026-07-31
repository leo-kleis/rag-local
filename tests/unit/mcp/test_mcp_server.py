import asyncio
from unittest.mock import patch

from fastmcp import FastMCP

from rag_local.mcp.server import get_lock, main, mcp


def test_get_lock():
    lock1 = get_lock()
    lock2 = get_lock()
    assert isinstance(lock1, asyncio.Lock)
    assert lock1 is lock2


def test_mcp_instance():
    assert isinstance(mcp, FastMCP)
    assert mcp.name == "rag-local"


def test_main():
    with patch.object(mcp, "run") as mock_run:
        main()
        mock_run.assert_called_once_with(show_banner=False)
