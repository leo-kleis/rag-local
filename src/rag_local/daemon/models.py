import contextlib
import ctypes
import gc
import os
import time
from typing import Any

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from rag_local.core import config
from rag_local.core.exceptions import EmbeddingError
from rag_local.core.logging import logger


class ModelWorker:
    """Gestiona la inferencia en GPU con ONNX Runtime (CUDA FP16) y Tokenizers."""

    def __init__(self) -> None:
        self.embedding_session: ort.InferenceSession | None = None
        self.embedding_tokenizer: Tokenizer | None = None
        self.reranker_session: ort.InferenceSession | None = None
        self.reranker_tokenizer: Tokenizer | None = None

    def get_vram_info(self) -> dict[str, float] | None:
        """Obtiene métricas precisas de VRAM mediante la API de NVIDIA Driver Ctypes."""
        try:
            lib_name = "nvcuda.dll" if os.name == "nt" else "libcuda.so.1"
            cuda = ctypes.CDLL(lib_name)
            cuda.cuInit(0)
            dev = ctypes.c_int()
            cuda.cuDeviceGet(ctypes.byref(dev), 0)
            ctx = ctypes.c_void_p()
            cuda.cuDevicePrimaryCtxRetain(ctypes.byref(ctx), dev)
            cuda.cuCtxSetCurrent(ctx)
            free = ctypes.c_size_t()
            total = ctypes.c_size_t()
            if cuda.cuMemGetInfo_v2(ctypes.byref(free), ctypes.byref(total)) == 0:
                free_mb = free.value / (1024 * 1024)
                total_mb = total.value / (1024 * 1024)
                used_mb = total_mb - free_mb
                return {
                    "free_mb": round(free_mb, 2),
                    "total_mb": round(total_mb, 2),
                    "used_mb": round(used_mb, 2),
                }
        except Exception as e:
            logger.debug(f"No se pudo consultar VRAM vía Ctypes: {e}")
        return None

    def _resolve_model_file(self, repo_id: str, preferred_file: str) -> str:
        """Localiza o descarga el archivo ONNX en la caché local de Hugging Face."""
        try:
            return hf_hub_download(
                repo_id=repo_id,
                filename=preferred_file,
                local_files_only=True,
            )
        except Exception:
            return hf_hub_download(
                repo_id=repo_id,
                filename=preferred_file,
                local_files_only=False,
            )

    def load_and_warmup_models(self) -> None:
        """Carga los modelos ONNX y tokenizers en memoria ejecutando un warm-up."""
        t0 = time.perf_counter()

        emb_repo = config.ONNX_EMBEDDING_MODEL
        rerank_repo = config.ONNX_RERANKER_MODEL

        logger.info(
            f"Cargando modelos ONNX FP16 (CUDA): "
            f"Embeddings='{emb_repo}', Reranker='{rerank_repo}'"
        )

        with contextlib.suppress(Exception):
            import torch  # noqa: F401

        if hasattr(ort, "preload_dlls"):
            with contextlib.suppress(Exception):
                ort.preload_dlls(cuda=True, cudnn=True)

        available = ort.get_available_providers()
        if "CUDAExecutionProvider" not in available:
            raise EmbeddingError(
                "CUDAExecutionProvider no disponible. "
                "El daemon requiere CUDA + cuDNN para funcionar."
            )

        cuda_options = {
            "arena_extend_strategy": "kSameAsRequested",
            "do_copy_in_default_stream": "1",
        }
        providers: list[Any] = [
            ("CUDAExecutionProvider", cuda_options),
            "CPUExecutionProvider",
        ]

        sess_options = ort.SessionOptions()
        opt_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.graph_optimization_level = opt_level

        onnx_fp16 = "onnx/model_fp16.onnx"

        # 1. Cargar Tokenizers y Modelos ONNX de Embeddings
        emb_tok_path = self._resolve_model_file(emb_repo, "tokenizer.json")
        emb_model_path = self._resolve_model_file(emb_repo, onnx_fp16)

        self.embedding_tokenizer = Tokenizer.from_file(emb_tok_path)
        self.embedding_tokenizer.enable_padding(
            pad_id=0,
            pad_token="[PAD]",  # noqa: S106
            length=512,
        )
        self.embedding_tokenizer.enable_truncation(max_length=512)

        self.embedding_session = ort.InferenceSession(
            emb_model_path, sess_options, providers=providers
        )

        # 2. Cargar Tokenizers y Modelos ONNX de Reranker
        rerank_tok_path = self._resolve_model_file(rerank_repo, "tokenizer.json")
        rerank_model_path = self._resolve_model_file(rerank_repo, onnx_fp16)

        self.reranker_tokenizer = Tokenizer.from_file(rerank_tok_path)
        self.reranker_tokenizer.enable_padding(
            pad_id=0,
            pad_token="[PAD]",  # noqa: S106
            length=512,
        )
        self.reranker_tokenizer.enable_truncation(max_length=512)

        self.reranker_session = ort.InferenceSession(
            rerank_model_path, sess_options, providers=providers
        )

        # 3. Warm-up
        logger.info("Ejecutando warm-up de sesiones ONNX...")
        warmup_texts = ["Warm-up query", "Dummy code snippet for warming up ONNX."]
        _ = self.sync_embed(warmup_texts)
        _ = self.sync_rerank(warmup_texts[0], [warmup_texts[1]])

        elapsed = time.perf_counter() - t0
        active_provider = self.embedding_session.get_providers()[0]
        logger.info(
            f"Modelos ONNX listos en {elapsed:.1f}s (Proveedor: {active_provider})"
        )

    def maybe_cleanup_vram(self) -> None:
        """Recolecta memoria residual de NumPy / Python."""
        gc.collect()

    def sync_embed(self, texts: list[str]) -> list[list[float]]:
        """Genera embeddings normalizados L2 mediante inferencia ONNX vectorizada."""
        if self.embedding_session is None or self.embedding_tokenizer is None:
            raise EmbeddingError("Sesión de embeddings ONNX no inicializada.")

        if not texts:
            return []

        encodings = self.embedding_tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        input_names = [inp.name for inp in self.embedding_session.get_inputs()]
        inputs: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in input_names:
            inputs["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self.embedding_session.run(None, inputs)
        token_embeddings = np.asarray(outputs[0])  # (batch_size, seq_len, hidden_dim)

        # Mean pooling con attention mask
        mask_expanded = np.expand_dims(attention_mask, axis=-1)
        sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
        sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
        mean_pooled = sum_embeddings / sum_mask

        # L2 normalization
        norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
        normalized = mean_pooled / np.clip(norms, a_min=1e-9, a_max=None)

        return [list(map(float, vec)) for vec in normalized]

    def sync_rerank(self, query: str, docs: list[str]) -> list[dict[str, Any]]:
        """Reordena documentos calculando scoring semántico sobre logits ONNX."""
        if self.reranker_session is None or self.reranker_tokenizer is None:
            return [{"doc_id": i, "score": 0.0} for i in range(len(docs))]

        if not docs:
            return []

        batch_size = config.DAEMON_RERANKER_BATCH_SIZE
        all_results: list[dict[str, Any]] = []

        input_names = [inp.name for inp in self.reranker_session.get_inputs()]

        for i in range(0, len(docs), batch_size):
            sub_docs = docs[i : i + batch_size]
            pairs = [(query, doc) for doc in sub_docs]
            encodings = self.reranker_tokenizer.encode_batch(pairs)

            input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
            attention_mask = np.array(
                [e.attention_mask for e in encodings], dtype=np.int64
            )

            inputs: dict[str, Any] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "token_type_ids" in input_names:
                inputs["token_type_ids"] = np.array(
                    [e.type_ids for e in encodings], dtype=np.int64
                )

            outputs = self.reranker_session.run(None, inputs)
            logits = np.asarray(outputs[0]).flatten()
            scores = 1.0 / (1.0 + np.exp(-logits))  # Sigmoide

            for offset, score in enumerate(scores):
                all_results.append({"doc_id": i + offset, "score": float(score)})

        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results

    def cleanup_models(self) -> None:
        """Cierra las sesiones de ONNX Runtime y libera la memoria."""
        self.embedding_session = None
        self.embedding_tokenizer = None
        self.reranker_session = None
        self.reranker_tokenizer = None
        gc.collect()
