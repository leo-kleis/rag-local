from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.services.cache import get_file_hash, load_cache
from rag_local.services.meta import check_schema_status
from rag_local.services.scanner import get_relative_path, scan_files


def fast_check_and_refresh(repo_path: Path | None = None) -> dict[str, Any]:
    """Verifica en microsegundos si existen archivos modificados o desactualizados.

    Si detecta cambios o esquema desactualizado, ejecuta un refresco transparente.

    Returns:
        Dict con 'updated' (bool), 'reason' (str) y 'changed_count' (int).
    """
    target_repo = (repo_path or config.REPO_ROOT).resolve()
    lancedb_path = target_repo / ".lancedb"

    config.REPO_ROOT = target_repo
    config.LANCEDB_PATH = lancedb_path

    if not lancedb_path.exists() or not any(lancedb_path.iterdir()):
        return {"updated": False, "reason": "no_index", "changed_count": 0}

    # 1. Verificar compatibilidad de SCHEMA_VERSION
    is_up_to_date, schema_reason, _ = check_schema_status(lancedb_path)
    if not is_up_to_date:
        from rich.console import Console

        from rag_local.core.events import SyncPhase, emit_sync_event

        emit_sync_event(
            phase=SyncPhase.START,
            progress=5,
            message="Re-ingesta forzada por cambio de versión de esquema...",
            reason=f"schema_update: {schema_reason}",
        )

        stderr_console = Console(stderr=True)
        stderr_console.print(
            f"[AUTO-SYNC] Cambio de esquema detectado ({schema_reason}). "
            "Iniciando re-ingesta completa..."
        )
        logger.info(
            f"[FAST-SYNC] Desactualización de esquema detectada ({schema_reason}). "
            "Ejecutando re-ingesta forzada..."
        )
        try:
            from rag_local.cli.ingest import run_ingestion

            run_ingestion(exit_on_complete=False, force=True)
            return {
                "updated": True,
                "reason": f"forced_schema_update: {schema_reason}",
                "changed_count": -1,
            }
        except (Exception, SystemExit) as e:
            emit_sync_event(
                phase=SyncPhase.ERROR,
                message=f"Error en re-ingesta por esquema: {e}",
                reason=str(e),
            )
            logger.warning(
                f"[FAST-SYNC] Error o salida en re-ingesta forzada por esquema: {e}"
            )
            return {"updated": False, "reason": f"error: {e}", "changed_count": -1}

    # 2. Escanear cambios en archivos en disco
    current_files = scan_files()
    cache: dict[str, Any] = load_cache()

    current_rel_map = {get_relative_path(f): f for f in current_files}
    current_rel_set = set(current_rel_map.keys())
    cached_rel_set = set(cache.keys())

    changed_count = 0

    # 2.1 Archivos eliminados
    deleted_files = cached_rel_set - current_rel_set
    changed_count += len(deleted_files)

    # 2.2 Archivos nuevos o modificados
    for rel_path, file_path in current_rel_map.items():
        cached_val = cache.get(rel_path)
        if cached_val is None:
            changed_count += 1
            continue

        try:
            if isinstance(cached_val, dict):
                current_mtime = file_path.stat().st_mtime
                if current_mtime != cached_val.get("mtime"):
                    changed_count += 1
            elif isinstance(cached_val, str):
                current_hash = get_file_hash(file_path)
                if current_hash != cached_val:
                    changed_count += 1
            else:
                changed_count += 1
        except Exception:
            changed_count += 1

    if changed_count == 0:
        return {"updated": False, "reason": "clean", "changed_count": 0}

    from rich.console import Console

    from rag_local.core.events import SyncPhase, emit_sync_event

    emit_sync_event(
        phase=SyncPhase.START,
        progress=10,
        changed_count=changed_count,
        message=f"Detectados {changed_count} archivos con cambios. Sincronizando...",
        reason="delta_changes",
    )

    stderr_console = Console(stderr=True)
    stderr_console.print(
        f"[AUTO-SYNC] Detectados {changed_count} archivos con cambios. "
        "Sincronizando en LanceDB..."
    )
    logger.info(
        f"[FAST-SYNC] Detectados {changed_count} cambios en disco. "
        "Sincronizando deltas en LanceDB..."
    )

    try:
        from rag_local.cli.ingest import run_ingestion

        run_ingestion(exit_on_complete=False, force=False)
        return {
            "updated": True,
            "reason": "sync_completed",
            "changed_count": changed_count,
        }
    except (Exception, SystemExit) as e:
        emit_sync_event(
            phase=SyncPhase.ERROR,
            message=f"Error en sincronización incremental: {e}",
            reason=str(e),
        )
        logger.warning(f"[FAST-SYNC] Error o salida en sincronización incremental: {e}")
        return {
            "updated": False,
            "reason": f"error: {e}",
            "changed_count": changed_count,
        }
