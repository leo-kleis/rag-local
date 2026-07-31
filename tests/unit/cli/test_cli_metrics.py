import json
from unittest.mock import patch

import pytest

from rag_local.cli.metrics import main, parse_arguments
from rag_local.core import config


def test_parse_arguments_default(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-loc", "-p", "/my/project"])
    args = parse_arguments()
    assert args.project_path == "/my/project"
    assert args.threshold == 200
    assert args.json is False


def test_parse_arguments_custom(monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["rag-loc", "-p", "/my/project", "-t", "500", "--json"]
    )
    args = parse_arguments()
    assert args.project_path == "/my/project"
    assert args.threshold == 500
    assert args.json is True


def test_parse_arguments_missing_required(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-loc"])
    with pytest.raises(SystemExit) as exc_info:
        parse_arguments()
    assert exc_info.value.code == 2


def test_main_path_not_exists(tmp_path, monkeypatch):
    non_existent = tmp_path / "missing"
    monkeypatch.setattr("sys.argv", ["rag-loc", "-p", str(non_existent)])

    with patch("rag_local.cli.metrics.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()
        assert (
            "Error: La ruta especificada no existe" in mock_stderr.print.call_args[0][0]
        )


def test_main_path_is_file(tmp_path, monkeypatch):
    file_path = tmp_path / "code.py"
    file_path.write_text("print('hello')", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["rag-loc", "-p", str(file_path)])

    with patch("rag_local.cli.metrics.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()


def test_main_text_output_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-loc", "-p", str(repo_dir), "-t", "100"])

    fake_metrics = {"total_files": 10, "total_loc": 1500}
    with (
        patch(
            "rag_local.cli.metrics.get_code_metrics", return_value=fake_metrics
        ) as mock_get,
        patch(
            "rag_local.cli.metrics.format_code_metrics",
            return_value="Formatted Metrics Output",
        ) as mock_fmt,
        patch("rag_local.cli.metrics.Console") as mock_console_cls,
    ):
        mock_console_instance = mock_console_cls.return_value
        main()

        assert repo_dir.resolve() == config.REPO_ROOT
        assert (repo_dir / ".lancedb").resolve() == config.LANCEDB_PATH
        mock_get.assert_called_once_with(str(repo_dir.resolve()), min_lines=100)
        mock_fmt.assert_called_once_with(fake_metrics)
        mock_console_instance.print.assert_called_once_with("Formatted Metrics Output")


def test_main_json_output_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-loc", "-p", str(repo_dir), "--json"])

    fake_metrics = {"total_files": 5, "large_files": []}
    with (
        patch(
            "rag_local.cli.metrics.get_code_metrics", return_value=fake_metrics
        ) as mock_get,
        patch("rag_local.cli.metrics.Console") as mock_console_cls,
    ):
        mock_console_instance = mock_console_cls.return_value
        main()

        mock_get.assert_called_once_with(str(repo_dir.resolve()), min_lines=200)
        printed_arg = mock_console_instance.print.call_args[0][0]
        parsed_printed = json.loads(printed_arg)
        assert parsed_printed == fake_metrics


def test_main_exception(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-loc", "-p", str(repo_dir)])

    with (
        patch(
            "rag_local.cli.metrics.get_code_metrics",
            side_effect=ValueError("Metrics calculation error"),
        ),
        patch("rag_local.cli.metrics.stderr_console") as mock_stderr,
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()
        assert (
            "Error al obtener métricas del código: Metrics calculation error"
            in mock_stderr.print.call_args[0][0]
        )
