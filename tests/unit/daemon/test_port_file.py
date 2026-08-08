import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_local.daemon.port_file import (
    delete_port_file,
    generate_token,
    get_port_file_path,
    is_daemon_alive,
    read_port_file,
    write_port_file,
)


def test_generate_token():
    token1 = generate_token()
    token2 = generate_token()
    assert isinstance(token1, str)
    assert len(token1) >= 40
    assert token1 != token2


def test_write_and_read_port_file(tmp_path: Path):
    data = {
        "port": 8765,
        "pid": 12345,
        "parent_pid": 6789,
        "token": "secret_token_123",
        "device": "cuda",
    }
    path = write_port_file(data, lancedb_path=tmp_path)
    assert path.exists()
    assert path.name == "daemon.json"

    read_data = read_port_file(lancedb_path=tmp_path)
    assert read_data is not None
    assert read_data["port"] == 8765
    assert read_data["pid"] == 12345
    assert read_data["token"] == "secret_token_123"
    assert read_data["device"] == "cuda"


def test_read_port_file_missing_or_corrupt(tmp_path: Path):
    # Archivo no existe
    assert read_port_file(lancedb_path=tmp_path) is None

    # Archivo corrupto (JSON inválido)
    target = get_port_file_path(tmp_path)
    target.write_text("invalid json {", encoding="utf-8")
    assert read_port_file(lancedb_path=tmp_path) is None


def test_delete_port_file(tmp_path: Path):
    data = {"port": 8765, "pid": 12345, "token": "tok"}
    write_port_file(data, lancedb_path=tmp_path)
    assert get_port_file_path(tmp_path).exists()

    deleted = delete_port_file(lancedb_path=tmp_path)
    assert deleted is True
    assert not get_port_file_path(tmp_path).exists()

    # Segunda llamada cuando no existe
    assert delete_port_file(lancedb_path=tmp_path) is False


def test_is_daemon_alive_process_dead():
    port_data = {"port": 8765, "pid": 9999999, "token": "tok"}
    with patch("psutil.pid_exists", return_value=False):
        assert is_daemon_alive(port_data) is False


def test_is_daemon_alive_http_success():
    port_data = {"port": 8765, "pid": 12345, "token": "tok"}
    mock_resp = MagicMock()
    mock_resp.status = 200

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = mock_resp

    with (
        patch("psutil.pid_exists", return_value=True),
        patch("http.client.HTTPConnection", return_value=mock_conn),
    ):
        assert is_daemon_alive(port_data) is True
        mock_conn.request.assert_called_once_with(
            "GET",
            "/health",
            headers={"Authorization": "Bearer tok", "Host": "127.0.0.1"},
        )


def test_is_daemon_alive_http_failure():
    port_data = {"port": 8765, "pid": 12345, "token": "tok"}
    with (
        patch("psutil.pid_exists", return_value=True),
        patch("http.client.HTTPConnection", side_effect=ConnectionRefusedError),
    ):
        assert is_daemon_alive(port_data) is False
