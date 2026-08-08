import argparse
import asyncio
import sys
from pathlib import Path

from rag_local.core.logging import logger
from rag_local.daemon.server import ModelWorkerServer


def main() -> None:
    """Punto de entrada principal al ejecutar python -m rag_local.daemon."""
    parser = argparse.ArgumentParser(
        description="Worker Daemon para modelos PyTorch de RAG Local"
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="PID del proceso padre a monitorear",
    )
    parser.add_argument(
        "--port", type=int, default=0, help="Puerto TCP a enlazar (0 para dinámico)"
    )
    parser.add_argument(
        "--lancedb-path",
        type=str,
        default=None,
        help="Ruta personalizada a la base de datos LanceDB",
    )
    args = parser.parse_args()

    lancedb_path = Path(args.lancedb_path) if args.lancedb_path else None
    server = ModelWorkerServer(
        parent_pid=args.parent_pid,
        lancedb_path=lancedb_path,
        port=args.port,
    )

    async def _run() -> None:
        await server.start()
        await server.wait_until_stopped()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Worker Daemon detenido por señal de teclado (KeyboardInterrupt).")
    except Exception as e:
        logger.exception(f"Error fatal en el Worker Daemon: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
