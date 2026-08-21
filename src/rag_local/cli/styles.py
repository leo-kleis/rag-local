import argparse
import json
import sys

from rich.console import Console

from rag_local.services.styles import format_styles_summary, get_styles_summary

stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos para rag-styles."""
    parser = argparse.ArgumentParser(
        description=(
            "Genera el mapa del sistema de estilos, "
            "trazabilidad componente-CSS y búsqueda de propiedades."
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
        "-c",
        "--component",
        type=str,
        default=None,
        help="Filtra por nombre o ruta de componente UI.",
    )
    parser.add_argument(
        "--class-name",
        type=str,
        default=None,
        help="Filtra por nombre de clase CSS.",
    )
    parser.add_argument(
        "--property",
        type=str,
        default=None,
        help="Filtra por propiedad o valor CSS (ej. 'display', 'flex', 'word-break').",
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

    repo_path = setup_and_validate_repo(args.project_path, console=stderr_console)
    ensure_fresh_index(repo_path)

    try:
        styles_data = get_styles_summary(
            repo_path=str(repo_path),
            component_filter=args.component,
            class_filter=args.class_name,
            property_filter=args.property,
        )
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
