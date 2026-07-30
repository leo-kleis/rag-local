import argparse
import json
import sys
from pathlib import Path

from rich.console import Console

from rag_local.core import config
from rag_local.services.styles import format_styles_summary, get_styles_summary

stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos para rag-styles."""
    parser = argparse.ArgumentParser(
        description=(
            "Genera el mapa del sistema de estilos y auditoría de clases CSS obsoletas."
        )
    )
    parser.add_argument(
        "-p",
        "--project-path",
        type=str,
        required=True,
        help="Ruta al directorio raíz del proyecto.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Imprime la salida en formato JSON bruto.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    # Forzar UTF-8 y desactivar ANSI cuando la salida no es un terminal (L1, L3)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    is_tty = sys.stdout.isatty()
    stdout_console = Console(force_terminal=is_tty, no_color=not is_tty)

    repo_path = Path(args.project_path).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        stderr_console.print(
            f"[bold red]Error: La ruta especificada no existe: {repo_path}[/bold red]"
        )
        sys.exit(1)

    config.REPO_ROOT = repo_path
    config.LANCEDB_PATH = repo_path / ".lancedb"

    try:
        styles_data = get_styles_summary(str(repo_path))
        if args.json:
            stdout_console.print(json.dumps(styles_data, indent=2, ensure_ascii=False))
        else:
            stdout_console.print(format_styles_summary(styles_data))
    except Exception as e:
        stderr_console.print(
            f"[bold red]Error al generar el mapa de estilos: {e}[/bold red]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
