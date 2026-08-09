import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from rag_local.core import config
from rag_local.core.logging import logger

_stderr_console = Console(stderr=True)
_AUTO_SYNC_TAG = "[AUTO-SYNC]"


def setup_and_validate_repo(project_path: str | Path) -> Path:
    repo_path = Path(project_path).resolve()
    if not repo_path.exists():
        _stderr_console.print(
            f"[bold red]Error: La ruta especificada no existe: {repo_path}[/bold red]"
        )
        sys.exit(1)
    if not repo_path.is_dir():
        _stderr_console.print(
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
) -> dict[str, Any]:
    from rag_local.services.fast_sync import fast_check_and_refresh

    sync_result = fast_check_and_refresh(repo_path)

    if sync_result.get("updated") and not silent:
        changed = sync_result.get("changed_count", 0)
        reason = str(sync_result.get("reason", "sync_completed"))

        if "schema" in reason:
            _stderr_console.print(
                f"[dim]{_AUTO_SYNC_TAG} Re-ingesta forzada por cambio de esquema.[/dim]"
            )
        elif changed > 0:
            _stderr_console.print(
                f"[dim]{_AUTO_SYNC_TAG} Actualizados "
                f"{changed} archivos modificados en LanceDB[/dim]"
            )
        else:
            _stderr_console.print(f"[dim]{_AUTO_SYNC_TAG} Índice sincronizado.[/dim]")
    elif sync_result.get("reason") == "no_index" and not silent:
        logger.debug("No hay índice existente. Se requiere ingesta inicial.")

    return sync_result
