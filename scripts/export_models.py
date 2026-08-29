"""Script de pre-descarga de modelos ONNX FP16 desde onnx-community.

Descarga los archivos model_fp16.onnx y tokenizer.json de los repos
onnx-community en HuggingFace Hub a la caché local. Una vez ejecutado,
el daemon arranca sin conexion a internet.

Uso:
    mise run export:models
    uv run python scripts/export_models.py
"""

from huggingface_hub import hf_hub_download
from rich.console import Console
from rich.table import Table

from rag_local.core import config

console = Console()

ONNX_FP16 = "onnx/model_fp16.onnx"
TOKENIZER = "tokenizer.json"

DOWNLOADS: list[tuple[str, str]] = [
    (config.ONNX_EMBEDDING_MODEL, TOKENIZER),
    (config.ONNX_EMBEDDING_MODEL, ONNX_FP16),
    (config.ONNX_RERANKER_MODEL, TOKENIZER),
    (config.ONNX_RERANKER_MODEL, ONNX_FP16),
]


def main() -> None:
    console.print(
        "\n[bold cyan]Pre-descarga de modelos ONNX FP16"
        " desde onnx-community[/bold cyan]\n"
    )

    results: list[tuple[str, str, str]] = []

    for repo_id, filename in DOWNLOADS:
        label = f"{repo_id}/{filename}"
        console.print(f"  Descargando [dim]{label}[/dim] ...", end=" ")
        try:
            local_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
            )
            console.print("[green]OK[/green]")
            results.append((repo_id, filename, local_path))
        except Exception as e:
            console.print(f"[red]ERROR[/red]: {e}")
            raise

    table = Table(title="\nArchivos ONNX descargados")
    table.add_column("Repo", style="bold")
    table.add_column("Archivo", style="dim")
    table.add_column("Ruta Local", style="cyan", no_wrap=False)

    for repo_id, filename, local_path in results:
        table.add_row(repo_id, filename, local_path)

    console.print(table)
    console.print(
        "\n[bold green]Listo. El daemon puede arrancar"
        " sin conexion a internet.[/bold green]\n"
    )


if __name__ == "__main__":
    main()
