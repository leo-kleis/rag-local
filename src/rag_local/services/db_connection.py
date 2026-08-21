import time
from typing import Any

import lancedb

from rag_local.core import config
from rag_local.core.logging import logger


def get_table_names(db: lancedb.DBConnection) -> list[str]:
    """Obtiene la lista de nombres de tablas de forma robusta."""
    tables_resp = db.list_tables()
    if isinstance(tables_resp, list):
        return tables_resp
    if hasattr(tables_resp, "tables"):
        return list(tables_resp.tables)
    return list(db.table_names())


def get_db_connection() -> lancedb.DBConnection:
    """Obtiene la conexión a LanceDB con reintentos para mitigar bloqueos."""
    config.LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
    max_retries = getattr(config, "MAX_RETRIES", 5)
    for attempt in range(max_retries):
        try:
            return lancedb.connect(str(config.LANCEDB_PATH))
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            sleep_sec = 0.3 * (2**attempt)
            logger.warning(
                f"Intento {attempt + 1}/{max_retries} al conectar con LanceDB ("
                f"esperando {sleep_sec:.2f}s por posible bloqueo): {e}"
            )
            time.sleep(sleep_sec)
    return lancedb.connect(str(config.LANCEDB_PATH))


def get_indexed_metadata(
    select_columns: list[str],
    table_name: str = "monorepo_code",
    limit: int = 10000,
) -> list[dict[str, Any]]:
    """Obtiene metadatos proyectados desde la tabla especificada en LanceDB."""
    try:
        db = get_db_connection()
        table_names = get_table_names(db)
        if table_name not in table_names:
            return []
        table = db.open_table(table_name)
        return table.search().select(select_columns).limit(limit).to_list()
    except Exception as e:
        logger.warning(f"Error al leer metadatos de LanceDB ({table_name}): {e}")
        return []


def compact_db() -> None:
    """Compacta las tablas de LanceDB y elimina versiones antiguas."""
    try:
        db = get_db_connection()
        for table_name in get_table_names(db):
            table = db.open_table(table_name)
            try:
                table.optimize()
            except Exception as e:
                logger.warning(f"No se pudo optimizar la tabla {table_name}: {e}")
        logger.info("Base de datos LanceDB compactada correctamente.")
    except Exception as e:
        logger.error(f"Error al compactar LanceDB: {e}")
