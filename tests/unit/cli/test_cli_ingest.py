from unittest.mock import MagicMock, patch

import pytest

from rag_local.cli.ingest import main, run_ingestion
from rag_local.core import config
from rag_local.core.models import Chunk, ChunkMetadata


def test_parse_arguments_defaults(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-ingest", "-p", str(repo_dir)])

    with patch("rag_local.cli.ingest.run_ingestion") as mock_run:
        main()
        assert repo_dir.resolve() == config.REPO_ROOT
        assert (repo_dir / ".lancedb").resolve() == config.LANCEDB_PATH
        mock_run.assert_called_once_with(force=False)


def test_parse_arguments_force(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-ingest", "-p", str(repo_dir), "-f"])

    with patch("rag_local.cli.ingest.run_ingestion") as mock_run:
        main()
        mock_run.assert_called_once_with(force=True)


def test_main_invalid_path_not_exists(tmp_path, monkeypatch):
    non_existent = tmp_path / "missing"
    monkeypatch.setattr("sys.argv", ["rag-ingest", "-p", str(non_existent)])

    with (
        patch("rag_local.cli.ingest.console") as mock_console,
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 1
    mock_console.print.assert_called_once()
    assert "Error: La ruta especificada no existe" in mock_console.print.call_args[0][0]


def test_main_invalid_path_is_file(tmp_path, monkeypatch):
    file_path = tmp_path / "file.txt"
    file_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["rag-ingest", "-p", str(file_path)])

    with (
        patch("rag_local.cli.ingest.console") as mock_console,
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 1
    mock_console.print.assert_called_once()


def test_run_ingestion_invalid_repo_root(tmp_path):
    config.REPO_ROOT = tmp_path / "non_existent_folder"
    with pytest.raises(SystemExit) as exc_info:
        run_ingestion()
    assert exc_info.value.code == 1


def test_run_ingestion_no_project_roots(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config.REPO_ROOT = repo_dir

    with (
        patch(
            "rag_local.cli.ingest.detect_project_roots",
            return_value=(None, None, None, None),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        run_ingestion()
    assert exc_info.value.code == 1


def test_run_ingestion_db_error(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config.REPO_ROOT = repo_dir

    with (
        patch(
            "rag_local.cli.ingest.detect_project_roots",
            return_value=(repo_dir, None, None, None),
        ),
        patch(
            "rag_local.cli.ingest.get_chroma_collection",
            side_effect=RuntimeError("Database failure"),
        ),
        patch("rag_local.cli.ingest.logger") as mock_logger,
        pytest.raises(SystemExit) as exc_info,
    ):
        run_ingestion()
    assert exc_info.value.code == 1
    mock_logger.error.assert_called_once_with(
        "Error de conexión con base de datos: Database failure"
    )


def test_run_ingestion_no_files_found(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config.REPO_ROOT = repo_dir
    mock_coll = MagicMock()

    with (
        patch(
            "rag_local.cli.ingest.detect_project_roots",
            return_value=(repo_dir, None, None, None),
        ),
        patch("rag_local.cli.ingest.get_chroma_collection", return_value=mock_coll),
        patch("rag_local.cli.ingest.scan_files", return_value=[]),
        patch("rag_local.cli.ingest.logger") as mock_logger,
        pytest.raises(SystemExit) as exc_info,
    ):
        run_ingestion()
    assert exc_info.value.code == 0
    mock_logger.warning.assert_called_once_with(
        "No se encontraron archivos de código válidos para indexar."
    )


def test_run_ingestion_full_flow_success(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config.REPO_ROOT = repo_dir

    file1 = repo_dir / "app.py"
    file1.write_text("print('hello')", encoding="utf-8")

    mock_coll = MagicMock()
    mock_coll.count.return_value = 1
    mock_coll.get.return_value = {"ids": []}

    dummy_chunk = Chunk(
        text="print('hello')",
        start_line=1,
        end_line=1,
        metadata=ChunkMetadata(),
        source="app.py",
    )

    progress_calls = []

    def p_cb(current, total, msg):
        progress_calls.append((current, total, msg))

    with (
        patch(
            "rag_local.cli.ingest.detect_project_roots",
            return_value=(repo_dir, repo_dir, repo_dir, repo_dir),
        ),
        patch("rag_local.cli.ingest.get_chroma_collection", return_value=mock_coll),
        patch("rag_local.cli.ingest.scan_files", return_value=[file1]),
        patch(
            "rag_local.cli.ingest.load_cache",
            return_value={"old_deleted.py": "hash_old"},
        ),
        patch("rag_local.cli.ingest.get_relative_path", side_effect=lambda p: p.name),
        patch("rag_local.cli.ingest.get_file_scope", return_value="python"),
        patch("rag_local.cli.ingest.get_file_hash", return_value="hash_new"),
        patch("rag_local.cli.ingest.chunk_file", return_value=[dummy_chunk]),
        patch("rag_local.cli.ingest.save_file_relationships"),
        patch(
            "rag_local.cli.ingest.index_chunks",
            side_effect=lambda coll, chunks, cb: cb(1, 1, 1, "start") or len(chunks),
        ),
        patch("rag_local.cli.ingest.compact_db") as mock_compact,
        patch("rag_local.cli.ingest.save_cache") as mock_save_cache,
    ):
        run_ingestion(progress_callback=p_cb, exit_on_complete=False, force=False)

        mock_compact.assert_called_once()
        mock_save_cache.assert_called_once()
        assert len(progress_calls) >= 3
        assert progress_calls[-1][2] == "¡Ingesta completada exitosamente!"


def test_main_keyboard_interrupt(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-ingest", "-p", str(repo_dir)])

    with (
        patch("rag_local.cli.ingest.run_ingestion", side_effect=KeyboardInterrupt()),
        patch("rag_local.cli.ingest.console") as mock_console,
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 1
    mock_console.print.assert_called_with(
        "\n[bold red]Proceso cancelado por el usuario.[/bold red]"
    )
