from pathlib import Path

from rag_local.core import config
from rag_local.core.logging import logger


def get_relative_path(path: Path) -> str:
    """Retorna la ruta relativa al repositorio con barras inclinadas."""
    try:
        rel_path = path.relative_to(config.REPO_ROOT)
        return str(rel_path).replace("\\", "/")
    except ValueError:
        return str(path)


def scan_files() -> list[Path]:
    """Escanea recursivamente carpetas buscando archivos de código."""
    files_to_process: list[Path] = []
    for dir_name in config.SCAN_DIRS:
        target_dir = config.REPO_ROOT / dir_name
        if not target_dir.exists() or not target_dir.is_dir():
            logger.warning(
                f"Advertencia: El directorio a escanear '{target_dir}' no existe."
            )
            continue

        for path in target_dir.rglob("*"):
            if path.is_file() and path.suffix in config.ALLOWED_EXTENSIONS:
                parts = path.relative_to(config.REPO_ROOT).parts
                if not any(ignored in parts for ignored in config.IGNORE_DIRS):
                    files_to_process.append(path)
    return files_to_process
