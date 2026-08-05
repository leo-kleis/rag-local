import asyncio
from unittest.mock import MagicMock, patch

from rag_local.mcp.tools.config import get_config


from rag_local.services.subprocess import SubprocessResult


def test_get_config_configured():
    """Test get_config when database is indexed."""
    sub_res = SubprocessResult(
        returncode=0,
        stdout=b"[RAG Configuration & Index Status]\nProyecto: /app/repo\nIndexado: S\xc3\xad\nEsquema RAG: 1.0.0 (Actualizada)\nModelo Embeddings: test-model\nTotal Chunks: 50",
        stderr=b"",
    )
    with (
        patch("rag_local.mcp.tools.config.setup_project_context") as mock_setup,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.config.run_cli_subprocess",
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_repo.resolve.return_value = "/app/repo"
        result = asyncio.run(get_config())

        mock_setup.assert_called_once_with(None)
        assert mock_sub.called
        cmd = mock_sub.call_args.args[0] if mock_sub.call_args.args else mock_sub.call_args.kwargs.get("cmd")
        assert "rag_local.cli.config" in cmd
        assert "[RAG Configuration & Index Status]" in result


def test_get_config_not_configured():
    """Test get_config when database is not indexed."""
    sub_res = SubprocessResult(
        returncode=0,
        stdout=b"[RAG Configuration & Index Status]\nProyecto: /custom/repo\nIndexado: No (ejecuta ingest_codebase)\nEsquema RAG: No indexado",
        stderr=b"",
    )
    with (
        patch("rag_local.mcp.tools.config.setup_project_context") as mock_setup,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.config.run_cli_subprocess",
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_repo.resolve.return_value = "/custom/repo"
        result = asyncio.run(get_config(project_path="/custom/repo"))

        mock_setup.assert_called_once_with("/custom/repo")
        assert mock_sub.called
        assert "[RAG Configuration & Index Status]" in result
