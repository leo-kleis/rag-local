import json
from unittest.mock import patch

import pytest

from rag_local.cli.styles import main, parse_arguments
from rag_local.core import config


def test_parse_arguments_default(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-styles", "-p", "/my/project"])
    args = parse_arguments()
    assert args.project_path == "/my/project"
    assert args.json is False


def test_parse_arguments_json(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-styles", "-p", "/my/project", "--json"])
    args = parse_arguments()
    assert args.project_path == "/my/project"
    assert args.json is True


def test_parse_arguments_missing(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-styles"])
    with pytest.raises(SystemExit) as exc_info:
        parse_arguments()
    assert exc_info.value.code == 2


def test_main_path_not_exists(tmp_path, monkeypatch):
    non_existent = tmp_path / "missing"
    monkeypatch.setattr("sys.argv", ["rag-styles", "-p", str(non_existent)])

    with patch("rag_local.cli.styles.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()
        assert (
            "Error: La ruta especificada no existe" in mock_stderr.print.call_args[0][0]
        )


def test_main_path_is_file(tmp_path, monkeypatch):
    file_path = tmp_path / "style.css"
    file_path.write_text(".class { color: red; }", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["rag-styles", "-p", str(file_path)])

    with patch("rag_local.cli.styles.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()


def test_main_formatted_output_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-styles", "-p", str(repo_dir)])

    fake_styles = {"unused_classes": ["btn-old"], "total_classes": 10}
    with (
        patch(
            "rag_local.cli.styles.get_styles_summary", return_value=fake_styles
        ) as mock_get,
        patch(
            "rag_local.cli.styles.format_styles_summary",
            return_value="Styles Summary Output",
        ) as mock_fmt,
        patch("rag_local.cli.styles.Console") as mock_console_cls,
    ):
        mock_console_instance = mock_console_cls.return_value
        main()

        mock_get.assert_called_once_with(
            repo_path=str(repo_dir.resolve()),
            component_filter=None,
            class_filter=None,
            property_filter=None,
        )
        mock_fmt.assert_called_once_with(fake_styles)
        mock_console_instance.print.assert_called_once_with("Styles Summary Output")


def test_main_json_output_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-styles", "-p", str(repo_dir), "--json"])

    fake_styles = {"css_files": ["style.css"], "unused_count": 0}
    with (
        patch(
            "rag_local.cli.styles.get_styles_summary", return_value=fake_styles
        ) as mock_get,
        patch("rag_local.cli.styles.Console") as mock_console_cls,
    ):
        mock_console_instance = mock_console_cls.return_value
        main()

        mock_get.assert_called_once_with(
            repo_path=str(repo_dir.resolve()),
            component_filter=None,
            class_filter=None,
            property_filter=None,
        )
        printed_arg = mock_console_instance.print.call_args[0][0]
        assert json.loads(printed_arg) == fake_styles


def test_main_exception(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-styles", "-p", str(repo_dir)])

    with (
        patch(
            "rag_local.cli.styles.get_styles_summary",
            side_effect=RuntimeError("CSS parse failure"),
        ),
        patch("rag_local.cli.styles.stderr_console") as mock_stderr,
    ):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()
        assert (
            "Error al generar el mapa de estilos: CSS parse failure"
            in mock_stderr.print.call_args[0][0]
        )
