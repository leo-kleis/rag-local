import gc
import os
from typing import TYPE_CHECKING

from rag_local.core import config
from rag_local.core.exceptions import EmbeddingError
from rag_local.core.logging import logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

# Singleton para el modelo
_model: "SentenceTransformer | None" = None


def get_model() -> "SentenceTransformer":
    """Carga e inicializa el modelo local de embeddings en GPU (Singleton)."""
    global _model
    if _model is not None:
        return _model

    import torch
    from sentence_transformers import SentenceTransformer

    if not torch.cuda.is_available():
        raise EmbeddingError(
            "GPU NVIDIA con CUDA no detectada. "
            "rag-local requiere una GPU NVIDIA para funcionar."
        )

    torch.cuda.set_per_process_memory_fraction(config.DAEMON_VRAM_FRACTION)
    model_name = config.LOCAL_EMBEDDING_MODEL
    logger.info(f"Cargando modelo de embeddings local '{model_name}' en GPU (cuda)")

    try:
        _model = SentenceTransformer(
            model_name, device="cuda", trust_remote_code=True, local_files_only=True
        )
    except Exception:
        logger.info(
            f"Modelo '{model_name}' no detectado en local. "
            "Conectando a Hugging Face Hub..."
        )
        _model = SentenceTransformer(
            model_name, device="cuda", trust_remote_code=True, local_files_only=False
        )

    return _model


def get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Genera embeddings locales para una lista de textos.

    Intenta delegar al Worker Daemon en VRAM primero; si no está activo,
    hace fallback automático al modelo local en memoria con protección VRAM.
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

    # 2. Modo standalone sin daemon: cargar modelo localmente con protección de VRAM
    try:
        import torch

        model = get_model()
        batch_size = 32
        while batch_size >= 1:
            try:
                with torch.inference_mode():
                    raw_embeddings = model.encode(
                        texts,
                        batch_size=batch_size,
                        show_progress_bar=False,
                        convert_to_numpy=True,
                    )
                    res = [list(map(float, emb)) for emb in raw_embeddings]
                    del raw_embeddings
                    torch.cuda.synchronize()
                    gc.collect()
                    torch.cuda.empty_cache()
                    return res
            except RuntimeError as e:
                if "out of memory" not in str(e).lower():
                    raise
                logger.warning(
                    f"OOM standalone con batch_size={batch_size}. "
                    f"Reduciendo a {batch_size // 2}."
                )
                gc.collect()
                torch.cuda.empty_cache()
                batch_size //= 2

        gc.collect()
        torch.cuda.empty_cache()
        raise EmbeddingError(
            "VRAM insuficiente en modo standalone: no se pudo completar la inferencia."
        )
    except Exception as e:
        logger.exception("Fallo al generar embeddings locales")
        raise EmbeddingError(f"Error al generar embeddings locales: {e}") from e
