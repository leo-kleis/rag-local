from unittest.mock import patch

import pytest

from rag_local.cli.query import main, parse_arguments, run_query_cli
from rag_local.core import config


def test_parse_arguments_all_options(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "rag-query",
            "-p",
            "/my/project",
            "-q",
            "How to test?",
            "-s",
            "python",
            "-j",
            "--no-llm",
        ],
    )
    args = parse_arguments()
    assert args.project_path == "/my/project"
    assert args.query == "How to test?"
    assert args.scope == "python"
    assert args.json is True
    assert args.no_llm is True


def test_parse_arguments_missing_required(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rag-query", "-p", "/my/project"])
    with pytest.raises(SystemExit) as exc_info:
        parse_arguments()
    assert exc_info.value.code == 2


def test_run_query_cli_invalid_path_not_exists(tmp_path, monkeypatch):
    non_existent = tmp_path / "missing"
    monkeypatch.setattr(
        "sys.argv", ["rag-query", "-p", str(non_existent), "-q", "hello"]
    )

    with patch("rag_local.cli.query.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            run_query_cli()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()
        assert (
            "Error: La ruta especificada no existe" in mock_stderr.print.call_args[0][0]
        )


def test_run_query_cli_invalid_path_is_file(tmp_path, monkeypatch):
    file_path = tmp_path / "file.py"
    file_path.write_text("x = 1", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["rag-query", "-p", str(file_path), "-q", "hello"])

    with patch("rag_local.cli.query.stderr_console") as mock_stderr:
        with pytest.raises(SystemExit) as exc_info:
            run_query_cli()
        assert exc_info.value.code == 1
        mock_stderr.print.assert_called_once()


def test_run_query_cli_empty_query(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr("sys.argv", ["rag-query", "-p", str(repo_dir), "-q", "   "])

    with (
        patch("rag_local.cli.query.logger") as mock_logger,
        pytest.raises(SystemExit) as exc_info,
    ):
        run_query_cli()
    assert exc_info.value.code == 1
    mock_logger.error.assert_called_once_with("La consulta no puede estar vacía.")


def test_run_query_cli_too_long_query(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    long_query = "a" * (config.MAX_QUERY_LENGTH + 10)
    monkeypatch.setattr(
        "sys.argv", ["rag-query", "-p", str(repo_dir), "-q", long_query]
    )

    with (
        patch("rag_local.cli.query.logger") as mock_logger,
        pytest.raises(SystemExit) as exc_info,
    ):
        run_query_cli()
    assert exc_info.value.code == 1
    mock_logger.error.assert_called_once()
    assert "excede la longitud máxima permitida" in mock_logger.error.call_args[0][0]


def test_run_query_cli_json_mode_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        [
            "rag-query",
            "-p",
            str(repo_dir),
            "-q",
            "what is this?",
            "-j",
            "-s",
            "angular",
        ],
    )

    fake_results = {
        "query": "what is this?",
        "retrieved_chunks": [],
        "response": "Test response",
    }
    with (
        patch(
            "rag_local.cli.query.process_query", return_value=fake_results
        ) as mock_proc,
        patch("rag_local.cli.query.stdout_console") as mock_stdout,
    ):
        run_query_cli()

        assert repo_dir.resolve() == config.REPO_ROOT
        assert (repo_dir / ".lancedb").resolve() == config.LANCEDB_PATH
        mock_proc.assert_called_once_with(
            query_text="what is this?",
            scope="angular",
            respond_in_english=True,
            generate_response=True,
        )
        mock_stdout.print_json.assert_called_once_with(data=fake_results)


def test_run_query_cli_human_mode_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        ["rag-query", "-p", str(repo_dir), "-q", "explain main.py", "--no-llm"],
    )

    fake_results = {
        "query": "explain main.py",
        "retrieved_chunks": [{"source": "main.py", "start_line": 1, "end_line": 10}],
        "response": "# Main Explanation",
    }
    with (
        patch(
            "rag_local.cli.query.process_query", return_value=fake_results
        ) as mock_proc,
        patch("rag_local.cli.query.stdout_console") as mock_stdout,
        patch("rag_local.cli.query.stderr_console") as mock_stderr,
    ):
        run_query_cli()

        mock_proc.assert_called_once_with(
            query_text="explain main.py",
            scope=None,
            respond_in_english=False,
            generate_response=False,
        )
        assert mock_stderr.print.call_count >= 3
        assert mock_stdout.print.call_count == 3


def test_run_query_cli_process_query_exception(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "sys.argv", ["rag-query", "-p", str(repo_dir), "-q", "valid query"]
    )

    with (
        patch(
            "rag_local.cli.query.process_query",
            side_effect=RuntimeError("LanceDB connection failed"),
        ),
        patch("rag_local.cli.query.logger") as mock_logger,
        pytest.raises(SystemExit) as exc_info,
    ):
        run_query_cli()
    assert exc_info.value.code == 1
    mock_logger.error.assert_called_once_with(
        "Fallo al consultar la base de datos: LanceDB connection failed"
    )


def test_main_keyboard_interrupt(monkeypatch):
    with (
        patch("rag_local.cli.query.run_query_cli", side_effect=KeyboardInterrupt()),
        patch("rag_local.cli.query.stderr_console") as mock_stderr,
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 1
    mock_stderr.print.assert_called_once_with(
        "\n[bold red]Consulta cancelada por el usuario.[/bold red]"
    )
