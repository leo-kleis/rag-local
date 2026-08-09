import argparse
import sys

# Forzar UTF-8 en los flujos estándar para evitar problemas en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console

from rag_local.core import config
from rag_local.services.project_map import generate_project_map

stdout_console = Console()
stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Generate a structural project map from LanceDB metadata."
    )
    parser.add_argument(
        "-p",
        "--project-path",
        type=str,
        required=True,
        help="Ruta absoluta o relativa al directorio raíz del proyecto.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    from rag_local.services.freshness import ensure_fresh_index, setup_and_validate_repo

    repo_path = setup_and_validate_repo(args.project_path)
    ensure_fresh_index(repo_path)

    stderr_console.print("Leyendo metadatos del índice...")
    try:
        result = generate_project_map(config.LANCEDB_PATH)
        stdout_console.print(result)
    except Exception as e:
        stderr_console.print(f"[bold red]Error al generar el mapa: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
