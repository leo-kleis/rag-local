import os

from rag_local.core import config
from rag_local.core.exceptions import EmbeddingError
from rag_local.core.logging import logger
from rag_local.core.models import TASK_PREFIXES, EmbeddingTask
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


def _prepare_task_texts(
    texts: list[str], task: EmbeddingTask | str = EmbeddingTask.NL2CODE_DOCUMENT
) -> list[str]:
    """Prefija la instrucción correspondiente a la tarea si no está ya presente."""
    task_key = str(task).lower()
    prefix = TASK_PREFIXES.get(task_key, "")
    if not prefix:
        return texts

    # Lista de todos los prefijos posibles para evitar doble prefijado
    all_prefixes = tuple(p for p in TASK_PREFIXES.values() if p)

    prepared = []
    for t in texts:
        if t.startswith(all_prefixes):
            prepared.append(t)
        else:
            prepared.append(f"{prefix}{t}")
    return prepared


def get_embeddings(
    texts: list[str],
    task: EmbeddingTask | str = EmbeddingTask.NL2CODE_DOCUMENT,
) -> list[list[float]] | None:
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
            vector = [
                rng.uniform(-1.0, 1.0) for _ in range(config.EMBEDDING_VECTOR_DIM)
            ]
            embeddings.append(vector)
        return embeddings

    formatted_texts = _prepare_task_texts(texts, task=task)

    # 1. Intentar delegar al Worker Daemon precargado en VRAM
    from rag_local.daemon.client import try_daemon_embed

    daemon_embeddings = try_daemon_embed(formatted_texts)
    if daemon_embeddings is not None:
        return daemon_embeddings

    # 2. Modo standalone sin daemon: usar ModelWorker ONNX
    try:
        worker = get_standalone_worker()
        return worker.sync_embed(formatted_texts)
    except Exception as e:
        logger.exception("Fallo al generar embeddings locales")
        raise EmbeddingError(f"Error al generar embeddings locales: {e}") from e
