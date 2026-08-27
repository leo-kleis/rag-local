from typing import Any

from rag_local.core import config
from rag_local.core.exceptions import EmbeddingError
from rag_local.core.logging import logger

_reranker: Any = None


def get_reranker() -> Any:
    """Obtiene o inicializa el Reranker en GPU de forma perezosa."""
    global _reranker
    if _reranker is None:
        import torch
        from rerankers import Reranker

        if not torch.cuda.is_available():
            raise EmbeddingError(
                "GPU NVIDIA con CUDA no detectada. "
                "rag-local requiere una GPU NVIDIA para funcionar."
            )

        model_name = getattr(config, "RERANKER_MODEL", "BAAI/bge-reranker-base")
        try:
            _reranker = Reranker(
                model_name,
                device="cuda",
                model_type="cross-encoder",
                verbose=0,
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
                device="cuda",
                model_type="cross-encoder",
                verbose=0,
                model_kwargs={"local_files_only": False},
                tokenizer_kwargs={"local_files_only": False},
            )
    return _reranker
