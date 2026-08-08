import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
from aiohttp import web

from rag_local.daemon.port_file import write_port_file
from rag_local.daemon.server import ModelWorkerServer


def test_daemon_server_e2e(tmp_path: Path):
    """Prueba end-to-end del servidor HTTP del daemon con mock de modelos.

    Usa aiohttp.ClientSession (async) para que el event loop de aiohttp
    pueda despachar peticiones y respuestas sin deadlock.
    """

    async def _test() -> None:
        server = ModelWorkerServer(
            parent_pid=None,
            lancedb_path=tmp_path,
            port=0,
        )

        mock_emb = MagicMock()
        mock_emb.encode.return_value = [[0.1, 0.2, 0.3]]

        mock_rerank_item = MagicMock()
        mock_rerank_item.doc_id = 0
        mock_rerank_item.score = 2.5
        mock_reranker = MagicMock()
        mock_reranker.rank.return_value = [mock_rerank_item]

        server.embedding_model = mock_emb
        server.reranker_model = mock_reranker

        server.app = web.Application(middlewares=[server._security_middleware])
        server.app.router.add_get("/health", server.handle_health)
        server.app.router.add_post("/embed", server.handle_embed)
        server.app.router.add_post("/rerank", server.handle_rerank)
        server.app.router.add_post("/claim", server.handle_claim)
        server.app.router.add_post("/shutdown", server.handle_shutdown)

        server.runner = web.AppRunner(server.app)
        await server.runner.setup()
        server.site = web.TCPSite(server.runner, server.host, server.port)
        await server.site.start()

        actual_port = server._get_actual_port()
        server.port = actual_port

        write_port_file(
            {
                "port": actual_port,
                "pid": os.getpid(),
                "parent_pid": None,
                "token": server.token,
                "device": server.device,
            },
            tmp_path,
        )

        base_url = f"http://127.0.0.1:{actual_port}"
        headers = {
            "Authorization": f"Bearer {server.token}",
            "Host": "127.0.0.1",
        }

        async with aiohttp.ClientSession() as session:
            # 1. GET /health
            async with session.get(f"{base_url}/health", headers=headers) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

            # 2. POST /embed
            async with session.post(
                f"{base_url}/embed",
                json={"texts": ["test query"]},
                headers=headers,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["embeddings"] == [[0.1, 0.2, 0.3]]

            # 3. POST /rerank
            async with session.post(
                f"{base_url}/rerank",
                json={"query": "q", "docs": ["d0"]},
                headers=headers,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert len(data["results"]) == 1
                assert data["results"][0]["doc_id"] == 0
                assert data["results"][0]["score"] == 2.5

            # 4. POST /shutdown — activa el shutdown (call_later 0.05s)
            async with session.post(
                f"{base_url}/shutdown", headers=headers
            ) as resp:
                assert resp.status == 200

        # Dar tiempo a que el event_loop procese el call_later de shutdown
        await asyncio.sleep(0.2)

        if server.runner:
            await server.runner.cleanup()

    asyncio.run(_test())
