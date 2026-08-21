import argparse
import json
import sys

from rich.console import Console

from rag_local.services.style_audit import audit_layout_risks, format_audit_report

stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos para rag-style-audit."""
    parser = argparse.ArgumentParser(
        description="Auditoría estática de riesgos y antipatrones de layout CSS."
    )
    parser.add_argument(
        "-p",
        "--project-path",
        type=str,
        required=True,
        help="Ruta al directorio raíz del proyecto.",
    )
    parser.add_argument(
        "-s",
        "--severity",
        type=str,
        default="ALL",
        choices=["CRITICAL", "WARNING", "INFO", "ALL"],
        help="Filtra por nivel de severidad de riesgo (CRITICAL, WARNING, INFO, ALL).",
    )
    parser.add_argument(
        "-f",
        "--file-filter",
        type=str,
        default=None,
        help="Filtra la auditoría a un archivo CSS específico (ej. 'chat.css').",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Imprime la salida en formato JSON bruto.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

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
        report_data = audit_layout_risks(
            repo_path=str(repo_path),
            severity_filter=args.severity,
            file_filter=args.file_filter,
        )
        if args.json:
            stdout_console.print(json.dumps(report_data, indent=2, ensure_ascii=False))
        else:
            stdout_console.print(format_audit_report(report_data))
    except Exception as e:
        stderr_console.print(
            f"[bold red]Error al ejecutar auditoría de layout CSS: {e}[/bold red]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
