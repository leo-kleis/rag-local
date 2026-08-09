import asyncio
import contextlib
import gc
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import torch
from aiohttp import web

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.daemon.lifecycle import LifecycleManager
from rag_local.daemon.port_file import (
    delete_port_file,
    generate_token,
    write_port_file,
)


class ModelWorkerServer:
    """Servidor HTTP del Worker Daemon que precarga modelos PyTorch en VRAM/RAM."""

    def __init__(
        self,
        parent_pid: int | None = None,
        daemon_data_dir: Path | None = None,
        port: int = 0,
        host: str = "127.0.0.1",
    ) -> None:
        self.parent_pid = parent_pid
        self.daemon_data_dir = (
            daemon_data_dir if daemon_data_dir is not None else config.DAEMON_DATA_DIR
        )
        self.port = port
        self.host = host
        self.token = generate_token()

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.embedding_model: Any = None
        self.reranker_model: Any = None

        self.lifecycle = LifecycleManager(parent_pid=parent_pid)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._shutdown_event = asyncio.Event()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _get_actual_port(self) -> int:
        """Obtiene el puerto TCP real asignado por el sistema operativo."""
        if self.site is not None:
            server: Any = getattr(self.site, "_server", None)
            if server is not None:
                sockets: Any = getattr(server, "sockets", None)
                if sockets and len(sockets) > 0:
                    sockname = getattr(sockets[0], "getsockname", None)
                    if callable(sockname):
                        name = sockname()
                        if isinstance(name, tuple) and len(name) > 1:
                            return int(name[1])
        return self.port

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

    def _load_and_warmup_models(self) -> None:
        """Carga los modelos en memoria y ejecuta pasadas de warm-up obligatorias."""
        from rerankers import Reranker
        from sentence_transformers import SentenceTransformer

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
                model_kwargs={"local_files_only": True},
                tokenizer_kwargs={"local_files_only": True},
            )
        except Exception:
            self.reranker_model = Reranker(
                rerank_name,
                device=self.device,
                model_type="cross-encoder",
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
        logger.info("Warm-up completado. Modelos listos para inferencia ultra-rápida.")

    def _sync_embed(self, texts: list[str]) -> list[list[float]]:
        with torch.inference_mode():
            embeddings = self.embedding_model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            return [list(map(float, emb)) for emb in embeddings]

    def _sync_rerank(self, query: str, docs: list[str]) -> list[dict[str, Any]]:
        with torch.inference_mode():
            ranked = self.reranker_model.rank(query=query, docs=docs)
            results = []
            for item in ranked:
                orig_idx = int(item.doc_id)
                score = float(getattr(item, "score", 0.0))
                results.append({"doc_id": orig_idx, "score": score})
            return results

    @web.middleware
    async def _security_middleware(
        self,
        request: web.Request,
        handler: Any,
    ) -> web.StreamResponse:
        # 1. Validación de Host Header (prevención de DNS Rebinding)
        host_header = request.headers.get("Host", "").split(":")[0]
        if host_header not in ("127.0.0.1", "localhost"):
            return web.json_response(
                {
                    "error": (
                        "Forbidden: Acceso restringido exclusivamente a loopback local."
                    )
                },
                status=403,
            )

        # 2. Validación de Token Criptográfico
        auth_header = request.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        if auth_header != expected:
            return web.json_response(
                {"error": "Unauthorized: Token inválido o no suministrado."},
                status=401,
            )

        # Registrar actividad para inactividad (excluyendo healthcheck)
        if request.path != "/health":
            self.lifecycle.record_activity()
        return await handler(request)

    async def handle_health(self, request: web.Request) -> web.Response:
        vram_info = self.get_vram_info()
        return web.json_response(
            {
                "status": "ok",
                "device": self.device,
                "uptime_s": round(self.lifecycle.get_uptime_s(), 2),
                "idle_s": round(self.lifecycle.get_idle_s(), 2),
                "in_grace_period": self.lifecycle.is_in_grace_period(),
                "parent_pid": self.lifecycle.parent_pid,
                "vram": vram_info,
            }
        )

    async def handle_embed(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            texts = data.get("texts", [])
            if not isinstance(texts, list):
                return web.json_response(
                    {"error": "'texts' debe ser una lista de strings."},
                    status=400,
                )
            if not texts:
                return web.json_response({"embeddings": []})

            loop = asyncio.get_running_loop()
            embeddings = await loop.run_in_executor(
                self.executor, self._sync_embed, texts
            )
            return web.json_response({"embeddings": embeddings})
        except Exception as e:
            logger.exception("Error en endpoint /embed del daemon")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_rerank(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            query = data.get("query")
            docs = data.get("docs", [])
            if not isinstance(query, str) or not isinstance(docs, list):
                return web.json_response(
                    {"error": "'query' debe ser string y 'docs' debe ser lista."},
                    status=400,
                )
            if not docs:
                return web.json_response({"results": []})

            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                self.executor, self._sync_rerank, query, docs
            )
            return web.json_response({"results": results})
        except Exception as e:
            logger.exception("Error en endpoint /rerank del daemon")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_claim(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            new_parent_pid = data.get("new_parent_pid")
            if not isinstance(new_parent_pid, int):
                return web.json_response(
                    {"error": "'new_parent_pid' requerido."}, status=400
                )

            success = self.lifecycle.claim(new_parent_pid)
            if success:
                actual_port = self._get_actual_port()
                write_port_file(
                    {
                        "port": actual_port,
                        "pid": os.getpid(),
                        "parent_pid": new_parent_pid,
                        "token": self.token,
                        "device": self.device,
                    },
                    self.daemon_data_dir,
                )
                return web.json_response(
                    {"status": "claimed", "parent_pid": new_parent_pid}
                )
            return web.json_response(
                {"error": f"PID {new_parent_pid} no existe."}, status=400
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_shutdown(self, request: web.Request) -> web.Response:
        logger.info("Solicitud explícita de apagado recibida en /shutdown.")
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, lambda: asyncio.create_task(self.shutdown()))
        return web.json_response({"status": "shutting_down"})

    async def shutdown(self) -> None:
        """Apagado limpio: limpia VRAM, elimina port file y activa evento."""
        self.lifecycle.stop()
        delete_port_file(self.daemon_data_dir)

        # Liberar memoria de GPU
        self.embedding_model = None
        self.reranker_model = None
        gc.collect()
        if torch.cuda.is_available():
            with contextlib.suppress(Exception):
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()

        self._shutdown_event.set()

    async def start(self) -> int:
        """Arranca el servidor y escribe el port file tras el warm-up."""
        # Cargar y precalentar modelos
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.executor, self._load_and_warmup_models)

        # Configurar aplicación aiohttp
        self.app = web.Application(middlewares=[self._security_middleware])
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_post("/embed", self.handle_embed)
        self.app.router.add_post("/rerank", self.handle_rerank)
        self.app.router.add_post("/claim", self.handle_claim)
        self.app.router.add_post("/shutdown", self.handle_shutdown)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        # Obtener el puerto real asignado (útil si port=0)
        actual_port = self._get_actual_port()

        # Escribir port file atómico
        write_port_file(
            {
                "port": actual_port,
                "pid": os.getpid(),
                "parent_pid": self.parent_pid,
                "token": self.token,
                "device": self.device,
            },
            self.daemon_data_dir,
        )

        # Iniciar monitoreo de ciclo de vida
        self.lifecycle.start(shutdown_callback=self.shutdown)

        logger.info(
            f"Worker Daemon activo y escuchando en http://{self.host}:{actual_port} "
            f"(PID: {os.getpid()}, Dispositivo: {self.device})"
        )
        return actual_port

    async def wait_until_stopped(self) -> None:
        """Bloquea hasta que se active el evento de apagado."""
        await self._shutdown_event.wait()
        if self.runner:
            await self.runner.cleanup()
        self.executor.shutdown(wait=False)
