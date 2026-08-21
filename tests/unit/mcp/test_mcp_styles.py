import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.mcp.tools.styles import get_styles_map
from rag_local.services.subprocess import SubprocessResult


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


def test_get_styles_map_setup_error(mock_ctx):
    with patch(
        "rag_local.mcp.tools.styles.setup_project_context",
        side_effect=ValueError("Setup error"),
    ):
        result = asyncio.run(get_styles_map(mock_ctx, project_path="/app/repo"))
        assert "Error de configuración: Setup error" in result


def test_get_styles_map_no_index(mock_ctx):
    with (
        patch("rag_local.mcp.tools.styles.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
    ):
        mock_db.exists.return_value = False
        result = asyncio.run(get_styles_map(mock_ctx, project_path="/app/repo"))
        assert result.startswith("NO_INDEX:")


def test_get_styles_map_success(mock_ctx):
    sub_res = SubprocessResult(
        returncode=0,
        stdout=b"CSS Variables:\n--primary: #fff\nUnused CSS classes:\n.btn-unused",
        stderr=b"",
    )

    with (
        patch("rag_local.mcp.tools.styles.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch("rag_local.core.config.RAG_ROOT") as mock_rag,
        patch(
            "rag_local.mcp.tools.styles.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"
        mock_rag.__str__.return_value = "/app/rag"

        result = asyncio.run(get_styles_map(mock_ctx, project_path="/app/repo"))

        assert mock_sub.called
        cmd_used = mock_sub.call_args.args[0]
        assert "rag_local.cli.styles" in cmd_used
        assert "--primary: #fff" in result
        assert ".btn-unused" in result


def test_get_styles_map_failure(mock_ctx):
    sub_res = SubprocessResult(returncode=1, stdout=b"", stderr=b"Styles parsing error")

    with (
        patch("rag_local.mcp.tools.styles.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.styles.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(get_styles_map(mock_ctx, project_path="/app/repo"))
        assert "ERROR (1): rag-styles fallo.\nStyles parsing error" in result


def test_get_styles_map_exception(mock_ctx):
    with (
        patch("rag_local.mcp.tools.styles.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.styles.run_cli_subprocess",
            side_effect=RuntimeError("Process execution error"),
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(get_styles_map(mock_ctx, project_path="/app/repo"))
        assert "Error al ejecutar rag-styles: Process execution error" in result
