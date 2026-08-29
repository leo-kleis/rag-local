import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from rag_local.core import config
from rag_local.core.logging import logger

_stderr_console = Console(stderr=True)
_AUTO_SYNC_TAG = "[AUTO-SYNC]"


def setup_and_validate_repo(
    project_path: str | Path,
    console: Any = None,
) -> Path:
    from rag_local.services.project import resolve_container_project_path

    repo_path = resolve_container_project_path(project_path)
    err_c = console if console is not None else _stderr_console
    if not repo_path.exists():
        err_c.print(
            f"[bold red]Error: La ruta especificada no existe: {repo_path}[/bold red]"
        )
        sys.exit(1)
    if not repo_path.is_dir():
        err_c.print(
            f"[bold red]Error: La ruta especificada no es un "
            f"directorio: {repo_path}[/bold red]"
        )
        sys.exit(1)

    config.REPO_ROOT = repo_path
    config.LANCEDB_PATH = repo_path / ".lancedb"
    return repo_path


def ensure_fresh_index(
    repo_path: Path,
    *,
    silent: bool = False,
    console: Any = None,
) -> dict[str, Any]:
    from rag_local.services.fast_sync import fast_check_and_refresh

    sync_result = fast_check_and_refresh(repo_path)
    err_c = console if console is not None else _stderr_console

    if sync_result.get("updated") and not silent:
        from rag_local.core.events import SyncPhase, emit_sync_event

        changed = sync_result.get("changed_count", 0)
        reason = str(sync_result.get("reason", "sync_completed"))

        if "schema" in reason:
            msg = "Re-ingesta forzada por cambio de esquema."
        elif changed > 0:
            msg = f"Actualizados {changed} archivos modificados en LanceDB."
        else:
            msg = "Índice sincronizado."

        emit_sync_event(
            phase=SyncPhase.COMPLETED,
            progress=100,
            changed_count=max(0, changed),
            message=msg,
            reason=reason,
        )
        err_c.print(f"[dim]{_AUTO_SYNC_TAG} {msg}[/dim]")
    elif sync_result.get("reason") == "no_index" and not silent:
        logger.debug("No hay índice existente. Se requiere ingesta inicial.")

    return sync_result
