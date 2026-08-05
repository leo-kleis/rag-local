import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.mcp.tools.project_map import get_project_map
from rag_local.services.subprocess import SubprocessResult


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


def test_get_project_map_setup_error(mock_ctx):
    with patch(
        "rag_local.mcp.tools.project_map.setup_project_context",
        side_effect=ValueError("Setup error"),
    ):
        result = asyncio.run(get_project_map(mock_ctx))
        assert "Error de configuración: Setup error" in result


def test_get_project_map_no_index(mock_ctx):
    with (
        patch("rag_local.mcp.tools.project_map.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
    ):
        mock_db.exists.return_value = False
        result = asyncio.run(get_project_map(mock_ctx))
        assert result.startswith("NO_INDEX:")


def test_get_project_map_success(mock_ctx):
    sub_res = SubprocessResult(
        returncode=0, stdout=b"# Project Map\n- Services: AuthService", stderr=b""
    )

    with (
        patch("rag_local.mcp.tools.project_map.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch("rag_local.core.config.RAG_ROOT") as mock_rag,
        patch(
            "rag_local.mcp.tools.project_map.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"
        mock_rag.__str__.return_value = "/app/rag"

        result = asyncio.run(get_project_map(mock_ctx, project_path="/app/repo"))

        assert mock_sub.called
        cmd_used = mock_sub.call_args.kwargs["cmd"]
        assert "rag_local.cli.project_map" in cmd_used
        assert "# Project Map\n- Services: AuthService" in result


def test_get_project_map_stderr_progress(mock_ctx):
    sub_res = SubprocessResult(returncode=0, stdout=b"Map result", stderr=b"")

    async def fake_run_sub(cmd, cwd, env, timeout, on_stderr_line):
        await on_stderr_line("Leyendo metadatos del proyecto...")
        return sub_res

    with (
        patch("rag_local.mcp.tools.project_map.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.project_map.run_cli_subprocess",
            side_effect=fake_run_sub,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        asyncio.run(get_project_map(mock_ctx))

        mock_ctx.report_progress.assert_any_call(
            50, 100, message="Leyendo metadatos del índice..."
        )


def test_get_project_map_timeout(mock_ctx):
    with (
        patch("rag_local.mcp.tools.project_map.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.project_map.run_cli_subprocess",
            side_effect=TimeoutError(),
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(get_project_map(mock_ctx))
        assert "El mapeo superó el límite de tiempo de 1 minuto." in result


def test_get_project_map_failure(mock_ctx):
    sub_res = SubprocessResult(returncode=1, stdout=b"", stderr=b"Mapping failure")

    with (
        patch("rag_local.mcp.tools.project_map.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.project_map.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(get_project_map(mock_ctx))
        assert "Error en mapeo (código 1): Mapping failure" in result
