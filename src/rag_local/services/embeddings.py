import os

from rag_local.core.exceptions import EmbeddingError
from rag_local.core.logging import logger
from rag_local.daemon.models import ModelWorker

# Singleton para el worker en modo standalone
_standalone_worker: ModelWorker | None = None


def get_standalone_worker() -> ModelWorker:
    """Carga e inicializa el ModelWorker de ONNX Runtime para ejecución directa."""
    global _standalone_worker
    if _standalone_worker is not None:
        return _standalone_worker

    _standalone_worker = ModelWorker()
    _standalone_worker.load_and_warmup_models()
    return _standalone_worker


def get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Genera embeddings locales para una lista de textos.

    Intenta delegar al Worker Daemon en VRAM primero; si no está activo,
    hace fallback automático al ModelWorker ONNX local.
    """
    if os.getenv("RAG_MOCK_API") == "1":
        import hashlib
        import random

        embeddings = []
        for text in texts:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            rng = random.Random(h)
            vector = [rng.uniform(-1.0, 1.0) for _ in range(768)]
            embeddings.append(vector)
        return embeddings

    # 1. Intentar delegar al Worker Daemon precargado en VRAM
    from rag_local.daemon.client import try_daemon_embed

    daemon_embeddings = try_daemon_embed(texts)
    if daemon_embeddings is not None:
        return daemon_embeddings

    # 2. Modo standalone sin daemon: usar ModelWorker ONNX
    try:
        worker = get_standalone_worker()
        return worker.sync_embed(texts)
    except Exception as e:
        logger.exception("Fallo al generar embeddings locales")
        raise EmbeddingError(f"Error al generar embeddings locales: {e}") from e
