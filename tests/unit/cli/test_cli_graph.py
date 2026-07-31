from unittest.mock import patch

import pytest

from rag_local.cli.graph import main, parse_arguments
from rag_local.core import config


def test_parse_arguments_success(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-graph", "-p", "/dummy/path"])
    args = parse_arguments()
    assert args.project_path == "/dummy/path"


def test_parse_arguments_missing_project_path(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-graph"])
    with pytest.raises(SystemExit) as exc_info:
        parse_arguments()
    assert exc_info.value.code == 2


def test_main_non_existent_path(tmp_path, monkeypatch):
    non_existent = tmp_path / "does_not_exist"
    monkeypatch.setattr("sys.argv", ["rag-graph", "-p", str(non_existent)])

    with patch("rag_local.cli.graph.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()
        call_arg = mock_stderr.print.call_args[0][0]
        assert "Error: La ruta especificada no existe" in call_arg


def test_main_path_is_file(tmp_path, monkeypatch):
    file_path = tmp_path / "file.txt"
    file_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["rag-graph", "-p", str(file_path)])

    with patch("rag_local.cli.graph.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()
        call_arg = mock_stderr.print.call_args[0][0]
        assert "no es un directorio" in call_arg


def test_main_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-graph", "-p", str(repo_dir)])

    with (
        patch("rag_local.cli.graph.generate_html_graph") as mock_gen,
        patch("rag_local.cli.graph.stdout_console") as mock_stdout,
        patch("rag_local.cli.graph.stderr_console") as mock_stderr,
    ):
        main()

        assert repo_dir.resolve() == config.REPO_ROOT
        assert (repo_dir / ".lancedb").resolve() == config.LANCEDB_PATH
        mock_gen.assert_called_once_with(
            repo_dir / ".lancedb", repo_dir / ".lancedb" / "project_graph.html"
        )
        mock_stderr.print.assert_called_once_with(
            "Generando visualizaciones del grafo (3D, 2D y Mermaid independientes)..."
        )
        mock_stdout.print.assert_called_once()
        stdout_text = mock_stdout.print.call_args[0][0]
        assert "Graph files generated successfully" in stdout_text


def test_main_exception(tmp_path, monkeypatch):
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-graph", "-p", str(repo_dir)])

    with (
        patch(
            "rag_local.cli.graph.generate_html_graph",
            side_effect=RuntimeError("Graph build error"),
        ),
        patch("rag_local.cli.graph.stderr_console") as mock_stderr,
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        assert mock_stderr.print.call_count == 2
        err_msg = mock_stderr.print.call_args_list[1][0][0]
        assert "Error al generar el grafo: Graph build error" in err_msg
