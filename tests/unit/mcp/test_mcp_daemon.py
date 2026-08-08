import asyncio
from unittest.mock import AsyncMock, patch

from rag_local.mcp.tools.daemon import manage_daemon
from rag_local.services.subprocess import SubprocessResult


def test_manage_daemon_invalid_action():
    mock_ctx = AsyncMock()
    result = asyncio.run(manage_daemon(ctx=mock_ctx, action="invalid_action"))
    assert "Acción inválida" in result


def test_manage_daemon_status():
    mock_ctx = AsyncMock()
    sub_res = SubprocessResult(
        returncode=0,
        stdout=b"WORKER_DAEMON: Activo\n  - Puerto: 8765\n  - Dispositivo: cuda",
        stderr=b"",
    )
    with (
        patch("rag_local.mcp.tools.daemon.setup_project_context"),
        patch("rag_local.mcp.tools.daemon.run_cli_subprocess", return_value=sub_res) as mock_sub,
    ):
        result = asyncio.run(manage_daemon(ctx=mock_ctx, action="status"))
        assert "WORKER_DAEMON: Activo" in result
        mock_sub.assert_called_once()
        cmd = mock_sub.call_args.kwargs.get("cmd") or mock_sub.call_args.args[0]
        assert "rag_local.cli.daemon" in cmd
        assert "status" in cmd


def test_manage_daemon_start():
    mock_ctx = AsyncMock()
    sub_res = SubprocessResult(
        returncode=0,
        stdout=b"[DAEMON] Worker Daemon iniciado con \xc3\xa9xito en http://127.0.0.1:8765",
        stderr=b"",
    )
    with (
        patch("rag_local.mcp.tools.daemon.setup_project_context"),
        patch("rag_local.mcp.tools.daemon.run_cli_subprocess", return_value=sub_res) as mock_sub,
    ):
        result = asyncio.run(manage_daemon(ctx=mock_ctx, action="start"))
        assert "iniciado con éxito" in result
        cmd = mock_sub.call_args.kwargs.get("cmd") or mock_sub.call_args.args[0]
        assert "start" in cmd
        assert "--parent-pid" in cmd


def test_manage_daemon_stop():
    mock_ctx = AsyncMock()
    sub_res = SubprocessResult(
        returncode=0,
        stdout=b"[DAEMON] Worker Daemon detenido.",
        stderr=b"",
    )
    with (
        patch("rag_local.mcp.tools.daemon.setup_project_context"),
        patch("rag_local.mcp.tools.daemon.run_cli_subprocess", return_value=sub_res) as mock_sub,
    ):
        result = asyncio.run(manage_daemon(ctx=mock_ctx, action="stop"))
        assert "detenido" in result
        cmd = mock_sub.call_args.kwargs.get("cmd") or mock_sub.call_args.args[0]
        assert "stop" in cmd
