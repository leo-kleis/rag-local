import argparse
import re
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.services.rag import process_query

# Consolas para separar stdout (salida principal) de stderr (información de progreso)
stdout_console = Console()
stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Query the local Monorepo RAG system using LanceDB and Gemini."
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="The question or search query you want to ask about the codebase.",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default=None,
        help=(
            "Filter results by scope: 'frontend' (Angular) "
            "or 'backend' (NestJS/Fastify/Prisma)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format instead of human-readable text.",
    )
    return parser.parse_args()


def run_query_cli() -> None:
    """Ejecuta el flujo completo de consulta RAG desde la consola."""
    args = parse_arguments()
    is_json_mode: bool = args.json

    query = args.query
    if not query or not query.strip():
        logger.error("La consulta no puede estar vacía.")
        sys.exit(1)

    # Filtrar caracteres de control (excepto \t y \n)
    query_clean = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", query)

    if len(query_clean) > config.MAX_QUERY_LENGTH:
        logger.error(
            f"La consulta excede la longitud máxima permitida de "
            f"{config.MAX_QUERY_LENGTH} caracteres."
        )
        sys.exit(1)

    if not is_json_mode:
        stderr_console.print(
            f"[bold cyan]Analizando consulta:[/bold cyan] '{query_clean}'"
        )
        if args.scope:
            stderr_console.print(
                f"[dim]Aplicando filtro de scope: '{args.scope}'[/dim]"
            )
        stderr_console.print("[dim]Consultando LanceDB y generando embeddings...[/dim]")

    try:
        # En modo JSON indicamos que responda en inglés para procesamiento de agentes
        results = process_query(
            query_text=query_clean,
            scope=args.scope,
            respond_in_english=is_json_mode,
        )
    except Exception as e:
        logger.error(f"Fallo al consultar la base de datos: {e}")
        sys.exit(1)

    if is_json_mode:
        # Imprimir JSON estructurado a stdout de manera limpia
        stdout_console.print_json(data=results)
    else:
        # Formato visual premium para consumo humano
        header = (
            "\n[bold yellow]"
            "============================ CONTEXTO RECUPERADO "
            "============================[/bold yellow]"
        )
        stderr_console.print(header)
        for idx, chunk in enumerate(results["retrieved_chunks"]):
            source = chunk["source"]
            lines = f"L{chunk['start_line']}-{chunk['end_line']}"
            stderr_console.print(
                f"  [bold green][{idx + 1}][/bold green] {source} [dim]({lines})[/dim]"
            )

        # Renderizar la respuesta Markdown dentro de un panel estético
        markdown_response = Markdown(results["response"])
        stdout_console.print("\n")
        stdout_console.print(
            Panel(
                markdown_response,
                title="[bold cyan]RESPUESTA RAG (Gemini)[/bold cyan]",
                title_align="left",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        footer = (
            "[bold yellow]"
            "================================================"
            "=============================[/bold yellow]"
        )
        stdout_console.print(footer)


def main() -> None:
    try:
        run_query_cli()
    except KeyboardInterrupt:
        stderr_console.print(
            "\n[bold red]Consulta cancelada por el usuario.[/bold red]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
