import sys
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from rag_local.core.config import REPO_ROOT
from rag_local.core.logging import logger
from rag_local.services.db import (
    chunk_file,
    get_chroma_collection,
    get_relative_path,
    index_chunks,
    scan_files,
)

console = Console(stderr=True)


def run_ingestion() -> None:
    """Ejecuta el proceso CLI completo de escaneo e indexación.

    Usa una barra de progreso interactiva para cada fase.
    """
    console.print(
        "[bold cyan]Iniciando proceso de ingesta del Monorepo "
        "(Estructura Modular)...[/bold cyan]"
    )
    console.print(f"[dim]Raíz del repositorio: {REPO_ROOT.resolve()}[/dim]\n")

    # 1. Escanear archivos
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(description="Escaneando archivos...", total=None)
        files = scan_files()
        progress.update(
            task,
            description=f"Escaneo finalizado. Se encontraron {len(files)} archivos.",
            completed=True,
        )

    if not files:
        logger.warning("No se encontraron archivos de código válidos para indexar.")
        sys.exit(0)

    # 2. Dividir archivos en chunks
    all_chunks: list[dict[str, Any]] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            description="Dividiendo archivos en fragmentos (chunks)...",
            total=len(files),
        )
        for file_path in files:
            rel_path = get_relative_path(file_path)
            scope = "frontend" if "frontend" in rel_path.split("/") else "backend"

            file_chunks = chunk_file(file_path)
            for chunk in file_chunks:
                chunk["source"] = rel_path
                chunk["scope"] = scope
                all_chunks.append(chunk)

            progress.advance(task)

    total_chunks = len(all_chunks)
    console.print(
        f"[bold green]Se generaron {total_chunks} fragmentos a procesar.[/bold green]\n"
    )

    if total_chunks == 0:
        logger.info("No hay contenido para indexar.")
        sys.exit(0)

    # 3. Conectar a ChromaDB
    try:
        collection = get_chroma_collection()
    except Exception as e:
        logger.error(f"Error de conexión con base de datos: {e}")
        sys.exit(1)

    # 4. Indexar en lotes (con barra de progreso)
    success_count = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        # Calculamos la cantidad aproximada de lotes (batches)
        from rag_local.core.config import BATCH_SIZE

        total_batches = (total_chunks - 1) // BATCH_SIZE + 1
        task = progress.add_task(
            description="Indexando lotes en ChromaDB...",
            total=total_batches,
        )

        def batch_update_callback(
            batch_num: int, total_b: int, batch_size: int
        ) -> None:
            desc = (
                f"Indexando lote {batch_num}/{total_b} "
                f"({batch_size} chunks)..."
            )
            progress.update(task, description=desc)

        success_count = index_chunks(collection, all_chunks, batch_update_callback)
        progress.update(
            task,
            description="Indexación en lotes finalizada.",
            completed=True,
        )

    # 5. Estadísticas finales
    db_count = collection.count()
    console.print("\n[bold green]¡Ingesta completada exitosamente![/bold green]")
    console.print(
        f"  • Chunks indexados con éxito: [bold]{success_count}/{total_chunks}[/bold]"
    )
    console.print(f"  • Total de chunks en ChromaDB: [bold]{db_count}[/bold]")


def main() -> None:
    try:
        run_ingestion()
    except KeyboardInterrupt:
        console.print("\n[bold red]Proceso cancelado por el usuario.[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
