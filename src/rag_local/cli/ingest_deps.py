import argparse
import sys

# Forzar UTF-8 en los flujos estándar para evitar problemas en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console

from rag_local.services.dependencies.sync import sync_project_dependencies
from rag_local.services.freshness import setup_and_validate_repo

stdout_console = Console()
stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Ingesta contratos de dependencias en LanceDB global."
    )
    parser.add_argument(
        "-p",
        "--project-path",
        type=str,
        required=True,
        help="Ruta absoluta o relativa al directorio raíz del proyecto.",
    )
    parser.add_argument(
        "--lang",
        type=str,
        choices=["python", "typescript", "node", "javascript", "js", "ts", "py"],
        default=None,
        help="Filtro opcional por lenguaje (ej. 'python', 'typescript', 'node').",
    )
    parser.add_argument(
        "-P",
        "--package",
        type=str,
        default=None,
        help="Filtro opcional para un paquete específico (ej. 'twitchio').",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Fuerza la re-indexación de dependencias aunque ya existan en la caché.",
    )
    return parser.parse_args()


def main() -> None:
    """Punto de entrada para el comando CLI rag-ingest-deps."""
    args = parse_arguments()
    repo_path = setup_and_validate_repo(args.project_path, console=stderr_console)

    stderr_console.print(
        "[bold cyan]Iniciando ingesta de dependencias externas...[/bold cyan]"
    )
    try:
        results = sync_project_dependencies(
            project_path=repo_path,
            language=args.lang,
            package_filter=args.package,
            force=args.force,
            console=stderr_console,
        )
        indexed = results.get("indexed_packages", [])
        cached = results.get("already_cached", [])
        failed = results.get("failed_packages", [])
        total_syms = results.get("total_new_symbols", 0)

        stdout_console.print(
            "\n[bold green][Dependency Ingestion Summary][/bold green]"
        )
        stdout_console.print(f"  • New/Updated packages: {len(indexed)}")
        for pkg in indexed:
            stdout_console.print(f"    - [cyan]{pkg}[/cyan]")

        stdout_console.print(f"  • Already cached packages: {len(cached)}")
        for pkg in cached:
            stdout_console.print(f"    - [dim]{pkg}[/dim]")

        if failed:
            stdout_console.print(f"  • Unresolved packages: {len(failed)}")
            for pkg in failed:
                stdout_console.print(f"    - [yellow]{pkg}[/yellow]")

        stdout_console.print(
            f"  • Total new symbols indexed: [bold]{total_syms}[/bold]"
        )
    except Exception as e:
        stderr_console.print(
            f"[bold red]Error during dependency ingestion: {e}[/bold red]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
