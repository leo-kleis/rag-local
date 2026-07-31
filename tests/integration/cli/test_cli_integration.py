import json
import subprocess
import sys

import pytest


@pytest.fixture
def dummy_repo(tmp_path):
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy files for detection
    (repo_dir / "pyproject.toml").write_text(
        "[project]\nname = 'test'\n", encoding="utf-8"
    )
    py_dir = repo_dir / "src"
    py_dir.mkdir(parents=True, exist_ok=True)
    (py_dir / "main.py").write_text(
        "def hello():\n    print('world')\n", encoding="utf-8"
    )
    (repo_dir / "style.css").write_text(".btn { color: red; }\n", encoding="utf-8")

    return repo_dir


def test_cli_metrics_subprocess_json(dummy_repo):
    cmd = [
        sys.executable,
        "-m",
        "rag_local.cli.metrics",
        "-p",
        str(dummy_repo),
        "--json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert isinstance(data, dict)


def test_cli_metrics_subprocess_invalid_path():
    cmd = [
        sys.executable,
        "-m",
        "rag_local.cli.metrics",
        "-p",
        "C:/path/that/does/not/exist_12345",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 1
    assert "Error" in res.stderr


def test_cli_styles_subprocess_json(dummy_repo):
    cmd = [
        sys.executable,
        "-m",
        "rag_local.cli.styles",
        "-p",
        str(dummy_repo),
        "--json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert isinstance(data, dict)


def test_cli_styles_subprocess_missing_arg():
    cmd = [sys.executable, "-m", "rag_local.cli.styles"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 2
    assert "required" in res.stderr or "error" in res.stderr.lower()


def test_cli_project_map_subprocess_invalid_path():
    cmd = [
        sys.executable,
        "-m",
        "rag_local.cli.project_map",
        "-p",
        "C:/invalid/path_54321",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 1
    assert "Error" in res.stderr


def test_cli_graph_subprocess_invalid_path():
    cmd = [
        sys.executable,
        "-m",
        "rag_local.cli.graph",
        "-p",
        "C:/invalid/path_54321",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 1
    assert "Error" in res.stderr


def test_cli_query_subprocess_missing_query(dummy_repo):
    cmd = [
        sys.executable,
        "-m",
        "rag_local.cli.query",
        "-p",
        str(dummy_repo),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 2
    assert "required" in res.stderr or "error" in res.stderr.lower()


def test_cli_query_subprocess_empty_query_string(dummy_repo):
    cmd = [
        sys.executable,
        "-m",
        "rag_local.cli.query",
        "-p",
        str(dummy_repo),
        "-q",
        "   ",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 1


def test_cli_ingest_subprocess_invalid_path():
    cmd = [
        sys.executable,
        "-m",
        "rag_local.cli.ingest",
        "-p",
        "C:/path/does_not_exist_999",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 1
    assert "Error" in res.stderr
