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
    """Carga e inicializa el modelo local de embeddings usando un patrón Singleton.

    Intenta cargar el modelo en GPU (cuda) y realiza un fallback transparente a
    CPU si falla.
    """
    global _model
    if _model is not None:
        return _model

    import torch
    from sentence_transformers import SentenceTransformer

    model_name = config.LOCAL_EMBEDDING_MODEL
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(
        f"Cargando modelo de embeddings local '{model_name}' en dispositivo: {device}"
    )

    def _load_model_safe(name: str, dev: str) -> SentenceTransformer:
        try:
            return SentenceTransformer(
                name, device=dev, trust_remote_code=True, local_files_only=True
            )
        except Exception:
            logger.info(
                f"Modelo '{name}' no detectado en local. "
                "Conectando a Hugging Face Hub..."
            )
            return SentenceTransformer(
                name, device=dev, trust_remote_code=True, local_files_only=False
            )

    try:
        if device == "cuda":
            try:
                _model = _load_model_safe(model_name, "cuda")
            except Exception as cuda_err:
                logger.warning(
                    f"Fallo al inicializar en GPU CUDA: {cuda_err}. "
                    "Reintentando inicialización en CPU..."
                )
                _model = _load_model_safe(model_name, "cpu")
        else:
            _model = _load_model_safe(model_name, "cpu")
    except Exception as e:
        logger.exception("Error definitivo al cargar el modelo de embeddings local")
        raise EmbeddingError(f"No se pudo cargar el modelo de embeddings: {e}") from e

    return _model


def get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Genera embeddings locales para una lista de textos.

    Intenta delegar al Worker Daemon en VRAM primero; si no está activo,
    hace fallback automático al modelo local en memoria.
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

    # 2. Fallback transparente: cargar modelo desde disco localmente
    try:
        model = get_model()
        # Generar embeddings usando encode.
        # convert_to_numpy=True y convert_to_tensor=False para evitar
        # problemas de tipos de tensores CUDA.
        embeddings = model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [list(map(float, emb)) for emb in embeddings]
    except Exception as e:
        logger.exception("Fallo al generar embeddings locales")
        raise EmbeddingError(f"Error al generar embeddings locales: {e}") from e
