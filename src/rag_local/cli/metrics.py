import argparse
import json
import sys

from rich.console import Console

from rag_local.services.metrics import format_code_metrics, get_code_metrics

stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos para rag-loc."""
    parser = argparse.ArgumentParser(
        description=(
            "Analiza métricas de volumen de líneas de código "
            "e identifica archivos extensos."
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
        "-t",
        "--threshold",
        type=int,
        default=200,
        help=(
            "Umbral mínimo de líneas de código para considerar un archivo extenso "
            "(por defecto: 200)."
        ),
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

    from rag_local.services.freshness import ensure_fresh_index, setup_and_validate_repo

    repo_path = setup_and_validate_repo(args.project_path)
    ensure_fresh_index(repo_path)

    try:
        metrics_data = get_code_metrics(str(repo_path), min_lines=args.threshold)
        if args.json:
            stdout_console.print(json.dumps(metrics_data, indent=2, ensure_ascii=False))
        else:
            stdout_console.print(format_code_metrics(metrics_data))
    except Exception as e:
        stderr_console.print(
            f"[bold red]Error al obtener métricas del código: {e}[/bold red]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
