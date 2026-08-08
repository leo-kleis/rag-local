import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_local.daemon.client import (
    daemon_claim,
    daemon_healthcheck,
    daemon_shutdown,
    try_daemon_embed,
    try_daemon_rerank,
)


def test_try_daemon_embed_empty_texts():
    assert try_daemon_embed([]) == []


def test_try_daemon_embed_no_daemon():
    with patch("rag_local.daemon.client.read_port_file", return_value=None):
        assert try_daemon_embed(["hello"]) is None


def test_try_daemon_embed_success():
    port_data = {"port": 8765, "token": "tok"}
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({"embeddings": [[0.1, 0.2, 0.3]]}).encode("utf-8")

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = mock_resp

    with (
        patch("rag_local.daemon.client.read_port_file", return_value=port_data),
        patch("http.client.HTTPConnection", return_value=mock_conn),
    ):
        result = try_daemon_embed(["hello world"])
        assert result == [[0.1, 0.2, 0.3]]


def test_try_daemon_rerank_no_daemon():
    with patch("rag_local.daemon.client.read_port_file", return_value=None):
        assert try_daemon_rerank("query", ["doc1", "doc2"]) is None


def test_try_daemon_rerank_success():
    port_data = {"port": 8765, "token": "tok"}
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(
        {"results": [{"doc_id": 1, "score": 3.5}, {"doc_id": 0, "score": -1.2}]}
    ).encode("utf-8")

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = mock_resp

    with (
        patch("rag_local.daemon.client.read_port_file", return_value=port_data),
        patch("http.client.HTTPConnection", return_value=mock_conn),
    ):
        ranked = try_daemon_rerank("query", ["doc0", "doc1"])
        assert ranked is not None
        assert len(ranked) == 2
        assert ranked[0].doc_id == 1
        assert ranked[0].score == 3.5
        assert ranked[1].doc_id == 0
        assert ranked[1].score == -1.2


def test_daemon_healthcheck_success():
    port_data = {"port": 8765, "pid": 1234, "token": "tok"}
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({"status": "ok", "device": "cuda"}).encode("utf-8")

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = mock_resp

    with (
        patch("rag_local.daemon.client.read_port_file", return_value=port_data),
        patch("rag_local.daemon.client.is_daemon_alive", return_value=True),
        patch("http.client.HTTPConnection", return_value=mock_conn),
    ):
        health = daemon_healthcheck()
        assert health is not None
        assert health["status"] == "ok"
        assert health["device"] == "cuda"
        assert health["port"] == 8765
        assert health["pid"] == 1234


def test_daemon_claim_and_shutdown():
    port_data = {"port": 8765, "pid": 1234, "token": "tok"}
    mock_resp = MagicMock()
    mock_resp.status = 200

    mock_conn = MagicMock()
    mock_conn.getresponse.return_value = mock_resp

    with (
        patch("rag_local.daemon.client.read_port_file", return_value=port_data),
        patch("http.client.HTTPConnection", return_value=mock_conn),
    ):
        assert daemon_claim(5678) is True
        assert daemon_shutdown() is True
