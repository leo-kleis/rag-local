import argparse
from unittest.mock import MagicMock, patch

from rag_local.cli.daemon import run_daemon_cli


def test_cli_daemon_status_inactive(capsys):
    with patch("rag_local.cli.daemon.daemon_healthcheck", return_value=None):
        args = argparse.Namespace(command="status")
        code = run_daemon_cli(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "Inactivo" in captured.out


def test_cli_daemon_status_active(capsys):
    mock_health = {
        "status": "ok",
        "device": "cuda",
        "port": 8765,
        "pid": 1234,
        "uptime_s": 3600.0,
        "idle_s": 120.0,
        "vram": {"used_mb": 1100.0, "total_mb": 6000.0},
    }
    with patch("rag_local.cli.daemon.daemon_healthcheck", return_value=mock_health):
        args = argparse.Namespace(command="status")
        code = run_daemon_cli(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "WORKER_DAEMON: Activo" in captured.out
        assert "8765" in captured.out
        assert "cuda" in captured.out


def test_cli_daemon_start_already_running(capsys):
    mock_health = {"status": "ok", "port": 8765, "pid": 1234, "device": "cuda"}
    with patch("rag_local.cli.daemon.daemon_healthcheck", return_value=mock_health):
        args = argparse.Namespace(command="start", parent_pid=None, port=0)
        code = run_daemon_cli(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "ya está activo" in captured.out


def test_cli_daemon_start_success(capsys):
    mock_health = {"status": "ok", "port": 8765, "pid": 5678, "device": "cuda"}
    with (
        patch("rag_local.cli.daemon.daemon_healthcheck", side_effect=[None, mock_health, mock_health]),
        patch("rag_local.cli.daemon._start_detached_daemon", return_value=True),
    ):
        args = argparse.Namespace(command="start", parent_pid=None, port=0)
        code = run_daemon_cli(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "iniciado con éxito" in captured.out


def test_cli_daemon_stop(capsys):
    port_data = {"port": 8765, "pid": 1234, "token": "tok"}
    with (
        patch("rag_local.cli.daemon.read_port_file", return_value=port_data),
        patch("rag_local.cli.daemon.daemon_shutdown", return_value=True),
        patch("rag_local.cli.daemon.daemon_healthcheck", return_value=None),
    ):
        args = argparse.Namespace(command="stop")
        code = run_daemon_cli(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "detenido" in captured.out
