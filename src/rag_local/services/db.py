import contextlib
import json
import threading
import time
from typing import Any, cast

import lancedb

from rag_local.core import config
from rag_local.core.exceptions import (
    EmbeddingError,
    IngestError,
    QueryError,
    RagLocalError,
)
from rag_local.core.logging import logger
from rag_local.core.models import Chunk
from rag_local.parsers import chunk_file
from rag_local.services.cache import get_file_hash, load_cache, save_cache
from rag_local.services.db_schemas import CodeChunk, CodeRelationship
from rag_local.services.db_wrapper import LanceDBCollectionWrapper, sanitize_sql_value
from rag_local.services.embeddings import get_embeddings
from rag_local.services.scanner import get_relative_path, scan_files


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
    """Obtiene metadatos proyectados desde la tabla especificada en LanceDB.

    Returns:
        Lista de diccionarios con las columnas solicitadas o vacía si no existe.
    """
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


def delete_file_chunks(collection: Any, file_path_rel: str) -> None:
    """Borra los chunks en LanceDB cuya metadata 'source' sea igual a file_path_rel."""
    try:
        collection.delete(where={"source": file_path_rel})

        # Eliminar también las relaciones de código asociadas al archivo
        try:
            db = get_db_connection()
            if "code_relationships" in get_table_names(db):
                table_rel = db.open_table("code_relationships")
                sanitized = sanitize_sql_value(file_path_rel)
                table_rel.delete(f"source_file = '{sanitized}'")
        except Exception as e:
            logger.warning(
                f"No se pudieron eliminar relaciones para {file_path_rel}: {e}"
            )
    except Exception as e:
        logger.exception(f"Error al eliminar chunks obsoletos para {file_path_rel}")
        raise IngestError(
            f"Error al eliminar chunks obsoletos para {file_path_rel}: {e}"
        ) from e


def save_file_relationships(file_path_rel: str, chunks: list[Chunk]) -> None:
    """Extrae y guarda las relaciones de código de un archivo basado en sus chunks."""
    try:
        db = get_db_connection()
        table_rel = db.open_table("code_relationships")

        records = []
        seen = set()

        for chunk in chunks:
            # Procesar imports
            imports = chunk.metadata.imports or []
            if isinstance(imports, str):
                imports = [i.strip() for i in imports.split(",") if i.strip()]
            for imp in imports:
                rel_key = (file_path_rel, imp, "import")
                if rel_key not in seen:
                    seen.add(rel_key)
                    records.append(
                        {
                            "id": f"{file_path_rel}#{imp}#import",
                            "source_file": file_path_rel,
                            "target_symbol": imp,
                            "relationship_type": "import",
                        }
                    )

            # Procesar dependencies
            deps = chunk.metadata.dependencies or []
            if isinstance(deps, str):
                deps = [d.strip() for d in deps.split(",") if d.strip()]
            for dep in deps:
                rel_key = (file_path_rel, dep, "depends_on")
                if rel_key not in seen:
                    seen.add(rel_key)
                    records.append(
                        {
                            "id": f"{file_path_rel}#{dep}#depends_on",
                            "source_file": file_path_rel,
                            "target_symbol": dep,
                            "relationship_type": "depends_on",
                        }
                    )

        if records:
            # Borrar relaciones previas para este archivo
            sanitized = sanitize_sql_value(file_path_rel)
            table_rel.delete(f"source_file = '{sanitized}'")
            table_rel.add(records)

    except Exception as e:
        logger.warning(
            f"No se pudieron guardar las relaciones de código para {file_path_rel}: {e}"
        )


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


db_lock = threading.Lock()


def _prepare_chunk_record(chunk: Chunk, embedding: list[float]) -> dict[str, Any]:
    """Prepara los datos del fragmento para ser insertados en LanceDB."""
    chunk_id = f"{chunk.source}#L{chunk.start_line}-{chunk.end_line}"
    meta: dict[str, Any] = {
        "source": chunk.source,
        "scope": chunk.scope,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
    }

    chunk_meta = chunk.metadata
    if not getattr(chunk_meta, "lines_code", 0) and chunk.text:
        chunk_meta.lines_code = sum(
            1
            for line in chunk.text.splitlines()
            if line.strip() and not line.strip().startswith(("//", "#", "/*", "*"))
        )

    rich_keys = [
        "class_name",
        "method_name",
        "imports",
        "dependencies",
        "tags",
        "title",
        "type",
        "models",
        "directives",
        "lines_code",
        "css_rules",
    ]

    for key in rich_keys:
        val = getattr(chunk_meta, key, "")
        if val is None:
            val = ""

        if isinstance(val, list):
            val = ",".join(str(item) for item in val)
        elif isinstance(val, dict):
            val = json.dumps(val)
        elif not isinstance(val, (str, int, float, bool)):
            val = str(val)

        meta[key] = val

    return {
        "id": chunk_id,
        "vector": embedding,
        "text": chunk.text,
        "metadata": meta,
    }


def index_chunks(
    collection: Any,
    chunks: list[Chunk],
    batch_callback: Any = None,
) -> int:
    """Indexa una lista de chunks en LanceDB de forma secuencial."""
    total_chunks = len(chunks)
    if total_chunks == 0:
        return 0

    batch_size = config.BATCH_SIZE
    batches = [chunks[i : i + batch_size] for i in range(0, total_chunks, batch_size)]
    total_batches = len(batches)

    success_count = 0

    for batch_idx, batch in enumerate(batches):
        batch_num = batch_idx + 1
        batch_texts = [c.text for c in batch]

        if batch_callback:
            try:
                batch_callback(batch_num, total_batches, len(batch), "start")
            except TypeError:
                batch_callback(batch_num, total_batches, len(batch))

        try:
            embeddings = get_embeddings(batch_texts)
            if not embeddings:
                raise EmbeddingError(
                    "No se pudieron generar los embeddings para el lote."
                )
        except Exception as e:
            logger.exception(
                f"Error al generar embeddings para el lote {batch_num}/{total_batches}"
            )
            raise EmbeddingError(
                f"Fallo al indexar el lote {batch_num}: "
                f"error en generación de embeddings: {e}"
            ) from e

        try:
            ids = []
            documents = []
            metadatas = []
            for chunk, emb_val in zip(batch, embeddings, strict=False):
                rec = _prepare_chunk_record(chunk, emb_val)
                ids.append(rec["id"])
                documents.append(rec["text"])
                metadatas.append(rec["metadata"])

            with db_lock:
                collection.upsert(
                    ids=ids,
                    embeddings=cast(Any, embeddings),
                    documents=documents,
                    metadatas=cast(Any, metadatas),
                )
            success_count += len(batch)
        except Exception as e:
            logger.exception(f"Error indexando lote {batch_num} en la base de datos")
            raise EmbeddingError(
                f"Fallo crítico al insertar el lote {batch_num} en la DB: {e}"
            ) from e

        if batch_callback:
            with contextlib.suppress(TypeError):
                batch_callback(batch_num, total_batches, len(batch), "success")

    return success_count


# Re-exportamos explícitamente para mantener compatibilidad total con la API existente
__all__ = [
    "CodeChunk",
    "CodeRelationship",
    "LanceDBCollectionWrapper",
    "chunk_file",
    "compact_db",
    "delete_file_chunks",
    "get_chroma_collection",
    "get_file_hash",
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
