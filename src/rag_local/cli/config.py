import argparse
import sys
from pathlib import Path

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
        repo_path = Path(args.project_path).resolve()

        if not repo_path.exists() or not repo_path.is_dir():
            stderr_console.print(
                "[bold red]Error: La ruta no existe o no es un directorio: "
                f"{repo_path}[/bold red]"
            )
            sys.exit(1)

        config.REPO_ROOT = repo_path
        config.LANCEDB_PATH = repo_path / ".lancedb"

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

        health = daemon_healthcheck(config.LANCEDB_PATH)
        if health:
            dev = str(health.get("device", "cpu")).upper()
            port = health.get("port")
            idle_s = float(health.get("idle_s", 0))
            idle_str = f"{int(idle_s // 60)}m" if idle_s >= 60 else f"{int(idle_s)}s"
            daemon_status = (
                f"Activo (Port {port} | Dispositivo: {dev} | Inactivo: {idle_str})"
            )
        else:
            daemon_status = "Inactivo (Modo bajo demanda)"

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
