from unittest.mock import patch

import pytest

from rag_local.cli.project_map import main, parse_arguments
from rag_local.core import config


def test_parse_arguments_success(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-project-map", "-p", "/my/project"])
    args = parse_arguments()
    assert args.project_path == "/my/project"


def test_parse_arguments_missing(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-project-map"])
    with pytest.raises(SystemExit) as exc_info:
        parse_arguments()
    assert exc_info.value.code == 2


def test_main_path_not_exists(tmp_path, monkeypatch):
    non_existent = tmp_path / "missing"
    monkeypatch.setattr("sys.argv", ["rag-project-map", "-p", str(non_existent)])

    with patch("rag_local.cli.project_map.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()
        assert (
            "Error: La ruta especificada no existe" in mock_stderr.print.call_args[0][0]
        )


def test_main_path_is_file(tmp_path, monkeypatch):
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["rag-project-map", "-p", str(file_path)])

    with patch("rag_local.cli.project_map.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()


def test_main_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-project-map", "-p", str(repo_dir)])

    fake_map = "=== Project Map ===\nModule A\nModule B"
    with (
        patch(
            "rag_local.cli.project_map.generate_project_map", return_value=fake_map
        ) as mock_gen,
        patch("rag_local.cli.project_map.stdout_console") as mock_stdout,
        patch("rag_local.cli.project_map.stderr_console") as mock_stderr,
    ):
        main()

        assert repo_dir.resolve() == config.REPO_ROOT
        assert (repo_dir / ".lancedb").resolve() == config.LANCEDB_PATH
        mock_gen.assert_called_once_with(repo_dir / ".lancedb")
        mock_stderr.print.assert_called_once_with("Leyendo metadatos del índice...")
        mock_stdout.print.assert_called_once_with(fake_map)


def test_main_exception(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-project-map", "-p", str(repo_dir)])

    with (
        patch(
            "rag_local.cli.project_map.generate_project_map",
            side_effect=RuntimeError("Index unreadable"),
        ),
        patch("rag_local.cli.project_map.stderr_console") as mock_stderr,
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        assert mock_stderr.print.call_count == 2
        err_msg = mock_stderr.print.call_args_list[1][0][0]
        assert "Error al generar el mapa: Index unreadable" in err_msg
