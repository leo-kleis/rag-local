import argparse
import sys

from rich.console import Console

from rag_local.core import config
from rag_local.services.meta import check_schema_status

stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos para rag-config."""
    parser = argparse.ArgumentParser(
        description=(
            "Muestra el estado del proyecto, índice de LanceDB, versión y modelo."
        )
    )
    parser.add_argument(
        "-p",
        "--project-path",
        type=str,
        required=True,
        help="Ruta al directorio raíz del proyecto.",
    )
    return parser.parse_args()


def main() -> None:
    """Punto de entrada principal del CLI rag-config."""
    try:
        args = parse_arguments()
        from rag_local.services.freshness import setup_and_validate_repo

        repo_path = setup_and_validate_repo(args.project_path)

        is_up_to_date, reason, meta = check_schema_status(config.LANCEDB_PATH)

        if not config.LANCEDB_PATH.exists() or not any(config.LANCEDB_PATH.iterdir()):
            index_status = "No (ejecuta ingest_codebase)"
            schema_status = "No indexado"
            chunks_count = 0
        elif is_up_to_date:
            index_status = "Sí"
            schema_status = (
                f"{meta.get('schema_version', config.SCHEMA_VERSION)} (Actualizada)"
            )
            chunks_count = meta.get("total_chunks", 0)
        else:
            index_status = "Sí"
            schema_status = f"Obsoleta ({reason})"
            chunks_count = meta.get("total_chunks", 0)

        embedding_model = meta.get("embedding_model", config.LOCAL_EMBEDDING_MODEL)

        from rag_local.daemon.client import daemon_healthcheck

        health = daemon_healthcheck()
        from rag_local.daemon.port_file import get_port_file_path

        daemon_file = get_port_file_path()
        if health:
            dev = str(health.get("device", "cpu")).upper()
            port = health.get("port")
            uptime_s = float(health.get("uptime_s", 0))
            mins = int(uptime_s) // 60
            secs = int(uptime_s) % 60
            uptime_str = f"{mins:02d}:{secs:02d}"
            daemon_status = (
                f"Activo (Port {port} | Dispositivo: {dev} | "
                f"Tiempo Activo: {uptime_str} | Path: {daemon_file})"
            )
        else:
            daemon_status = (
                f"Inactivo (Modo bajo demanda | Directorio: {config.DAEMON_DATA_DIR})"
            )

        out = (
            "[RAG Configuration & Index Status]\n"
            f"Proyecto: {repo_path}\n"
            f"Indexado: {index_status}\n"
            f"Esquema RAG: {schema_status}\n"
            f"Modelo Embeddings: {embedding_model}\n"
            f"Worker Daemon: {daemon_status}\n"
            f"Total Chunks: {chunks_count}"
        )
        print(out)  # noqa: T201
    except Exception as e:
        stderr_console.print(
            f"[bold red]Error al obtener la configuración: {e}[/bold red]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
