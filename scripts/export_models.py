"""Script de pre-descarga de modelos ONNX desde Hugging Face Hub.

Descarga los modelos ONNX y tokenizers de los repositorios configurados
a la caché local de Hugging Face. Una vez ejecutado, el daemon y los
comandos CLI pueden operar sin conexión a Internet.

Uso:
    mise run export:models
    uv run python scripts/export_models.py
"""

from pathlib import Path

from rich.console import Console
from rich.table import Table

from rag_local.daemon.models import download_required_models

console = Console()


def main() -> None:
    console.print(
        "\n[bold cyan]Pre-descarga y verificación de modelos ONNX "
        "desde Hugging Face[/bold cyan]\n"
    )

    try:
        downloaded = download_required_models(console=console)

        table = Table(title="\nArchivos ONNX y Tokenizers Verificados")
        table.add_column("Componente", style="bold")
        table.add_column("Archivo", style="dim")
        table.add_column("Ruta Local", style="cyan", no_wrap=False)

        for key, local_path in downloaded.items():
            repo_id = key.split(":")[0]
            table.add_row(repo_id, Path(local_path).name, local_path)

        console.print(table)
        console.print(
            "\n[bold green]Listo. Los modelos están en caché local y el daemon "
            "puede operar sin conexión a Internet.[/bold green]\n"
        )
    except Exception as e:
        console.print(f"\n[bold red][ERROR][/bold red] {e}\n")
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
