from pathlib import Path

from rag_local.core import config
from rag_local.services.meta import (
    check_schema_status,
    get_meta_file_path,
    load_index_meta,
    save_index_meta,
)


def test_meta_lifecycle(tmp_path: Path):
    lancedb_path = tmp_path / ".lancedb"

    # 1. Base de datos no existe
    is_up_to_date, reason, meta = check_schema_status(lancedb_path)
    assert not is_up_to_date
    assert "no existe" in reason

    # 2. Crear directorio pero sin meta.json (Legacy)
    lancedb_path.mkdir(parents=True, exist_ok=True)
    dummy_file = lancedb_path / "dummy.lance"
    dummy_file.write_text("test")

    is_up_to_date, reason, meta = check_schema_status(lancedb_path)
    assert not is_up_to_date
    assert "Legacy" in reason

    # 3. Guardar metadatos actuales v1.0.0
    save_index_meta(lancedb_path, total_chunks=42)
    assert get_meta_file_path(lancedb_path).is_file()

    loaded = load_index_meta(lancedb_path)
    assert loaded["schema_version"] == config.SCHEMA_VERSION
    assert loaded["embedding_model"] == config.LOCAL_EMBEDDING_MODEL
    assert loaded["total_chunks"] == 42

    # 4. Verificar que está actualizada
    is_up_to_date, reason, meta = check_schema_status(lancedb_path)
    assert is_up_to_date
    assert "actualizado" in reason.lower()

    # 5. Simular versión obsoleta
    save_index_meta(lancedb_path, schema_version="0.9.0", total_chunks=42)
    is_up_to_date, reason, meta = check_schema_status(lancedb_path)
    assert not is_up_to_date
    assert "0.9.0" in reason
