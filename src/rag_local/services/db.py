import contextlib
from typing import Any, cast

from rag_local.core.exceptions import (
    QueryError,
    RagLocalError,
)
from rag_local.core.logging import logger
from rag_local.parsers import chunk_file
from rag_local.services.cache import get_file_hash, load_cache, save_cache
from rag_local.services.db_connection import (
    compact_db,
    get_db_connection,
    get_indexed_metadata,
    get_table_names,
)
from rag_local.services.db_indexing import (
    _prepare_chunk_record,
    db_lock,
    index_chunks,
)
from rag_local.services.db_relationships import (
    delete_file_chunks,
    save_file_relationships,
)
from rag_local.services.db_schemas import CodeChunk, CodeRelationship
from rag_local.services.db_wrapper import LanceDBCollectionWrapper, sanitize_sql_value
from rag_local.services.embeddings import get_embeddings
from rag_local.services.scanner import get_relative_path, scan_files


def get_chroma_collection() -> Any:
    """Inicializa y retorna la tabla de LanceDB envuelta en LanceDBCollectionWrapper."""
    try:
        db = get_db_connection()
        table_name = "monorepo_code"
        try:
            table = db.create_table(table_name, schema=CodeChunk, exist_ok=True)
        except Exception as ex:
            if "schema" in str(ex).lower():
                logger.warning(
                    f"Esquema de LanceDB desactualizado en {table_name}, "
                    f"recreando tabla: {ex}"
                )
                with contextlib.suppress(Exception):
                    db.drop_table(table_name)
                table = db.create_table(table_name, schema=CodeChunk)
            else:
                raise

        # Inicializar tabla de relaciones de grafo
        try:
            db.create_table(
                "code_relationships", schema=CodeRelationship, exist_ok=True
            )
        except Exception as ex:
            if "schema" in str(ex).lower():
                with contextlib.suppress(Exception):
                    db.drop_table("code_relationships")
                db.create_table("code_relationships", schema=CodeRelationship)

        # Habilitar índice FTS en la columna 'text' si no existe
        try:
            indices = table.list_indices()
            indexed_columns = {idx.columns[0] for idx in indices if idx.columns}
            has_fts = any(
                idx.index_type == "fts" and "text" in idx.columns for idx in indices
            )
        except Exception:
            indexed_columns = set()
            has_fts = False

        if not has_fts:
            try:
                table.create_fts_index("text", replace=True)
            except Exception as e:
                logger.error(f"Error al crear el índice FTS en LanceDB: {e}")

        # Habilitar índices escalares para 'scope' y 'source' si no existen
        if "scope" not in indexed_columns:
            try:
                table.create_scalar_index("scope", index_type="BTREE")
            except Exception as e:
                logger.warning(f"No se pudo crear el índice escalar en 'scope': {e}")

        if "source" not in indexed_columns:
            try:
                table.create_scalar_index("source", index_type="BTREE")
            except Exception as e:
                logger.warning(f"No se pudo crear el índice escalar en 'source': {e}")

        return LanceDBCollectionWrapper(table, db, table_name)
    except Exception as e:
        logger.exception("Error al conectar con LanceDB")
        raise RagLocalError(f"Error al conectar con LanceDB: {e}") from e


def query_db(query_text: str, scope: str | None = None, k: int = 4) -> Any:
    """Genera embeddings para la consulta y busca en LanceDB."""
    if not query_text or not query_text.strip():
        raise ValueError("La consulta no puede estar vacía o contener solo espacios.")
    if k <= 0:
        raise ValueError("El valor de k debe ser mayor que cero.")
    query_vector_list = get_embeddings([query_text])
    if not query_vector_list or len(query_vector_list) == 0:
        raise ValueError(
            "No se pudo generar el embedding para la consulta de búsqueda."
        )
    query_vector = query_vector_list[0]

    collection = get_chroma_collection()

    where_filter = None
    if scope:
        where_filter = {"scope": scope}

    try:
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=k,
            where=cast(Any, where_filter),
            query_text=query_text,
        )
        return results
    except Exception as e:
        logger.exception("Error al consultar la colección de LanceDB")
        raise QueryError(f"Error al consultar la colección de LanceDB: {e}") from e


__all__ = [
    "CodeChunk",
    "CodeRelationship",
    "LanceDBCollectionWrapper",
    "_prepare_chunk_record",
    "chunk_file",
    "compact_db",
    "db_lock",
    "delete_file_chunks",
    "get_chroma_collection",
    "get_db_connection",
    "get_file_hash",
    "get_indexed_metadata",
    "get_relative_path",
    "get_table_names",
    "index_chunks",
    "load_cache",
    "query_db",
    "sanitize_sql_value",
    "save_cache",
    "save_file_relationships",
    "scan_files",
]
