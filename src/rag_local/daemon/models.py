import contextlib
import gc
import time
from typing import Any

import torch

from rag_local.core import config
from rag_local.core.exceptions import EmbeddingError
from rag_local.core.logging import logger


class ModelWorker:
    """Gestiona la carga, inferencia y ciclo de vida de memoria GPU/VRAM."""

    def __init__(self, device: str = "cuda") -> None:
        self.device = device
        self.embedding_model: Any = None
        self.reranker_model: Any = None

    def get_vram_info(self) -> dict[str, float] | None:
        """Obtiene información precisa de VRAM disponible en la GPU."""
        if self.device == "cuda" and torch.cuda.is_available():
            try:
                free_bytes, total_bytes = torch.cuda.mem_get_info()
                used_bytes = total_bytes - free_bytes
                return {
                    "free_mb": round(free_bytes / (1024 * 1024), 2),
                    "total_mb": round(total_bytes / (1024 * 1024), 2),
                    "used_mb": round(used_bytes / (1024 * 1024), 2),
                }
            except Exception as e:
                logger.debug(f"No se pudo consultar mem_get_info: {e}")
        return None

    def load_and_warmup_models(self) -> None:
        """Carga los modelos en memoria y ejecuta pasadas de warm-up obligatorias."""
        from rerankers import Reranker
        from sentence_transformers import SentenceTransformer

        t0 = time.perf_counter()

        if self.device == "cuda":
            torch.cuda.set_per_process_memory_fraction(config.DAEMON_VRAM_FRACTION)
            logger.info(
                f"Límite de VRAM establecido: {config.DAEMON_VRAM_FRACTION * 100:.0f}% "
                f"de la GPU"
            )

        emb_name = config.LOCAL_EMBEDDING_MODEL
        rerank_name = getattr(config, "RERANKER_MODEL", "BAAI/bge-reranker-base")

        logger.info(
            f"Precargando modelos en el Worker Daemon ({self.device}): "
            f"Embeddings='{emb_name}', Reranker='{rerank_name}'"
        )

        # 1. Cargar Embedding Model
        try:
            self.embedding_model = SentenceTransformer(
                emb_name,
                device=self.device,
                trust_remote_code=True,
                local_files_only=True,
            )
        except Exception:
            self.embedding_model = SentenceTransformer(
                emb_name,
                device=self.device,
                trust_remote_code=True,
                local_files_only=False,
            )
        self.embedding_model.eval()

        # 2. Cargar Reranker Model
        try:
            self.reranker_model = Reranker(
                rerank_name,
                device=self.device,
                model_type="cross-encoder",
                verbose=0,
                model_kwargs={"local_files_only": True},
                tokenizer_kwargs={"local_files_only": True},
            )
        except Exception:
            self.reranker_model = Reranker(
                rerank_name,
                device=self.device,
                model_type="cross-encoder",
                verbose=0,
                model_kwargs={"local_files_only": False},
                tokenizer_kwargs={"local_files_only": False},
            )

        # 3. Warm-up obligatorio con torch.inference_mode()
        logger.info("Ejecutando warm-up de modelos en el Worker Daemon...")
        warmup_texts = [
            "Warm-up query",
            "Dummy code snippet for warming up the CUDA kernels.",
        ]
        with torch.inference_mode():
            for _ in range(config.DAEMON_WARMUP_PASSES):
                _ = self.embedding_model.encode(warmup_texts, show_progress_bar=False)
                if hasattr(self.reranker_model, "rank"):
                    _ = self.reranker_model.rank(
                        query=warmup_texts[0], docs=[warmup_texts[1]]
                    )

        if self.device == "cuda" and torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - t0
        logger.info(
            f"Modelos cargados y warm-up completado en {elapsed:.1f}s "
            f"(dispositivo: {self.device})"
        )

    def maybe_cleanup_vram(self) -> None:
        """Libera la caché de VRAM dejando solo los modelos en memoria."""
        if self.device != "cuda" or not torch.cuda.is_available():
            return
        torch.cuda.synchronize()
        gc.collect()
        torch.cuda.empty_cache()
        vram = self.get_vram_info()
        if vram:
            logger.debug(
                f"VRAM post-cleanup: usado={vram['used_mb']:.0f}MB "
                f"libre={vram['free_mb']:.0f}MB"
            )

    def sync_embed(self, texts: list[str]) -> list[list[float]]:
        """Genera embeddings vectoriales de forma sincrónica con mitigación de OOM."""
        batch_size = 32
        while batch_size >= 1:
            try:
                with torch.inference_mode():
                    raw_embeddings = self.embedding_model.encode(
                        texts,
                        batch_size=batch_size,
                        show_progress_bar=False,
                        convert_to_numpy=True,
                    )
                res = [list(map(float, emb)) for emb in raw_embeddings]
                del raw_embeddings
                self.maybe_cleanup_vram()
                return res
            except RuntimeError as e:
                if "out of memory" not in str(e).lower():
                    raise
                logger.warning(
                    f"OOM en /embed con batch_size={batch_size}. "
                    f"Reduciendo a {batch_size // 2}."
                )
                gc.collect()
                torch.cuda.empty_cache()
                batch_size //= 2

        gc.collect()
        torch.cuda.empty_cache()
        raise EmbeddingError(
            "VRAM insuficiente: no se pudo completar la inferencia "
            "incluso con batch_size=1."
        )

    def sync_rerank(self, query: str, docs: list[str]) -> list[dict[str, Any]]:
        """Ejecuta el reordenamiento neuronal de documentos para una consulta."""
        reranker_batch = config.DAEMON_RERANKER_BATCH_SIZE
        with torch.inference_mode():
            all_results: list[dict[str, Any]] = []
            for i in range(0, len(docs), reranker_batch):
                sub_docs = docs[i : i + reranker_batch]
                ranked = self.reranker_model.rank(query=query, docs=sub_docs)
                for item in ranked:
                    orig_idx = i + int(item.doc_id)
                    score = float(getattr(item, "score", 0.0))
                    all_results.append({"doc_id": orig_idx, "score": score})
            all_results.sort(key=lambda x: x["score"], reverse=True)
            self.maybe_cleanup_vram()
            return all_results

    def cleanup_models(self) -> None:
        """Libera por completo los modelos cargados en GPU y memoria principal."""
        self.embedding_model = None
        self.reranker_model = None
        gc.collect()
        if torch.cuda.is_available():
            with contextlib.suppress(Exception):
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
