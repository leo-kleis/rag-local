import argparse
import contextlib
import sys

# Forzar UTF-8 en los flujos estándar para evitar problemas en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console

from rag_local.services.dependencies.cleaner import (
    clean_all_dependencies,
    remove_dependency,
)
from rag_local.services.dependencies.db import get_deps_table
from rag_local.services.dependencies.detector import detect_project_dependencies
from rag_local.services.dependencies.query import (
    format_dependency_result,
    query_dependency_symbols,
)
from rag_local.services.freshness import setup_and_validate_repo

stdout_console = Console()
stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea argumentos con subcomandos para la CLI de dependencias."""
    parser = argparse.ArgumentParser(
        description="Administra y consulta la caché global de dependencias."
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # Subcomando query
    query_parser = subparsers.add_parser(
        "query", help="Consulta contratos y firmas de dependencias."
    )
    query_parser.add_argument(
        "-p",
        "--project-path",
        type=str,
        required=True,
        help="Ruta al proyecto para contexto de entorno.",
    )
    query_parser.add_argument(
        "-P",
        "--package",
        type=str,
        required=True,
        help="Nombre del paquete a consultar (ej. 'twitchio', 'preact').",
    )
    query_parser.add_argument(
        "-S",
        "--symbol",
        type=str,
        default=None,
        help="Nombre exacto del símbolo (ej. 'ChannelFollow').",
    )
    query_parser.add_argument(
        "-q",
        "--query",
        type=str,
        default=None,
        help="Consulta semántica o por palabras clave (ej. 'oauth2 password bearer').",
    )
    query_parser.add_argument(
        "--lang",
        type=str,
        choices=["python", "typescript", "node", "javascript", "js", "ts", "py"],
        default=None,
        help="Filtro opcional por lenguaje.",
    )
    query_parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=5,
        help="Número máximo de símbolos a retornar (por defecto 5).",
    )
    query_parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Salida en formato JSON para consumo de agentes.",
    )

    # Subcomando status
    status_parser = subparsers.add_parser(
        "status", help="Muestra el estado de dependencias del proyecto vs caché global."
    )
    status_parser.add_argument(
        "-p",
        "--project-path",
        type=str,
        required=True,
        help="Ruta al proyecto para analizar dependencias.",
    )

    # Subcomando remove
    remove_parser = subparsers.add_parser(
        "remove", help="Elimina un paquete o versión específica de la caché global."
    )
    remove_parser.add_argument(
        "-P",
        "--package",
        type=str,
        required=True,
        help="Nombre del paquete a eliminar.",
    )
    remove_parser.add_argument(
        "-V",
        "--version",
        type=str,
        default=None,
        help="Versión específica a eliminar.",
    )
    remove_parser.add_argument(
        "--lang",
        type=str,
        choices=["python", "typescript", "node", "javascript", "js", "ts", "py"],
        default=None,
        help="Lenguaje del paquete.",
    )

    # Subcomando clean
    clean_parser = subparsers.add_parser(
        "clean", help="Limpia o purga la caché global de dependencias."
    )
    clean_parser.add_argument(
        "--all",
        action="store_true",
        required=True,
        help="Purga toda la base de datos de dependencias global.",
    )

    return parser.parse_args()


def main() -> None:
    """Punto de entrada principal para rag-deps."""
    try:
        args = parse_arguments()

        if args.subcommand == "query":
            setup_and_validate_repo(args.project_path, console=stderr_console)
            with contextlib.redirect_stdout(sys.stderr):
                raw_res = query_dependency_symbols(
                    package_name=args.package,
                    symbol_name=args.symbol,
                    query_text=args.query,
                    language=args.lang,
                    limit=args.limit,
                )
            if args.json:
                clean_symbols = []
                for s in raw_res.get("symbols", []):
                    s_copy = dict(s)
                    s_copy.pop("vector", None)
                    clean_symbols.append(s_copy)
                stdout_console.print_json(data=clean_symbols)
            else:
                stdout_console.print(format_dependency_result(raw_res))

        elif args.subcommand == "status":
            repo_path = setup_and_validate_repo(
                args.project_path, console=stderr_console
            )
            detected = detect_project_dependencies(repo_path)
            try:
                table = get_deps_table()
                existing_rows = (
                    table.search()
                    .select(["package_name", "package_version", "language"])
                    .to_list()
                )
                existing_set = {
                    (
                        r.get("language", ""),
                        str(r.get("package_name", "")).lower().replace("_", "-"),
                        r.get("package_version", ""),
                    )
                    for r in existing_rows
                }
            except Exception:
                existing_set = set()

            stdout_console.print(f"[Dependency Status: {repo_path}]")
            for lang, pkgs in detected.items():
                hdr = f"\n[bold cyan]{lang.capitalize()} ({len(pkgs)}):[/bold cyan]"
                stdout_console.print(hdr)
                if not pkgs:
                    stdout_console.print("  (none detected)")
                    continue
                for pkg, ver in pkgs.items():
                    cache_key = (lang, pkg.lower().replace("_", "-"), ver)
                    is_cached = cache_key in existing_set
                    status_tag = (
                        "[green]\\[cached][/green]"
                        if is_cached
                        else "[yellow]\\[pending][/yellow]"
                    )
                    stdout_console.print(f"  • {pkg} ({ver}) {status_tag}")

        elif args.subcommand == "remove":
            removed = remove_dependency(
                package_name=args.package,
                version=args.version,
                language=args.lang,
            )
            if removed:
                stdout_console.print(
                    f"[green]Dependency '{args.package}' removed.[/green]"
                )
            else:
                stdout_console.print(
                    f"[red]Dependency '{args.package}' not found.[/red]"
                )

        elif args.subcommand == "clean" and args.all:
            clean_all_dependencies()
            stdout_console.print(
                "[green]Global dependencies cache cleared successfully.[/green]"
            )
    except KeyboardInterrupt:
        stderr_console.print(
            "\n[bold red]Operación cancelada por el usuario.[/bold red]"
        )
        sys.exit(1)
    except Exception as e:
        stderr_console.print(f"\n[bold red][ERROR RAG-DEPS][/bold red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
