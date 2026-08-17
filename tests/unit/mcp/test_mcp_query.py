import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.mcp.tools.query import query_codebase
from rag_local.services.subprocess import SubprocessResult


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


def test_query_codebase_setup_error(mock_ctx):
    with patch(
        "rag_local.mcp.tools.query.setup_project_context",
        side_effect=ValueError("Invalid context"),
    ):
        result = asyncio.run(
            query_codebase(mock_ctx, query="test query", project_path="/app/repo")
        )
        assert "Error de configuración: Invalid context" in result


def test_query_codebase_no_index(mock_ctx):
    with (
        patch("rag_local.mcp.tools.query.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
    ):
        mock_db.exists.return_value = False
        result = asyncio.run(
            query_codebase(mock_ctx, query="test query", project_path="/app/repo")
        )
        assert "No existe una base de datos indexada" in result


def test_query_codebase_no_valid_roots(mock_ctx):
    with (
        patch("rag_local.mcp.tools.query.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(None, None, None, None),
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(
            query_codebase(mock_ctx, query="test query", project_path="/app/repo")
        )
        assert "El proyecto activo en el workspace no parece ser" in result


def test_query_codebase_success_with_results(mock_ctx):
    payload = {
        "context": "def authenticate(): pass",
        "retrieved_chunks": [{"source": "auth.py", "start_line": 10, "end_line": 25}],
    }
    json_bytes = json.dumps(payload).encode("utf-8")
    sub_res = SubprocessResult(returncode=0, stdout=json_bytes, stderr=b"")

    with (
        patch("rag_local.mcp.tools.query.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch("rag_local.core.config.RAG_ROOT") as mock_rag,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(None, None, MagicMock(), None),
        ),
        patch(
            "rag_local.mcp.tools.query.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"
        mock_rag.__str__.return_value = "/app/rag"

        result = asyncio.run(
            query_codebase(
                mock_ctx,
                query="auth function",
                project_path="/app/repo",
                scope="python",
            )
        )

        assert mock_sub.called
        cmd_used = mock_sub.call_args.kwargs["cmd"]
        assert "rag_local.cli.query" in cmd_used
        assert "--query" in cmd_used
        assert "auth function" in cmd_used
        assert "--scope" in cmd_used
        assert "python" in cmd_used

        assert "[Archivos relevantes: 1]" in result
        assert "auth.py (L10-25)" in result
        assert "def authenticate(): pass" in result


def test_query_codebase_no_context(mock_ctx):
    payload = {"context": "", "retrieved_chunks": []}
    json_bytes = json.dumps(payload).encode("utf-8")
    sub_res = SubprocessResult(returncode=0, stdout=json_bytes, stderr=b"")

    with (
        patch("rag_local.mcp.tools.query.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.query.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(
            query_codebase(
                mock_ctx, query="nonexistent topic", project_path="/app/repo"
            )
        )
        assert result.startswith("NO_CONTEXT:")


def test_query_codebase_invalid_json(mock_ctx):
    sub_res = SubprocessResult(
        returncode=0, stdout=b"Non-JSON string output", stderr=b""
    )

    with (
        patch("rag_local.mcp.tools.query.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.query.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(
            query_codebase(mock_ctx, query="test query", project_path="/app/repo")
        )
        assert "Error al parsear resultados JSON:" in result


def test_query_codebase_stderr_progress(mock_ctx):
    payload = {
        "context": "Context",
        "retrieved_chunks": [{"source": "f.py", "start_line": 1, "end_line": 2}],
    }
    sub_res = SubprocessResult(
        returncode=0, stdout=json.dumps(payload).encode("utf-8"), stderr=b""
    )

    async def fake_run_sub(cmd, cwd, env, timeout, on_stderr_line):
        await on_stderr_line("Analizando consulta...")
        await on_stderr_line("generando embeddings...")
        await on_stderr_line("Loading SentenceTransformer model...")
        await on_stderr_line("Loading weights...")
        await on_stderr_line("CONTEXTO RECUPERADO...")
        return sub_res

    with (
        patch("rag_local.mcp.tools.query.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.query.run_cli_subprocess",
            side_effect=fake_run_sub,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        asyncio.run(
            query_codebase(mock_ctx, query="test query", project_path="/app/repo")
        )

        mock_ctx.report_progress.assert_any_call(
            15, 100, message="Analizando consulta..."
        )
        mock_ctx.report_progress.assert_any_call(
            30, 100, message="Generando embeddings..."
        )
        mock_ctx.report_progress.assert_any_call(
            60, 100, message="Cargando modelos locales..."
        )
        mock_ctx.report_progress.assert_any_call(
            75, 100, message="Cargando pesos en GPU/CPU..."
        )
        mock_ctx.report_progress.assert_any_call(
            90, 100, message="Re-rankeando resultados..."
        )


def test_query_codebase_timeout(mock_ctx):
    with (
        patch("rag_local.mcp.tools.query.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.query.run_cli_subprocess",
            side_effect=TimeoutError(),
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(
            query_codebase(mock_ctx, query="test query", project_path="/app/repo")
        )
        assert "La búsqueda superó el límite de 5 minutos." in result


def test_query_codebase_failure(mock_ctx):
    sub_res = SubprocessResult(
        returncode=1, stdout=b"", stderr=b"Query execution error"
    )

    with (
        patch("rag_local.mcp.tools.query.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.query.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ),
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(
            query_codebase(mock_ctx, query="test query", project_path="/app/repo")
        )
        assert "Error en consulta (código 1): Query execution error" in result
