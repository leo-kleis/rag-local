import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from aiohttp import web

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.daemon.lifecycle import LifecycleManager
from rag_local.daemon.models import ModelWorker
from rag_local.daemon.port_file import (
    delete_port_file,
    generate_token,
    write_port_file,
)


class ModelWorkerServer:
    """Servidor HTTP del Worker Daemon para embeddings y reranking."""

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
        self.token = os.getenv("DAEMON_TOKEN") or generate_token()
        self.worker = ModelWorker()
        self.lifecycle = LifecycleManager(parent_pid=parent_pid)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._shutdown_event = asyncio.Event()
        self._current_task: str | None = None

    @property
    def device(self) -> str:
        return "cuda"

    @property
    def embedding_model(self) -> Any:
        return self.worker.embedding_session

    @embedding_model.setter
    def embedding_model(self, value: Any) -> None:
        self.worker.embedding_session = value

    @property
    def reranker_model(self) -> Any:
        return self.worker.reranker_session

    @reranker_model.setter
    def reranker_model(self, value: Any) -> None:
        self.worker.reranker_session = value

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
        return self.worker.get_vram_info()

    def _load_and_warmup_models(self) -> None:
        """Carga los modelos en memoria y ejecuta warm-up."""
        self.worker.load_and_warmup_models()

    def _maybe_cleanup_vram(self) -> None:
        """Libera la caché de VRAM dejando solo los modelos en memoria."""
        self.worker.maybe_cleanup_vram()

    def _sync_embed(self, texts: list[str]) -> list[list[float]]:
        return self.worker.sync_embed(texts)

    def _sync_rerank(self, query: str, docs: list[str]) -> list[dict[str, Any]]:
        return self.worker.sync_rerank(query, docs)

    @web.middleware
    async def _security_middleware(
        self,
        request: web.Request,
        handler: Any,
    ) -> web.StreamResponse:
        # 1. Validación de Host Header (prevención de DNS Rebinding)
        host_header = request.headers.get("Host", "").split(":")[0]
        if host_header not in ("127.0.0.1", "localhost", "0.0.0.0", "rag-daemon"):  # noqa: S104
            return web.json_response(
                {
                    "error": (
                        "Forbidden: Acceso restringido exclusivamente a loopback local."
                    )
                },
                status=403,
            )

        # 2. Validación de Token Criptográfico (excepto healthcheck de Docker)
        if request.path != "/health":
            auth_header = request.headers.get("Authorization", "")
            expected = f"Bearer {self.token}"
            if auth_header != expected:
                return web.json_response(
                    {"error": "Unauthorized: Token inválido o no suministrado."},
                    status=401,
                )
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
            is_ingestion = bool(data.get("is_ingestion", True))
            if not isinstance(texts, list):
                return web.json_response(
                    {"error": "'texts' debe ser una lista de strings."},
                    status=400,
                )
            if not texts:
                return web.json_response({"embeddings": []})

            if is_ingestion:
                self._current_task = "ingestion"

            try:
                loop = asyncio.get_running_loop()
                embeddings = await loop.run_in_executor(
                    self.executor, self._sync_embed, texts
                )
                return web.json_response({"embeddings": embeddings})
            finally:
                if is_ingestion:
                    self._current_task = None
        except Exception as e:
            logger.exception("Error en endpoint /embed del daemon")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_rerank(self, request: web.Request) -> web.Response:
        if self._current_task == "ingestion":
            return web.json_response(
                {
                    "status": "busy",
                    "task": "ingestion",
                    "message": (
                        "El daemon se encuentra ocupado ejecutando "
                        "una ingesta masiva de código."
                    ),
                },
                status=503,
            )
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

    async def handle_query(self, request: web.Request) -> web.Response:
        if self._current_task == "ingestion":
            return web.json_response(
                {
                    "status": "busy",
                    "task": "ingestion",
                    "message": (
                        "El daemon se encuentra ocupado ejecutando "
                        "una ingesta masiva de código."
                    ),
                },
                status=503,
            )
        try:
            data = await request.json()
            query_text = data.get("query")
            if not isinstance(query_text, str) or not query_text.strip():
                return web.json_response(
                    {"error": "'query' requerido como string no vacío."},
                    status=400,
                )
            scope = data.get("scope")
            k = int(data.get("k", 4))
            full_block = bool(data.get("full_block", False))
            generate_response = bool(data.get("generate_response", False))
            respond_in_english = bool(data.get("respond_in_english", False))

            from rag_local.services.rag import process_query

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self.executor,
                process_query,
                query_text,
                scope,
                respond_in_english,
                k,
                generate_response,
                full_block,
            )
            return web.json_response(result)
        except Exception as e:
            logger.exception("Error en endpoint /query del daemon")
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
        self.worker.cleanup_models()
        self._shutdown_event.set()

    async def start(self) -> int:
        """Arranca el servidor y escribe el port file tras el warm-up."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.executor, self._load_and_warmup_models)

        self.app = web.Application(middlewares=[self._security_middleware])
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_post("/embed", self.handle_embed)
        self.app.router.add_post("/rerank", self.handle_rerank)
        self.app.router.add_post("/query", self.handle_query)
        self.app.router.add_post("/claim", self.handle_claim)

        class FilteredAccessLogger(web.AccessLogger):
            def log(
                self,
                request: web.BaseRequest,
                response: web.StreamResponse,
                time: float,
            ) -> None:
                if request.path == "/health":
                    return
                super().log(request, response, time)

        self.runner = web.AppRunner(self.app, access_log_class=FilteredAccessLogger)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        actual_port = self._get_actual_port()

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
