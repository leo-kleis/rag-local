import contextlib

from rag_local.core.logging import logger
from rag_local.services.db_wrapper import sanitize_sql_value
from rag_local.services.dependencies.db import (
    DEPS_TABLE_NAME,
    compact_deps_db,
    get_deps_db_connection,
    get_deps_table,
)


def remove_dependency(
    package_name: str,
    version: str | None = None,
    language: str | None = None,
) -> int:
    """Elimina los símbolos de un paquete o versión específica de la caché global."""
    table = get_deps_table()
    sanitized_pkg = sanitize_sql_value(package_name.lower().replace("_", "-"))

    where_clauses = [f"package_name = '{sanitized_pkg}'"]
    if version:
        where_clauses.append(f"package_version = '{sanitize_sql_value(version)}'")
    if language:
        where_clauses.append(f"language = '{sanitize_sql_value(language.lower())}'")

    where_str = " AND ".join(where_clauses)
    try:
        table.delete(where_str)
        compact_deps_db()
        logger.info(f"Dependencia eliminada de la caché global: {where_str}")
        return 1
    except Exception as e:
        logger.error(f"Error al eliminar dependencia {package_name}: {e}")
        return 0


def clean_all_dependencies() -> bool:
    """Purga completamente la tabla de dependencias en la caché global."""
    try:
        db = get_deps_db_connection()
        with contextlib.suppress(Exception):
            db.drop_table(DEPS_TABLE_NAME)
        logger.info("Caché global de dependencias purgada con éxito.")
        return True
    except Exception as e:
        logger.error(f"Error al purgar la caché global de dependencias: {e}")
        return False
