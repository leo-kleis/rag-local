from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger

_reranker: Any = None


def get_reranker() -> Any:
    """Obtiene o inicializa el Reranker de forma perezosa.

    Intenta cargar el modelo localmente primero y, si falla, permite la descarga.
    """
    global _reranker
    if _reranker is None:
        import torch
        from rerankers import Reranker

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model_name = getattr(config, "RERANKER_MODEL", "BAAI/bge-reranker-base")

        try:
            _reranker = Reranker(
                model_name,
                device=device,
                model_type="cross-encoder",
                model_kwargs={"local_files_only": True},
                tokenizer_kwargs={"local_files_only": True},
            )
        except Exception:
            logger.info(
                f"Modelo de reordenamiento '{model_name}' no detectado en local. "
                "Conectando a Hugging Face Hub..."
            )
            _reranker = Reranker(
                model_name,
                device=device,
                model_type="cross-encoder",
                model_kwargs={"local_files_only": False},
                tokenizer_kwargs={"local_files_only": False},
            )
    return _reranker
