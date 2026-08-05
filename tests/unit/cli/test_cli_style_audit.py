import json
from unittest.mock import patch

import pytest

from rag_local.cli.style_audit import main, parse_arguments
from rag_local.core import config


def test_parse_arguments_default(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-style-audit", "-p", "/my/project"])
    args = parse_arguments()
    assert args.project_path == "/my/project"
    assert args.severity == "ALL"
    assert args.json is False


def test_parse_arguments_severity_json(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["rag-style-audit", "-p", "/my/project", "-s", "CRITICAL", "--json"],
    )
    args = parse_arguments()
    assert args.project_path == "/my/project"
    assert args.severity == "CRITICAL"
    assert args.json is True


def test_main_path_not_exists(tmp_path, monkeypatch):
    non_existent = tmp_path / "missing"
    monkeypatch.setattr("sys.argv", ["rag-style-audit", "-p", str(non_existent)])

    with patch("rag_local.cli.style_audit.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()


def test_main_formatted_output_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-style-audit", "-p", str(repo_dir)])

    fake_report = {"total_issues": 0, "issues": []}
    with (
        patch(
            "rag_local.cli.style_audit.audit_layout_risks", return_value=fake_report
        ) as mock_audit,
        patch(
            "rag_local.cli.style_audit.format_audit_report",
            return_value="No issues found",
        ) as mock_fmt,
        patch("rag_local.cli.style_audit.Console") as mock_console_cls,
    ):
        mock_console_instance = mock_console_cls.return_value
        main()

        assert repo_dir.resolve() == config.REPO_ROOT
        mock_audit.assert_called_once_with(
            repo_path=str(repo_dir.resolve()),
            severity_filter="ALL",
            file_filter=None,
        )
        mock_fmt.assert_called_once_with(fake_report)
        mock_console_instance.print.assert_called_once_with("No issues found")
