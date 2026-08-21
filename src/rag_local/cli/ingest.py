import sys
from typing import Any

from rich.console import Console

from rag_local.core import config
from rag_local.core.events import SyncPhase, emit_sync_event
from rag_local.core.logging import logger
from rag_local.core.models import Chunk
from rag_local.services.db import (
    chunk_file,
    compact_db,
    delete_file_chunks,
    get_chroma_collection,
    get_file_hash,
    get_relative_path,
    index_chunks,
    load_cache,
    save_cache,
    save_file_relationships,
    scan_files,
)
from rag_local.services.meta import check_schema_status, save_index_meta
from rag_local.services.scanner import detect_project_roots, get_file_scope

console = Console(stderr=True)


def run_ingestion(
    progress_callback: Any = None,
    exit_on_complete: bool = True,
    force: bool = False,
) -> None:
    """Ejecuta el proceso CLI completo de escaneo e indexación incremental.

    Usa una barra de progreso interactiva para cada fase.
    """
    is_up_to_date, reason, _ = check_schema_status(config.LANCEDB_PATH)
    if not force and not is_up_to_date:
        console.print(
            f"[bold yellow][AUTO-FORCE] {reason} Re-indexando totalmente.[/bold yellow]"
        )
        force = True

    emit_sync_event(
        phase=SyncPhase.START,
        progress=0,
        message="Iniciando proceso de ingesta...",
    )

    if progress_callback:
        progress_callback(0, 100, "Iniciando proceso de ingesta del Monorepo...")
    console.print(
        "[bold cyan]Iniciando proceso de ingesta del Monorepo "
        "(Estructura Modular)...[/bold cyan]"
    )

    if not config.REPO_ROOT.exists() or not config.REPO_ROOT.is_dir():
        console.print(
            f"[bold red]Error: El directorio raíz del repositorio especificado "
            f"no existe o no es válido: {config.REPO_ROOT}[/bold red]"
        )
        sys.exit(1)

    console.print(f"[dim]Raíz del repositorio: {config.REPO_ROOT.resolve()}[/dim]\n")

    angular_root, nest_root, python_root, nextjs_root = detect_project_roots(
        config.REPO_ROOT
    )
    if not angular_root and not nest_root and not python_root and not nextjs_root:
        console.print(
            "[bold red]Error: No se detectó un proyecto de Angular (angular.json), "
            "NestJS (nest-cli.json), Python (pyproject.toml) "
            "ni de Next.js (next.config.ts/js/mjs) en el repositorio.[/bold red]"
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

    if python_root:
        try:
            rel_py = python_root.relative_to(config.REPO_ROOT)
            console.print(f"[dim]Proyecto Python detectado en: {rel_py}[/dim]")
        except ValueError:
            console.print(f"[dim]Proyecto Python detectado en: {python_root}[/dim]")

    if nextjs_root:
        try:
            rel_next = nextjs_root.relative_to(config.REPO_ROOT)
            console.print(f"[dim]Proyecto Next.js detectado en: {rel_next}[/dim]")
        except ValueError:
            console.print(f"[dim]Proyecto Next.js detectado en: {nextjs_root}[/dim]")

    console.print("")

    if force:
        try:
            import contextlib

            from rag_local.services.db_connection import get_db_connection

            db = get_db_connection()
            for t_name in ("monorepo_code", "code_relationships"):
                with contextlib.suppress(Exception):
                    db.drop_table(t_name)
        except Exception as e:
            logger.warning(f"Error recreando tablas durante force ingest: {e}")

    try:
        collection = get_chroma_collection()
    except Exception as e:
        logger.error(f"Error de conexión con base de datos: {e}")
        sys.exit(1)

    console.print("[bold]1. Escaneando archivos...[/bold]")
    emit_sync_event(
        phase=SyncPhase.PROGRESS,
        progress=5,
        message="Escaneando archivos del repositorio...",
    )
    files = scan_files()
    console.print(f"   -> Escaneo finalizado. Se encontraron {len(files)} archivos.\n")
    emit_sync_event(
        phase=SyncPhase.PROGRESS,
        progress=10,
        message=f"Escaneo finalizado: {len(files)} archivos encontrados.",
    )
    if progress_callback:
        progress_callback(
            10, 100, f"Escaneo finalizado. Encontrados {len(files)} archivos."
        )

    if not files:
        logger.warning("No se encontraron archivos de código válidos para indexar.")
        sys.exit(0)

    cache = {} if force else load_cache()

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
            f"[bold]   Eliminando {len(deleted_files)} archivos obsoletos...[/bold]"
        )
        for file_path_rel in deleted_files:
            # Contar chunks anteriores
            try:
                existing = collection.get(where={"source": file_path_rel}, include=[])
                num_chunks = (
                    len(existing["ids"]) if existing and "ids" in existing else 0
                )
                stats["chunks_deleted"] += num_chunks
            except Exception as e:
                logger.warning(f"No se pudo consultar chunks para {file_path_rel}: {e}")

            # Eliminar chunks de LanceDB
            try:
                delete_file_chunks(collection, file_path_rel)
                # Borrar de la caché
                cache.pop(file_path_rel, None)
            except Exception as e:
                logger.error(f"Error al eliminar chunks de {file_path_rel}: {e}")
        console.print("   -> Eliminación completada.\n")

    # 2. Procesar cada archivo en disco (nuevos, modificados o sin cambios)
    all_chunks: list[Chunk] = []
    console.print(f"[bold]2. Procesando {len(files)} archivos en disco...[/bold]")
    total_files = len(files)

    for idx, file_path in enumerate(files, 1):
        rel_path = get_relative_path(file_path)
        if idx == 1 or idx == total_files or idx % 10 == 0:
            prog = 10 + int((idx / max(total_files, 1)) * 20)
            msg = f"Procesando archivo {idx}/{total_files}: {rel_path}"
            emit_sync_event(phase=SyncPhase.PROGRESS, progress=prog, message=msg)
            console.print(f"[AUTO-SYNC] {msg}")
        try:
            scope = get_file_scope(
                file_path, angular_root, nest_root, python_root, nextjs_root
            )
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
                chunk.source = rel_path
                chunk.scope = scope
                all_chunks.append(chunk)
            cache[rel_path] = current_hash
            save_file_relationships(rel_path, file_chunks)
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
                logger.error(f"Error al eliminar chunks obsoletos para {rel_path}: {e}")
                continue

            # Generar nuevos chunks
            file_chunks = chunk_file(file_path)
            for chunk in file_chunks:
                chunk.source = rel_path
                chunk.scope = scope
                all_chunks.append(chunk)
            cache[rel_path] = current_hash
            save_file_relationships(rel_path, file_chunks)
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
        msg = f"Indexando {total_chunks} fragmentos en {total_batches} lotes..."
        console.print(f"[bold]3. {msg}[/bold]")
        if progress_callback:
            progress_callback(30, 100, msg)
        success_count = 0

        import threading

        print_lock = threading.Lock()

        def batch_update_callback(
            batch_num: int, total_b: int, batch_size: int, status: str = "start"
        ) -> None:
            with print_lock:
                if status == "start":
                    msg = (
                        f"Indexando lote {batch_num}/{total_b}: "
                        f"{batch_size} fragmentos..."
                    )
                    prog = 30 + int((batch_num / max(total_b, 1)) * 65)
                    emit_sync_event(
                        phase=SyncPhase.PROGRESS,
                        progress=prog,
                        message=msg,
                    )
                    console.print(f"[AUTO-SYNC] {msg}")
                    console.print(f"   [cyan][PROCESANDO][/cyan] {msg}")
                    if progress_callback:
                        progress_callback(prog, 100, msg)
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

    # Optimizar y compactar base de datos
    # (compactación y limpieza de versiones obsoletas)
    if stats["new"] > 0 or stats["modified"] > 0 or stats["deleted"] > 0:
        try:
            console.print(
                "\n[dim]Optimizando y compactando almacenamiento en LanceDB...[/dim]"
            )
            compact_db()
            console.print(
                "[dim]Optimización y compactación completadas con éxito.[/dim]"
            )
        except Exception as e:
            logger.warning(f"No se pudo optimizar LanceDB durante la ingesta: {e}")

    # Guardar la caché final actualizada en disco
    save_cache(cache)

    # 4. Estadísticas finales
    total_changed = stats["new"] + stats["modified"] + stats["deleted"]
    emit_sync_event(
        phase=SyncPhase.COMPLETED,
        progress=100,
        changed_count=total_changed,
        message="¡Ingesta completada exitosamente!",
        reason="ingest_finished",
    )

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
    # Guardar metadatos del índice v1.0.0
    save_index_meta(config.LANCEDB_PATH, total_chunks=db_count)

    if progress_callback:
        progress_callback(100, 100, "¡Ingesta completada exitosamente!")
    if exit_on_complete:
        sys.exit(0)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingesta e indexación incremental del codebase para RAG local."
    )
    parser.add_argument(
        "-p",
        "--project-path",
        required=True,
        help="Ruta absoluta o relativa al directorio raíz del proyecto a indexar.",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Forzar reindexación completa ignorando la caché de hashes.",
    )

    try:
        args = parser.parse_args()
        target_path_str = args.project_path

        from rag_local.services.freshness import setup_and_validate_repo

        setup_and_validate_repo(target_path_str, console=console)

        run_ingestion(force=args.force)
    except KeyboardInterrupt:
        console.print("\n[bold red]Proceso cancelado por el usuario.[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
