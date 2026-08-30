from unittest.mock import MagicMock, patch

from rag_local.services.embeddings import get_embeddings


def test_get_embeddings_delegates_to_daemon():
    mock_daemon_vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    with patch(
        "rag_local.daemon.client.try_daemon_embed",
        return_value=mock_daemon_vectors,
    ) as mock_daemon:
        result = get_embeddings(["text 1", "text 2"])
        assert result == mock_daemon_vectors
        mock_daemon.assert_called_once_with(
            ["Candidate code snippet:\ntext 1", "Candidate code snippet:\ntext 2"]
        )


def test_get_embeddings_fallback_when_daemon_inactive():
    mock_worker = MagicMock()
    mock_worker.sync_embed.return_value = [[0.7, 0.8, 0.9]]

    with (
        patch("rag_local.daemon.client.try_daemon_embed", return_value=None),
        patch(
            "rag_local.services.embeddings.get_standalone_worker",
            return_value=mock_worker,
        ),
    ):
        result = get_embeddings(["fallback text"])
        assert result == [[0.7, 0.8, 0.9]]
        mock_worker.sync_embed.assert_called_once_with(
            ["Candidate code snippet:\nfallback text"]
        )

