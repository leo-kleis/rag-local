import json
from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger

META_FILE_NAME = "meta.json"


def get_meta_file_path(lancedb_path: Path) -> Path:
    """Retorna la ruta al archivo meta.json dentro de .lancedb."""
    return lancedb_path / META_FILE_NAME


def load_index_meta(lancedb_path: Path) -> dict[str, Any]:
    """Carga los metadatos del índice desde .lancedb/meta.json."""
    meta_path = get_meta_file_path(lancedb_path)
    if not meta_path.is_file():
        return {}
    try:
        content = meta_path.read_text(encoding="utf-8")
        return json.loads(content)
    except Exception as e:
        logger.warning(f"Error al leer metadatos de índice en {meta_path}: {e}")
        return {}


def save_index_meta(
    lancedb_path: Path,
    schema_version: str = config.SCHEMA_VERSION,
    embedding_model: str = config.LOCAL_EMBEDDING_MODEL,
    total_chunks: int = 0,
) -> None:
    """Guarda los metadatos del índice actualizados en .lancedb/meta.json."""
    meta_path = get_meta_file_path(lancedb_path)
    data = {
        "schema_version": schema_version,
        "embedding_model": embedding_model,
        "total_chunks": total_chunks,
    }
    try:
        lancedb_path.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Error al guardar metadatos del índice en {meta_path}: {e}")


def check_schema_status(lancedb_path: Path) -> tuple[bool, str, dict[str, Any]]:
    """Verifica si el esquema del índice en disco está actualizado.

    Returns:
        (is_up_to_date, reason, current_meta)
    """
    if not lancedb_path.exists() or not any(lancedb_path.iterdir()):
        return False, "La base de datos no existe o está vacía.", {}

    meta = load_index_meta(lancedb_path)
    if not meta:
        return (
            False,
            "Versión de esquema no encontrada (Legacy / sin versión).",
            meta,
        )

    disk_version = meta.get("schema_version")
    disk_model = meta.get("embedding_model")

    if disk_version != config.SCHEMA_VERSION:
        return (
            False,
            f"Versión de esquema no coincide (Disco: {disk_version} "
            f"-> RAG: {config.SCHEMA_VERSION}).",
            meta,
        )

    if disk_model != config.LOCAL_EMBEDDING_MODEL:
        return (
            False,
            f"Modelo de embeddings no coincide (Disco: {disk_model} "
            f"-> RAG: {config.LOCAL_EMBEDDING_MODEL}).",
            meta,
        )

    return True, "Esquema actualizado.", meta
