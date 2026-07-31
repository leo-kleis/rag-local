import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.mcp.tools.graph import export_project_graph
from rag_local.services.subprocess import SubprocessResult


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


def test_export_project_graph_setup_error(mock_ctx):
    with patch(
        "rag_local.mcp.tools.graph.setup_project_context",
        side_effect=ValueError("Invalid repo path"),
    ):
        result = asyncio.run(
            export_project_graph(mock_ctx, project_path="/invalid/path")
        )
        assert "Error de configuración: Invalid repo path" in result


def test_export_project_graph_no_index(mock_ctx):
    with (
        patch("rag_local.mcp.tools.graph.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
    ):
        mock_db.exists.return_value = False
        result = asyncio.run(export_project_graph(mock_ctx))
        assert result.startswith("NO_INDEX:")


def test_export_project_graph_success(mock_ctx):
    sub_res = SubprocessResult(returncode=0, stdout=b"", stderr=b"")

    with (
        patch("rag_local.mcp.tools.graph.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch("rag_local.core.config.RAG_ROOT") as mock_rag,
        patch(
            "rag_local.mcp.tools.graph.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_db.__truediv__.side_effect = lambda filename: MagicMock(
            resolve=lambda: MagicMock(as_posix=lambda: f"/db/{filename}")
        )
        mock_repo.resolve.return_value = "/app/repo"
        mock_rag.__str__.return_value = "/app/rag"

        result = asyncio.run(export_project_graph(mock_ctx, project_path="/app/repo"))

        assert mock_sub.called
        cmd_used = mock_sub.call_args.kwargs["cmd"]
        assert cmd_used == [
            "uv",
            "run",
            "--project",
            mock_sub.call_args.kwargs["cmd"][3],
            "rag-graph",
            "--project-path",
            "/app/repo",
        ]
        assert "¡Grafo exportado con éxito" in result
        assert "3D Graph" in result
        assert "2D Graph" in result
        assert "Mermaid View" in result


def test_export_project_graph_stderr_progress(mock_ctx):
    sub_res = SubprocessResult(returncode=0, stdout=b"", stderr=b"")

    async def fake_run_sub(cmd, cwd, env, timeout, on_stderr_line):
        await on_stderr_line("Generando visualizaciones del proyecto...")
        return sub_res

    with (
        patch("rag_local.mcp.tools.graph.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.graph.run_cli_subprocess",
            side_effect=fake_run_sub,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_db.__truediv__.side_effect = lambda fn: MagicMock(
            resolve=lambda: MagicMock(as_posix=lambda: f"/db/{fn}")
        )
        mock_repo.resolve.return_value = "/app/repo"

        asyncio.run(export_project_graph(mock_ctx))

        mock_ctx.report_progress.assert_any_call(
            50, 100, message="Construyendo nodos y estructura de datos..."
        )


def test_export_project_graph_subprocess_failure(mock_ctx):
    sub_res = SubprocessResult(
        returncode=1, stdout=b"", stderr=b"Subprocess failed with error"
    )

    with (
        patch("rag_local.mcp.tools.graph.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.graph.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(export_project_graph(mock_ctx))
        assert "Error en graficado (código 1): Subprocess failed with error" in result


def test_export_project_graph_timeout(mock_ctx):
    with (
        patch("rag_local.mcp.tools.graph.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.graph.run_cli_subprocess",
            side_effect=TimeoutError(),
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(export_project_graph(mock_ctx))
        assert "La exportación superó el límite de tiempo" in result
