from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from rag_local.core import config
from rag_local.services.project import setup_project_context


def test_setup_project_context_empty_path():
    with pytest.raises(ValueError, match="El parámetro 'project_path' es obligatorio"):
        setup_project_context("")

    with pytest.raises(ValueError, match="El parámetro 'project_path' es obligatorio"):
        setup_project_context("   ")


def test_setup_project_context_system_path():
    with pytest.raises(ValueError, match="Acceso denegado"):
        setup_project_context("C:\\Windows\\System32")


def test_setup_project_context_nonexistent_path():
    with pytest.raises(FileNotFoundError):
        setup_project_context("C:\\fake_nonexistent_project_dir_xyz_123")


def test_setup_project_context_valid_dir():
    target = Path("C:/my_valid_workspace/repo")
    with (
        patch(
            "rag_local.services.scanner.detect_project_roots",
            return_value=(None, None, None, None),
        ),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "is_dir", return_value=True),
    ):
        setup_project_context("C:/my_valid_workspace/repo")

        assert config.REPO_ROOT == target.resolve()
        assert config.LANCEDB_PATH == (target / ".lancedb").resolve()
