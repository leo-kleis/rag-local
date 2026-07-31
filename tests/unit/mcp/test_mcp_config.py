import asyncio
from unittest.mock import MagicMock, patch

from rag_local.mcp.tools.config import get_config


def test_get_config_configured():
    """Test get_config when GEMINI_API_KEY is present and database is indexed."""
    with (
        patch("rag_local.mcp.tools.config.setup_project_context") as mock_setup,
        patch("rag_local.core.config.GEMINI_API_KEY", "secret_key"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db_path,
    ):
        mock_db_path.exists.return_value = True
        mock_db_path.iterdir.return_value = [MagicMock()]

        result = asyncio.run(get_config())

        mock_setup.assert_called_once_with(None)
        assert "GEMINI_API_KEY: Configurada" in result
        assert "LANCEDB_INDEXADA: Sí" in result


def test_get_config_not_configured():
    """Test get_config when GEMINI_API_KEY is missing and database is not indexed."""
    with (
        patch("rag_local.mcp.tools.config.setup_project_context") as mock_setup,
        patch("rag_local.core.config.GEMINI_API_KEY", None),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db_path,
    ):
        mock_db_path.exists.return_value = False

        result = asyncio.run(get_config(project_path="/custom/repo"))

        mock_setup.assert_called_once_with("/custom/repo")
        assert "GEMINI_API_KEY: No configurada" in result
        assert "LANCEDB_INDEXADA: No (ejecuta ingest_codebase)" in result
