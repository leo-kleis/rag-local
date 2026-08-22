import contextlib
import time
from typing import Any

import lancedb

from rag_local.core import config
from rag_local.core.exceptions import RagLocalError
from rag_local.core.logging import logger
from rag_local.services.db_schemas import DependencySymbol

DEPS_TABLE_NAME = "external_dependencies"


def get_deps_db_connection() -> lancedb.DBConnection:
    """Obtiene la conexión a la base de datos LanceDB global de dependencias."""
    config.GLOBAL_DEPS_LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
    max_retries = getattr(config, "MAX_RETRIES", 5)
    for attempt in range(max_retries):
        try:
            return lancedb.connect(str(config.GLOBAL_DEPS_LANCEDB_PATH))
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            sleep_sec = 0.3 * (2**attempt)
            logger.warning(
                f"Intento {attempt + 1}/{max_retries} al conectar con LanceDB "
                f"de dependencias (esperando {sleep_sec:.2f}s): {e}"
            )
            time.sleep(sleep_sec)
    return lancedb.connect(str(config.GLOBAL_DEPS_LANCEDB_PATH))


def get_deps_table() -> Any:
    """Abre o crea la tabla external_dependencies con índices escalares y FTS."""
    try:
        db = get_deps_db_connection()
        try:
            table = db.create_table(
                DEPS_TABLE_NAME, schema=DependencySymbol, exist_ok=True
            )
        except Exception as ex:
            if "schema" in str(ex).lower():
                logger.warning(
                    f"Esquema desactualizado en {DEPS_TABLE_NAME}, recreando: {ex}"
                )
                with contextlib.suppress(Exception):
                    db.drop_table(DEPS_TABLE_NAME)
                table = db.create_table(DEPS_TABLE_NAME, schema=DependencySymbol)
            else:
                raise

        # Habilitar índices escalares si no existen
        try:
            indices = table.list_indices()
            indexed_columns = {idx.columns[0] for idx in indices if idx.columns}
        except Exception:
            indexed_columns = set()

        if "package_name" not in indexed_columns:
            with contextlib.suppress(Exception):
                table.create_scalar_index("package_name", index_type="BTREE")

        if "language" not in indexed_columns:
            with contextlib.suppress(Exception):
                table.create_scalar_index("language", index_type="BTREE")

        if "id" not in indexed_columns:
            with contextlib.suppress(Exception):
                table.create_scalar_index("id", index_type="BTREE")

        return table
    except Exception as e:
        logger.exception("Error al conectar con la base de datos de dependencias")
        raise RagLocalError(
            f"Error al conectar con LanceDB de dependencias: {e}"
        ) from e


def compact_deps_db() -> None:
    """Compacta la base de datos de dependencias global."""
    try:
        db = get_deps_db_connection()
        tables_resp = db.list_tables()
        table_names = (
            tables_resp
            if isinstance(tables_resp, list)
            else list(getattr(tables_resp, "tables", db.table_names()))
        )
        if DEPS_TABLE_NAME in table_names:
            table = db.open_table(DEPS_TABLE_NAME)
            table.compact_files()
            table.cleanup_old_versions()
    except Exception as e:
        logger.warning(f"No se pudo compactar la BD de dependencias: {e}")
