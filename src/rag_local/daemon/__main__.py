import os

# Activar segmentos expandibles de PyTorch ANTES de importar torch.
# Previene fragmentación de VRAM en procesos daemon de larga vida.
# Nota: setdefault respeta configuraciones manuales del entorno, pero si
# se define PYTORCH_CUDA_ALLOC_CONF sin incluir expandable_segments:True,
# esta protección quedará desactivada silenciosamente.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

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
        "--host",
        type=str,
        default=os.getenv("DAEMON_HOST", "0.0.0.0"),  # noqa: S104
        help="Dirección IP de escucha (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--daemon-data-dir",
        type=str,
        default=None,
        help="Directorio personalizado para datos del daemon (override para tests)",
    )
    args = parser.parse_args()

    daemon_data_dir = Path(args.daemon_data_dir) if args.daemon_data_dir else None
    server = ModelWorkerServer(
        parent_pid=args.parent_pid,
        daemon_data_dir=daemon_data_dir,
        port=args.port,
        host=args.host,
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
