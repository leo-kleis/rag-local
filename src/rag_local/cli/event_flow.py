import argparse
import sys

# Forzar UTF-8 en los flujos estándar para evitar problemas en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console

from rag_local.core import config
from rag_local.services.event_flow import trace_event_flow

stdout_console = Console()
stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Trace end-to-end event flow across backend and frontend."
    )
    parser.add_argument(
        "-p",
        "--project-path",
        type=str,
        required=True,
        help="Ruta absoluta o relativa al directorio raíz del proyecto.",
    )
    parser.add_argument(
        "-e",
        "--event",
        type=str,
        default="",
        help="Nombre opcional del evento, acción o patrón wildcard (ej. 'follower_*').",
    )
    parser.add_argument(
        "-E",
        "--entity",
        type=str,
        default="",
        help="Filtro opcional por entidad o dominio (ej. 'user', 'chat').",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=15,
        help="Límite de eventos a mostrar en ejecuciones globales (por defecto 15).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    from rag_local.services.freshness import ensure_fresh_index, setup_and_validate_repo

    repo_path = setup_and_validate_repo(args.project_path)
    ensure_fresh_index(repo_path)

    stderr_console.print("Rastreando flujo de eventos en el índice...")
    try:
        result = trace_event_flow(
            config.LANCEDB_PATH,
            target_event=args.event,
            entity=args.entity,
            limit=args.limit,
        )
        stdout_console.print(result)
    except Exception as e:
        stderr_console.print(f"[bold red]Error al rastrear eventos: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
