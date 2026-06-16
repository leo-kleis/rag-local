import hashlib
import json
from pathlib import Path

from rag_local.core import config
from rag_local.core.logging import logger


def get_file_hash(file_path: Path) -> str:
    """Calcula el hash SHA256 de un archivo en formato hexadecimal."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
    except Exception as e:
        logger.error(f"Error al calcular hash para {file_path}: {e}")
        raise e
    return sha256.hexdigest()


def load_cache() -> dict[str, str]:
    """Carga la caché de hashes de archivos desde el archivo persistente."""
    cache_file = config.LANCEDB_PATH / "ingest_cache.json"
    if not cache_file.exists():
        return {}
    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            return {}
    except Exception as e:
        logger.error(f"Error al cargar la caché de ingesta: {e}")
        return {}


def save_cache(cache: dict[str, str]) -> None:
    """Guarda la caché de hashes de archivos en el archivo persistente."""
    try:
        config.LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
        cache_file = config.LANCEDB_PATH / "ingest_cache.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error al guardar la caché de ingesta: {e}")
