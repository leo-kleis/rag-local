import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.mcp.tools.ingest import ingest_codebase
from rag_local.services.subprocess import SubprocessResult


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


def test_ingest_codebase_setup_error(mock_ctx):
    with patch(
        "rag_local.mcp.tools.ingest.setup_project_context",
        side_effect=ValueError("Bad path"),
    ):
        result = asyncio.run(ingest_codebase(mock_ctx))
        assert "Error de configuración: Bad path" in result


def test_ingest_codebase_no_valid_roots(mock_ctx):
    with (
        patch("rag_local.mcp.tools.ingest.setup_project_context"),
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(None, None, None, None),
        ),
    ):
        mock_repo.resolve.return_value = "/app/repo"
        result = asyncio.run(ingest_codebase(mock_ctx))
        assert "No se detectó un proyecto de Angular" in result


def test_ingest_codebase_success(mock_ctx):
    sub_res = SubprocessResult(
        returncode=0, stdout=b"Files processed", stderr=b"Stats summary"
    )

    with (
        patch("rag_local.mcp.tools.ingest.setup_project_context"),
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch("rag_local.core.config.RAG_ROOT") as mock_rag,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.ingest.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_repo.resolve.return_value = "/app/repo"
        mock_rag.__str__.return_value = "/app/rag"

        result = asyncio.run(ingest_codebase(mock_ctx, force=False))

        assert mock_sub.called
        cmd_used = mock_sub.call_args.kwargs["cmd"]
        assert "rag-ingest" in cmd_used
        assert "--force" not in cmd_used
        assert "Ingesta completada de forma exitosa." in result


def test_ingest_codebase_force_flag(mock_ctx):
    sub_res = SubprocessResult(returncode=0, stdout=b"OK", stderr=b"Done")

    with (
        patch("rag_local.mcp.tools.ingest.setup_project_context"),
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.ingest.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_repo.resolve.return_value = "/app/repo"

        asyncio.run(ingest_codebase(mock_ctx, force=True))

        cmd_used = mock_sub.call_args.kwargs["cmd"]
        assert "--force" in cmd_used


def test_ingest_codebase_stderr_progress_tracking(mock_ctx):
    sub_res = SubprocessResult(returncode=0, stdout=b"OK", stderr=b"Done")

    async def fake_run_sub(cmd, cwd, env, timeout, on_stderr_line):
        await on_stderr_line("1. Escaneando archivos...")
        await on_stderr_line("2. Procesando metadatos...")
        await on_stderr_line("3. Indexando en LanceDB...")
        await on_stderr_line("Lote 2/4")
        await on_stderr_line("¡Ingesta completada!")
        return sub_res

    with (
        patch("rag_local.mcp.tools.ingest.setup_project_context"),
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.ingest.run_cli_subprocess",
            side_effect=fake_run_sub,
        ),
    ):
        mock_repo.resolve.return_value = "/app/repo"

        asyncio.run(ingest_codebase(mock_ctx))

        mock_ctx.report_progress.assert_any_call(
            10, 100, message="Escaneando archivos..."
        )
        mock_ctx.report_progress.assert_any_call(
            20, 100, message="Procesando archivos..."
        )
        mock_ctx.report_progress.assert_any_call(
            30, 100, message="Iniciando indexación..."
        )
        mock_ctx.report_progress.assert_any_call(
            62, 100, message="Indexando lote 2/4..."
        )
        mock_ctx.report_progress.assert_any_call(
            100, 100, message="¡Ingesta completada!"
        )


def test_ingest_codebase_failure(mock_ctx):
    sub_res = SubprocessResult(returncode=2, stdout=b"", stderr=b"Fatal ingest error")

    with (
        patch("rag_local.mcp.tools.ingest.setup_project_context"),
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.ingest.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ),
    ):
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(ingest_codebase(mock_ctx))
        assert "Error en la ingesta (código 2): Fatal ingest error" in result


def test_ingest_codebase_timeout(mock_ctx):
    with (
        patch("rag_local.mcp.tools.ingest.setup_project_context"),
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(MagicMock(), None, None, None),
        ),
        patch(
            "rag_local.mcp.tools.ingest.run_cli_subprocess",
            side_effect=TimeoutError(),
        ),
    ):
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(ingest_codebase(mock_ctx))
        assert "superó el tiempo límite de 5 minutos" in result
