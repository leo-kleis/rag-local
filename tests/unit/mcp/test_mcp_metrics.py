import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.mcp.tools.metrics import get_code_metrics
from rag_local.services.subprocess import SubprocessResult


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


def test_get_code_metrics_setup_error(mock_ctx):
    with patch(
        "rag_local.mcp.tools.metrics.setup_project_context",
        side_effect=ValueError("Configuration error"),
    ):
        result = asyncio.run(get_code_metrics(mock_ctx))
        assert "Error de configuración: Configuration error" in result


def test_get_code_metrics_no_index(mock_ctx):
    with (
        patch("rag_local.mcp.tools.metrics.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
    ):
        mock_db.exists.return_value = False
        result = asyncio.run(get_code_metrics(mock_ctx))
        assert result.startswith("NO_INDEX:")


def test_get_code_metrics_success(mock_ctx):
    sub_res = SubprocessResult(
        returncode=0, stdout=b"Total LOC: 1500 lines\nCRITICAL: file1.py", stderr=b""
    )

    with (
        patch("rag_local.mcp.tools.metrics.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch("rag_local.core.config.RAG_ROOT") as mock_rag,
        patch(
            "rag_local.mcp.tools.metrics.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"
        mock_rag.__str__.return_value = "/app/rag"

        result = asyncio.run(get_code_metrics(mock_ctx, threshold=300))

        assert mock_sub.called
        cmd_used = mock_sub.call_args.args[0]
        assert "rag-loc" in cmd_used
        assert "--threshold" in cmd_used
        assert "300" in cmd_used
        assert "Total LOC: 1500 lines" in result


def test_get_code_metrics_failure(mock_ctx):
    sub_res = SubprocessResult(returncode=1, stdout=b"", stderr=b"Internal loc error")

    with (
        patch("rag_local.mcp.tools.metrics.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.metrics.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(get_code_metrics(mock_ctx))
        assert "ERROR (1): rag-loc fallo.\nInternal loc error" in result


def test_get_code_metrics_exception(mock_ctx):
    with (
        patch("rag_local.mcp.tools.metrics.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.metrics.run_cli_subprocess",
            side_effect=RuntimeError("Subprocess failed"),
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(get_code_metrics(mock_ctx))
        assert "Error al ejecutar rag-loc: Subprocess failed" in result
