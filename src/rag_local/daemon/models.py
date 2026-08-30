import contextlib
import ctypes
import gc
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from rag_local.core import config
from rag_local.core.exceptions import EmbeddingError
from rag_local.core.logging import logger

if sys.platform == "win32":
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

if importlib.util.find_spec("hf_transfer") is not None:
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")


def _download_model_file(
    repo_id: str,
    filename: str,
    force_download: bool = False,
) -> str | None:
    """Descarga o localiza un archivo de Hugging Face de forma segura."""
    if not force_download:
        with contextlib.suppress(Exception):
            cached = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_files_only=True,
            )
            if os.path.isfile(cached):
                if os.path.getsize(cached) > 1024:
                    return cached
                return None

    try:
        path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_files_only=False,
            force_download=force_download,
        )
        if os.path.isfile(path) and os.path.getsize(path) > 1024:
            return path
    except Exception:
        return None
    return None


def is_model_repo_cached(repo_id: str) -> bool:
    """Comprueba si el modelo y tokenizer están en la caché local sin red."""
    # 1. Comprobar ruta directa en disco
    direct_path = Path(repo_id)
    if direct_path.is_dir():
        return (direct_path / "tokenizer.json").is_file() and (
            direct_path / "model.onnx"
        ).is_file()

    # 2. Comprobar en RAG_MODELS_DIR
    models_dir = config.RAG_MODELS_DIR / Path(repo_id).name
    if (
        models_dir.is_dir()
        and (models_dir / "tokenizer.json").is_file()
        and (models_dir / "model.onnx").is_file()
    ):
        return True

    # 3. Comprobar caché local de rag-local (DAEMON_CACHE_DIR / models)
    local_dir = config.DAEMON_CACHE_DIR / "models" / Path(repo_id).name
    if (
        local_dir.is_dir()
        and (local_dir / "tokenizer.json").is_file()
        and (local_dir / "model.onnx").is_file()
    ):
        return True

    # 4. Comprobar Hugging Face hub cache
    try:
        tok_cached = hf_hub_download(
            repo_id=repo_id,
            filename="tokenizer.json",
            local_files_only=True,
        )
        if not (os.path.isfile(tok_cached) and os.path.getsize(tok_cached) > 1024):
            return False

        with contextlib.suppress(Exception):
            onnx = hf_hub_download(
                repo_id=repo_id,
                filename="onnx/model.onnx",
                local_files_only=True,
            )
            if os.path.isfile(onnx) and os.path.getsize(onnx) > 1024:
                if os.path.getsize(onnx) < 50 * 1024 * 1024:
                    try:
                        data = hf_hub_download(
                            repo_id=repo_id,
                            filename="onnx/model.onnx_data",
                            local_files_only=True,
                        )
                        if not (os.path.isfile(data) and os.path.getsize(data) > 1024):
                            return False
                    except Exception:
                        return False
                return True

        with contextlib.suppress(Exception):
            onnx_root = hf_hub_download(
                repo_id=repo_id,
                filename="model.onnx",
                local_files_only=True,
            )
            if os.path.isfile(onnx_root) and os.path.getsize(onnx_root) > 1024:
                return True

        return False
    except Exception:
        return False


def get_models_cache_status() -> dict[str, bool]:
    """Retorna el estado de disponibilidad en caché local de los modelos."""
    emb_repo = config.ONNX_EMBEDDING_MODEL
    rerank_repo = config.ONNX_RERANKER_MODEL

    return {
        "embeddings": is_model_repo_cached(emb_repo),
        "reranker": is_model_repo_cached(rerank_repo),
    }


class ModelWorker:
    """Gestiona la inferencia en GPU con ONNX Runtime (CUDA FP32) y Tokenizers."""

    def __init__(self) -> None:
        self.embedding_session: ort.InferenceSession | None = None
        self.embedding_tokenizer: Tokenizer | None = None
        self.reranker_session: ort.InferenceSession | None = None
        self.reranker_tokenizer: Tokenizer | None = None
        self.run_options = ort.RunOptions()
        self.run_options.add_run_config_entry(
            "memory.enable_memory_arena_shrinkage", "gpu:0"
        )

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

    def _resolve_model_files(
        self,
        repo_id: str,
        force_download: bool = False,
    ) -> tuple[str, str]:
        """Descarga o localiza el modelo ONNX FP32 y su tokenizer."""
        # 1. Comprobar si es un directorio en disco
        direct_path = Path(repo_id)
        if direct_path.is_dir():
            tok = direct_path / "tokenizer.json"
            onnx = direct_path / "model.onnx"
            if tok.is_file() and onnx.is_file():
                return str(tok), str(onnx)

        # 2. Comprobar en RAG_MODELS_DIR
        models_dir = config.RAG_MODELS_DIR / Path(repo_id).name
        if models_dir.is_dir() and not force_download:
            tok = models_dir / "tokenizer.json"
            onnx = models_dir / "model.onnx"
            if tok.is_file() and onnx.is_file():
                return str(tok), str(onnx)

        # 3. Comprobar en el directorio de modelos de DAEMON_CACHE_DIR
        local_model_dir = config.DAEMON_CACHE_DIR / "models" / Path(repo_id).name
        if local_model_dir.is_dir() and not force_download:
            tok = local_model_dir / "tokenizer.json"
            onnx = local_model_dir / "model.onnx"
            if tok.is_file() and onnx.is_file():
                return str(tok), str(onnx)

        # 3. Intentar resolución desde Hugging Face Hub (ej. Reranker)
        tok_path = _download_model_file(
            repo_id, "tokenizer.json", force_download=force_download
        )
        onnx_path = None
        if tok_path:
            onnx_path = _download_model_file(
                repo_id, "onnx/model.onnx", force_download=force_download
            )
            if not onnx_path:
                onnx_path = _download_model_file(
                    repo_id, "model.onnx", force_download=force_download
                )
            if onnx_path:
                _download_model_file(
                    repo_id, "onnx/model.onnx_data", force_download=force_download
                )
                return tok_path, onnx_path

        # 4. Si es jina-code-embeddings y no existe ONNX en HF, exportar localmente
        if "jina-code-embeddings" in repo_id.lower():
            local_model_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Exportando {repo_id} a ONNX FP32 en {local_model_dir}...")
            export_cmd = [
                "uvx",
                "--from",
                "optimum[onnxruntime]",
                "optimum-cli",
                "export",
                "onnx",
                "--model",
                repo_id,
                "--task",
                "feature-extraction",
                "--dtype",
                "fp32",
                "--trust-remote-code",
                str(local_model_dir),
            ]
            import subprocess

            proc = subprocess.run(  # noqa: S603
                export_cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            tok = local_model_dir / "tokenizer.json"
            onnx = local_model_dir / "model.onnx"
            if tok.is_file() and onnx.is_file():
                return str(tok), str(onnx)
            logger.error(
                f"Error al exportar {repo_id} a ONNX: {proc.stderr or proc.stdout}"
            )

        msg = (
            f"No se encontró un binario ONNX válido para '{repo_id}'. "
            "Verifique su conexión a Internet o ejecute 'rag-daemon download'."
        )
        logger.error(msg)
        raise EmbeddingError(msg)

    def load_and_warmup_models(self) -> None:
        """Carga los modelos ONNX y tokenizers en memoria ejecutando un warm-up."""
        t0 = time.perf_counter()

        emb_repo = config.ONNX_EMBEDDING_MODEL
        rerank_repo = config.ONNX_RERANKER_MODEL

        logger.info(
            f"Cargando modelos ONNX (CUDA FP32): "
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
            "gpu_mem_limit": 4 * 1024 * 1024 * 1024,
            "do_copy_in_default_stream": "1",
            "cudnn_conv_use_max_workspace": "0",
        }
        providers: list[Any] = [
            ("CUDAExecutionProvider", cuda_options),
            "CPUExecutionProvider",
        ]

        sess_options = ort.SessionOptions()
        opt_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.graph_optimization_level = opt_level
        sess_options.log_severity_level = 3  # Silencia advertencias de Memcpy

        # 1. Cargar Tokenizers y Modelos ONNX de Embeddings
        emb_tok_path, emb_model_path = self._resolve_model_files(emb_repo)

        self.embedding_tokenizer = Tokenizer.from_file(emb_tok_path)
        pad_id = self.embedding_tokenizer.token_to_id("<|endoftext|>")
        if pad_id is None:
            pad_id = self.embedding_tokenizer.token_to_id("[PAD]") or 0
        pad_token = (
            "<|endoftext|>"
            if self.embedding_tokenizer.token_to_id("<|endoftext|>") is not None
            else "[PAD]"
        )
        self.embedding_tokenizer.enable_padding(
            direction="left",
            pad_id=pad_id,
            pad_token=pad_token,
            pad_to_multiple_of=32,
        )
        self.embedding_tokenizer.enable_truncation(max_length=1024, direction="left")

        self.embedding_session = ort.InferenceSession(
            emb_model_path, sess_options, providers=providers
        )

        # 2. Cargar Tokenizers y Modelos ONNX de Reranker
        rerank_tok_path, rerank_model_path = self._resolve_model_files(rerank_repo)

        self.reranker_tokenizer = Tokenizer.from_file(rerank_tok_path)
        self.reranker_tokenizer.enable_padding(
            direction="right",
            pad_id=0,
            pad_token="[PAD]",  # noqa: S106
            pad_to_multiple_of=32,
        )
        self.reranker_tokenizer.enable_truncation(max_length=1024)

        self.reranker_session = ort.InferenceSession(
            rerank_model_path, sess_options, providers=providers
        )

        # 3. Warm-up
        logger.info("Ejecutando warm-up de sesiones ONNX...")
        warmup_texts = [
            (
                "Find the most relevant code snippet given the following query:\n"
                "Warm-up query"
            ),
            "Candidate code snippet:\nDummy code snippet for warming up ONNX.",
        ]
        _ = self.sync_embed(warmup_texts)
        _ = self.sync_rerank(
            "Warm-up query", ["Dummy code snippet for warming up ONNX."]
        )

        elapsed = time.perf_counter() - t0
        active_provider = self.embedding_session.get_providers()[0]
        logger.info(
            f"Modelos ONNX listos en {elapsed:.1f}s (Proveedor: {active_provider})"
        )

    def maybe_cleanup_vram(self) -> None:
        """Recolecta memoria residual de NumPy / Python y vacía caches de GPU."""
        gc.collect()
        with contextlib.suppress(Exception):
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()

    def sync_embed(self, texts: list[str]) -> list[list[float]]:
        """Genera embeddings normalizados L2 mediante inferencia ONNX vectorizada."""
        if self.embedding_session is None or self.embedding_tokenizer is None:
            raise EmbeddingError("Sesión de embeddings ONNX no inicializada.")

        if not texts:
            return []

        micro_batch_size = max(1, config.DAEMON_EMBED_BATCH_SIZE)
        all_embeddings: list[list[float]] = []

        input_names = [inp.name for inp in self.embedding_session.get_inputs()]

        for i in range(0, len(texts), micro_batch_size):
            chunk_texts = texts[i : i + micro_batch_size]
            encodings = self.embedding_tokenizer.encode_batch(chunk_texts)
            input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
            attention_mask = np.array(
                [e.attention_mask for e in encodings], dtype=np.int64
            )

            inputs: dict[str, Any] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "position_ids" in input_names:
                inputs["position_ids"] = np.clip(
                    np.cumsum(attention_mask, axis=-1) - 1,
                    a_min=0,
                    a_max=None,
                ).astype(np.int64)
            if "token_type_ids" in input_names:
                inputs["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

            outputs = self.embedding_session.run(None, inputs, self.run_options)
            raw_output = np.asarray(outputs[0])

            if raw_output.ndim == 2:
                # Salida 2D directa: (batch_size, hidden_dim)
                embeddings = raw_output
            elif raw_output.ndim == 3:
                # Salida 3D: (batch, seq, dim) -> last-token pooling con left padding
                # Con left-padding, la secuencia útil termina en el último token (-1)
                embeddings = raw_output[:, -1, :]
            else:
                raise EmbeddingError(
                    f"Forma de salida de embeddings no soportada: {raw_output.shape}"
                )

            # L2 normalization
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            normalized = embeddings / np.clip(norms, a_min=1e-9, a_max=None)
            all_embeddings.extend([list(map(float, vec)) for vec in normalized])

        self.maybe_cleanup_vram()
        return all_embeddings

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

            outputs = self.reranker_session.run(None, inputs, self.run_options)
            logits = np.asarray(outputs[0]).flatten()
            scores = 1.0 / (1.0 + np.exp(-np.clip(logits, -50.0, 50.0)))  # Sigmoide

            for offset, score in enumerate(scores):
                all_results.append({"doc_id": i + offset, "score": float(score)})

        all_results.sort(key=lambda x: x["score"], reverse=True)
        self.maybe_cleanup_vram()
        return all_results

    def cleanup_models(self) -> None:
        """Cierra las sesiones de ONNX Runtime y libera la memoria."""
        self.embedding_session = None
        self.embedding_tokenizer = None
        self.reranker_session = None
        self.reranker_tokenizer = None
        gc.collect()


def download_required_models(
    console: Any = None, force: bool = False
) -> dict[str, str]:
    """Descarga explícita y optimizada de todos los modelos ONNX requeridos."""
    emb_repo = config.ONNX_EMBEDDING_MODEL
    rerank_repo = config.ONNX_RERANKER_MODEL

    worker = ModelWorker()
    downloaded_paths = {}

    tasks = [
        ("Modelo de Embeddings", emb_repo),
        ("Modelo de Reranker", rerank_repo),
    ]

    total_tasks = len(tasks)
    for idx, (label, repo) in enumerate(tasks, start=1):
        if console:
            console.print(
                f"[bold cyan][{idx}/{total_tasks}][/bold cyan] "
                f"Descargando {label} ({repo})..."
            )
        else:
            logger.info(f"[{idx}/{total_tasks}] Descargando {label} ({repo})...")

        try:
            tok_path, model_path = worker._resolve_model_files(
                repo_id=repo,
                force_download=force,
            )
            downloaded_paths[f"{repo}:tokenizer"] = tok_path
            downloaded_paths[f"{repo}:model"] = model_path
            if console:
                console.print(
                    f"    [bold green]Listo:[/bold green] Tokenizer: "
                    f"{Path(tok_path).name}, ONNX: {Path(model_path).name}"
                )
        except Exception as e:
            if console:
                console.print(
                    f"    [bold red]Error al descargar {label}:[/bold red] {e}"
                )
            raise

    return downloaded_paths
