import sys
from typing import Any

from rich.console import Console

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.services.db import (
    chunk_file,
    delete_file_chunks,
    get_chroma_collection,
    get_file_hash,
    get_relative_path,
    index_chunks,
    load_cache,
    save_cache,
    scan_files,
)
from rag_local.services.scanner import detect_project_roots, get_file_scope

console = Console(stderr=True)


def run_ingestion() -> None:
    """Ejecuta el proceso CLI completo de escaneo e indexación incremental.

    Usa una barra de progreso interactiva para cada fase.
    """
    console.print(
        "[bold cyan]Iniciando proceso de ingesta del Monorepo "
        "(Estructura Modular)...[/bold cyan]"
    )
    console.print(f"[dim]Raíz del repositorio: {config.REPO_ROOT.resolve()}[/dim]\n")

    angular_root, nest_root = detect_project_roots(config.REPO_ROOT)
    if not angular_root and not nest_root:
        console.print(
            "[bold red]Error: No se detectó un proyecto de Angular (angular.json) "
            "ni de NestJS (nest-cli.json) en el repositorio.[/bold red]"
        )
        sys.exit(1)

    if angular_root:
        try:
            rel_ang = angular_root.relative_to(config.REPO_ROOT)
            console.print(f"[dim]Proyecto Angular detectado en: {rel_ang}[/dim]")
        except ValueError:
            console.print(f"[dim]Proyecto Angular detectado en: {angular_root}[/dim]")

    if nest_root:
        try:
            rel_nest = nest_root.relative_to(config.REPO_ROOT)
            console.print(f"[dim]Proyecto NestJS detectado en: {rel_nest}[/dim]")
        except ValueError:
            console.print(f"[dim]Proyecto NestJS detectado en: {nest_root}[/dim]")
    console.print("")

    # Conectar a LanceDB primero para realizar eliminaciones si es necesario
    try:
        collection = get_chroma_collection()
    except Exception as e:
        logger.error(f"Error de conexión con base de datos: {e}")
        sys.exit(1)

    # 1. Escanear archivos
    console.print("[bold]1. Escaneando archivos...[/bold]")
    files = scan_files()
    console.print(f"   -> Escaneo finalizado. Se encontraron {len(files)} archivos.\n")

    if not files:
        logger.warning("No se encontraron archivos de código válidos para indexar.")
        sys.exit(0)

    # Cargar la caché de hashes
    cache = load_cache()

    # Inicializar estadísticas
    stats = {
        "processed": len(files),
        "new": 0,
        "modified": 0,
        "deleted": 0,
        "unchanged": 0,
        "chunks_indexed": 0,
        "chunks_deleted": 0,
    }

    # Detectar archivos eliminados
    physical_rel_paths = {get_relative_path(f) for f in files}
    deleted_files = [
        path_rel for path_rel in cache if path_rel not in physical_rel_paths
    ]

    if deleted_files:
        stats["deleted"] = len(deleted_files)
        console.print(
            f"[bold]   Eliminando {len(deleted_files)} "
            "archivos obsoletos...[/bold]"
        )
        for file_path_rel in deleted_files:
            # Contar chunks anteriores
            try:
                existing = collection.get(
                    where={"source": file_path_rel}, include=[]
                )
                num_chunks = (
                    len(existing["ids"]) if existing and "ids" in existing else 0
                )
                stats["chunks_deleted"] += num_chunks
            except Exception as e:
                logger.warning(
                    f"No se pudo consultar chunks para {file_path_rel}: {e}"
                )

            # Eliminar chunks de LanceDB
            try:
                delete_file_chunks(collection, file_path_rel)
                # Borrar de la caché
                cache.pop(file_path_rel, None)
            except Exception as e:
                logger.error(f"Error al eliminar chunks de {file_path_rel}: {e}")
        console.print("   -> Eliminación completada.\n")

    # 2. Procesar cada archivo en disco (nuevos, modificados o sin cambios)
    all_chunks: list[dict[str, Any]] = []
    console.print(f"[bold]2. Procesando {len(files)} archivos en disco...[/bold]")

    for file_path in files:
        rel_path = get_relative_path(file_path)
        try:
            scope = get_file_scope(file_path, angular_root, nest_root)
        except ValueError as e:
            logger.error(
                f"Error al determinar el scope para el archivo {file_path}: {e}"
            )
            continue

        try:
            current_hash = get_file_hash(file_path)
        except Exception as e:
            logger.error(f"Error procesando hash del archivo {file_path}: {e}")
            continue

        cached_hash = cache.get(rel_path)
        if cached_hash is not None:
            # Verificar que realmente existan chunks indexados para ese archivo
            existing = collection.get(where={"source": rel_path}, limit=1)
            if not existing or not existing.get("ids"):
                cached_hash = None

        if cached_hash is None:
            # Archivo nuevo
            stats["new"] += 1
            file_chunks = chunk_file(file_path)
            for chunk in file_chunks:
                chunk["source"] = rel_path
                chunk["scope"] = scope
                all_chunks.append(chunk)
            cache[rel_path] = current_hash
        elif cached_hash != current_hash:
            # Archivo modificado
            stats["modified"] += 1

            # Contar chunks obsoletos para estadísticas
            try:
                existing = collection.get(where={"source": rel_path}, include=[])
                num_chunks = (
                    len(existing["ids"]) if existing and "ids" in existing else 0
                )
                stats["chunks_deleted"] += num_chunks
            except Exception as e:
                logger.warning(f"No se pudo consultar chunks para {rel_path}: {e}")

            # Borrar chunks antiguos de LanceDB primero
            try:
                delete_file_chunks(collection, rel_path)
            except Exception as e:
                logger.error(
                    f"Error al eliminar chunks obsoletos para {rel_path}: {e}"
                )
                continue

            # Generar nuevos chunks
            file_chunks = chunk_file(file_path)
            for chunk in file_chunks:
                chunk["source"] = rel_path
                chunk["scope"] = scope
                all_chunks.append(chunk)
            cache[rel_path] = current_hash
        else:
            # Archivo sin cambios
            stats["unchanged"] += 1

    total_chunks = len(all_chunks)
    console.print(
        f"   -> Procesamiento finalizado. Nuevos: {stats['new']}, "
        f"Modificados: {stats['modified']}, Sin cambios: {stats['unchanged']}.\n"
    )

    # 3. Indexar en lotes si hay chunks nuevos o modificados
    if total_chunks > 0:
        from rag_local.core.config import BATCH_SIZE
        total_batches = (total_chunks - 1) // BATCH_SIZE + 1
        console.print(
            f"[bold]3. Indexando {total_chunks} fragmentos "
            f"en {total_batches} lotes...[/bold]"
        )
        success_count = 0

        import threading
        print_lock = threading.Lock()

        def batch_update_callback(
            batch_num: int, total_b: int, batch_size: int, status: str = "start"
        ) -> None:
            with print_lock:
                if status == "start":
                    console.print(
                        f"   [cyan][PROCESANDO][/cyan] Lote {batch_num}/{total_b}: "
                        f"Indexando {batch_size} fragmentos..."
                    )
                elif status == "success":
                    console.print(
                        f"   [green][HECHO][/green] Lote {batch_num}/{total_b}: "
                        "Indexación completada."
                    )

        success_count = index_chunks(collection, all_chunks, batch_update_callback)
        stats["chunks_indexed"] = success_count
        console.print("   -> Indexación en lotes finalizada.\n")
    else:
        console.print(
            "[yellow]No hay fragmentos nuevos o modificados para indexar.[/yellow]\n"
        )

    # Optimizar base de datos (compactación y limpieza de versiones obsoletas)
    if stats["chunks_indexed"] > 0 or stats["chunks_deleted"] > 0:
        try:
            console.print(
                "\n[dim]Optimizando almacenamiento en LanceDB...[/dim]"
            )
            collection.table.optimize()
            collection.table = collection.db.open_table(collection.table_name)
            console.print("[dim]Optimización completada con éxito.[/dim]")
        except Exception as e:
            logger.warning(f"No se pudo optimizar LanceDB durante la ingesta: {e}")

    # Guardar la caché final actualizada en disco
    save_cache(cache)

    # 4. Estadísticas finales
    db_count = collection.count()
    console.print("\n[bold green]¡Ingesta completada exitosamente![/bold green]")
    console.print(
        f"  • Archivos procesados en disco: [bold]{stats['processed']}[/bold]"
    )
    console.print(f"  • Archivos nuevos: [bold]{stats['new']}[/bold]")
    console.print(f"  • Archivos modificados: [bold]{stats['modified']}[/bold]")
    console.print(f"  • Archivos eliminados: [bold]{stats['deleted']}[/bold]")
    console.print(f"  • Archivos sin cambios: [bold]{stats['unchanged']}[/bold]")
    console.print(
        f"  • Chunks indexados con éxito: "
        f"[bold]{stats['chunks_indexed']}/{total_chunks}[/bold]"
    )
    console.print(
        f"  • Chunks eliminados de LanceDB: [bold]{stats['chunks_deleted']}[/bold]"
    )
    console.print(f"  • Total de chunks en LanceDB: [bold]{db_count}[/bold]")
    sys.exit(0)


def main() -> None:
    try:
        run_ingestion()
    except KeyboardInterrupt:
        console.print("\n[bold red]Proceso cancelado por el usuario.[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
