from unittest.mock import patch

from rag_local.core import config
from rag_local.services.fast_sync import fast_check_and_refresh
from rag_local.services.scanner import get_relative_path


def test_fast_check_and_refresh_no_index(tmp_path):
    result = fast_check_and_refresh(tmp_path)
    assert not result["updated"]
    assert result["reason"] == "no_index"
    assert result["changed_count"] == 0


def test_fast_check_and_refresh_outdated_schema(tmp_path):
    config.REPO_ROOT = tmp_path
    lancedb_dir = tmp_path / ".lancedb"
    lancedb_dir.mkdir(parents=True, exist_ok=True)
    # Crear meta.json con version de esquema obsoleta
    (lancedb_dir / "meta.json").write_text(
        '{"schema_version": "0.1.0", "embedding_model": "old-model"}',
        encoding="utf-8",
    )

    with patch("rag_local.cli.ingest.run_ingestion") as mock_ingest:
        result = fast_check_and_refresh(tmp_path)
        assert result["updated"]
        assert "forced_schema_update" in result["reason"]
        assert result["changed_count"] == -1
        mock_ingest.assert_called_once_with(exit_on_complete=False, force=True)


def test_fast_check_and_refresh_clean(tmp_path):
    config.REPO_ROOT = tmp_path
    lancedb_dir = tmp_path / ".lancedb"
    lancedb_dir.mkdir(parents=True, exist_ok=True)
    (lancedb_dir / "meta.json").write_text(
        f'{{"schema_version": "{config.SCHEMA_VERSION}", '
        f'"embedding_model": "{config.LOCAL_EMBEDDING_MODEL}"}}',
        encoding="utf-8",
    )

    file1 = tmp_path / "main.py"
    file1.write_text("print('hello')", encoding="utf-8")

    rel_key = get_relative_path(file1)
    fake_cache = {rel_key: {"mtime": file1.stat().st_mtime, "hash": "abc"}}

    with (
        patch("rag_local.services.fast_sync.scan_files", return_value=[file1]),
        patch("rag_local.services.fast_sync.load_cache", return_value=fake_cache),
    ):
        result = fast_check_and_refresh(tmp_path)
        assert not result["updated"]
        assert result["reason"] == "clean"
        assert result["changed_count"] == 0


def test_fast_check_and_refresh_modified_file(tmp_path):
    config.REPO_ROOT = tmp_path
    lancedb_dir = tmp_path / ".lancedb"
    lancedb_dir.mkdir(parents=True, exist_ok=True)
    (lancedb_dir / "meta.json").write_text(
        f'{{"schema_version": "{config.SCHEMA_VERSION}", '
        f'"embedding_model": "{config.LOCAL_EMBEDDING_MODEL}"}}',
        encoding="utf-8",
    )

    file1 = tmp_path / "main.py"
    file1.write_text("print('modified')", encoding="utf-8")

    rel_key = get_relative_path(file1)
    # Hash diferente en cache
    fake_cache = {rel_key: "old_hash"}

    with (
        patch("rag_local.services.fast_sync.scan_files", return_value=[file1]),
        patch("rag_local.services.fast_sync.load_cache", return_value=fake_cache),
        patch("rag_local.cli.ingest.run_ingestion") as mock_ingest,
    ):
        result = fast_check_and_refresh(tmp_path)
        assert result["updated"]
        assert result["reason"] == "sync_completed"
        assert result["changed_count"] == 1
        mock_ingest.assert_called_once_with(exit_on_complete=False, force=False)
