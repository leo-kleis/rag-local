import argparse
import sys

# Forzar UTF-8 en los flujos estándar para evitar problemas en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console

from rag_local.core import config
from rag_local.services.graph import generate_html_graph

stdout_console = Console()
stderr_console = Console(stderr=True)


def parse_arguments() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Generate a structural project 3D/2D and Mermaid graph."
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
    from pathlib import Path

    repo_path = Path(args.project_path).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        stderr_console.print(
            "[bold red]Error: La ruta especificada no existe o "
            f"no es un directorio: {repo_path}[/bold red]"
        )
        sys.exit(1)

    config.REPO_ROOT = repo_path
    config.LANCEDB_PATH = repo_path / ".lancedb"

    output_path = config.LANCEDB_PATH / "project_graph.html"
    stderr_console.print(
        "Generando visualizaciones del grafo (3D, 2D y Mermaid independientes)..."
    )
    try:
        generate_html_graph(config.LANCEDB_PATH, output_path)

        dir_path = config.LANCEDB_PATH
        file_3d = dir_path / "project_graph_3d.html"
        file_2d = dir_path / "project_graph_2d.html"
        file_mermaid = dir_path / "project_graph_mermaid.html"

        # Mostrar links en stdout
        stdout_console.print(
            "Graph files generated successfully. Open them in your browser:\n\n"
            f"- 3D Graph: file:///{file_3d.resolve().as_posix()}\n"
            f"- 2D Graph: file:///{file_2d.resolve().as_posix()}\n"
            f"- Mermaid:  file:///{file_mermaid.resolve().as_posix()}"
        )
    except Exception as e:
        stderr_console.print(f"[bold red]Error al generar el grafo: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
