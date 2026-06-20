import contextlib
import json
import threading
import time
from typing import TYPE_CHECKING, Any, cast

import lancedb
from lancedb.pydantic import LanceModel, Vector

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
from rag_local.services.gemini import get_embeddings
from rag_local.services.scanner import get_relative_path, scan_files

if TYPE_CHECKING:
    VectorType = Any
else:
    VectorType = Vector(768)


def sanitize_sql_value(val: Any) -> str:
    """Sanitiza un valor para su uso en consultas SQL de LanceDB

    escapando comillas simples.
    """
    return str(val).replace("'", "''")


class CodeChunk(LanceModel):
    id: str
    vector: VectorType
    text: str
    source: str
    scope: str
    start_line: int
    end_line: int
    class_name: str = ""
    method_name: str = ""
    imports: str = ""
    dependencies: str = ""
    tags: str = ""
    title: str = ""
    type: str = ""
    models: str = ""
    directives: str = ""


class CodeRelationship(LanceModel):
    id: str
    source_file: str
    target_symbol: str
    relationship_type: str


class LanceDBCollectionWrapper:
    def __init__(
        self, table: lancedb.table.Table, db: lancedb.DBConnection, table_name: str
    ) -> None:
        self.table = table
        self.db = db
        self.table_name = table_name

    def count(self) -> int:
        self.table = self.db.open_table(self.table_name)
        return self.table.count_rows()

    def _prepare_records(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        records = []
        for idx in range(len(ids)):
            meta = metadatas[idx] if metadatas else {}
            rec = {
                "id": ids[idx],
                "vector": embeddings[idx],
                "text": documents[idx],
                "source": meta.get("source", "") or "",
                "scope": meta.get("scope", "") or "",
                "start_line": int(meta.get("start_line", 0))
                if meta.get("start_line") is not None
                else 0,
                "end_line": int(meta.get("end_line", 0))
                if meta.get("end_line") is not None
                else 0,
                "class_name": meta.get("class_name", "") or "",
                "method_name": meta.get("method_name", "") or "",
                "imports": meta.get("imports", "") or "",
                "dependencies": meta.get("dependencies", "") or "",
                "tags": meta.get("tags", "") or "",
                "title": meta.get("title", "") or "",
                "type": meta.get("type", "") or "",
                "models": meta.get("models", "") or "",
                "directives": meta.get("directives", "") or "",
            }
            records.append(rec)
        return records

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        records = self._prepare_records(ids, embeddings, documents, metadatas)
        self.table.add(records)
        self.table = self.db.open_table(self.table_name)

    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        records = self._prepare_records(ids, embeddings, documents, metadatas)
        (
            self.table.merge_insert("id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(records)
        )
        self.table = self.db.open_table(self.table_name)

    def delete(
        self,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        conditions = []
        if ids:
            ids_str = ", ".join(f"'{sanitize_sql_value(val)}'" for val in ids)
            conditions.append(f"id IN ({ids_str})")
        if where:
            for k, v in where.items():
                conditions.append(f"{k} = '{sanitize_sql_value(v)}'")

        if conditions:
            filter_str = " AND ".join(conditions)
            self.table.delete(filter_str)
            self.table = self.db.open_table(self.table_name)

    def get(
        self,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        self.table = self.db.open_table(self.table_name)

        # Construir condiciones SQL para filtrar en LanceDB nativo
        conditions = []
        if ids:
            ids_str = ", ".join(f"'{sanitize_sql_value(val)}'" for val in ids)
            conditions.append(f"id IN ({ids_str})")
        if where:
            for k, v in where.items():
                conditions.append(f"{k} = '{sanitize_sql_value(v)}'")

        filter_str = " AND ".join(conditions) if conditions else None

        # Usar query builder de LanceDB con filtro SQL nativo
        query_builder = self.table.search()
        if filter_str:
            query_builder = query_builder.where(filter_str)
        if limit is not None:
            query_builder = query_builder.limit(limit)

        arrow_table = query_builder.to_arrow()
        rows = arrow_table.to_pylist()

        res_ids = [r["id"] for r in rows]
        res_docs = [r["text"] for r in rows]

        res_metadatas = []
        for r in rows:
            meta = {
                "source": r["source"],
                "scope": r["scope"],
                "start_line": int(r["start_line"]),
                "end_line": int(r["end_line"]),
                "class_name": r["class_name"],
                "method_name": r["method_name"],
                "imports": r["imports"],
                "dependencies": r["dependencies"],
                "tags": r["tags"],
                "title": r["title"],
                "type": r["type"],
                "models": r["models"],
                "directives": r["directives"],
            }
            res_metadatas.append(meta)

        response = {
            "ids": res_ids,
            "documents": res_docs,
            "metadatas": res_metadatas,
        }

        if include is not None and "embeddings" in include:
            response["embeddings"] = [r["vector"] for r in rows]

        return response

    def query(
        self,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, Any] | None = None,
        query_text: str | None = None,
    ) -> dict[str, Any]:
        self.table = self.db.open_table(self.table_name)
        results_ids = []
        results_docs = []
        results_metadatas = []
        results_distances = []

        for q_emb in query_embeddings:
            search_res = []
            if query_text:
                try:
                    query_builder = (
                        self.table.search(query_type="hybrid")
                        .vector(q_emb)
                        .text(query_text)
                    )
                    if where:
                        conditions = [
                            f"{k} = '{sanitize_sql_value(v)}'" for k, v in where.items()
                        ]
                        query_builder = query_builder.where(" AND ".join(conditions))
                    query_builder = query_builder.limit(n_results)
                    search_res = query_builder.to_list()
                except Exception as e:
                    logger.warning(
                        f"Busqueda hibrida fallo, usando vector-only como fallback: {e}"
                    )
                    query_builder = self.table.search(q_emb)
                    if where:
                        conditions = [
                            f"{k} = '{sanitize_sql_value(v)}'" for k, v in where.items()
                        ]
                        query_builder = query_builder.where(" AND ".join(conditions))
                    query_builder = query_builder.limit(n_results)
                    search_res = query_builder.to_list()
            else:
                query_builder = self.table.search(q_emb)
                if where:
                    conditions = [
                        f"{k} = '{sanitize_sql_value(v)}'" for k, v in where.items()
                    ]
                    query_builder = query_builder.where(" AND ".join(conditions))
                query_builder = query_builder.limit(n_results)
                search_res = query_builder.to_list()

            q_ids = []
            q_docs = []
            q_metadatas = []
            q_distances = []

            for item in search_res:
                q_ids.append(item["id"])
                q_docs.append(item["text"])
                meta = {
                    "source": item.get("source", ""),
                    "scope": item.get("scope", ""),
                    "start_line": int(item.get("start_line", 0)),
                    "end_line": int(item.get("end_line", 0)),
                    "class_name": item.get("class_name", ""),
                    "method_name": item.get("method_name", ""),
                    "imports": item.get("imports", ""),
                    "dependencies": item.get("dependencies", ""),
                    "tags": item.get("tags", ""),
                    "title": item.get("title", ""),
                    "type": item.get("type", ""),
                    "models": item.get("models", ""),
                    "directives": item.get("directives", ""),
                }
                q_metadatas.append(meta)
                # En búsqueda híbrida LanceDB devuelve _relevance_score
                # en lugar de _distance
                score = item.get("_relevance_score", item.get("_distance", 0.0))
                q_distances.append(score)

            results_ids.append(q_ids)
            results_docs.append(q_docs)
            results_metadatas.append(q_metadatas)
            results_distances.append(q_distances)

        return {
            "ids": results_ids,
            "documents": results_docs,
            "metadatas": results_metadatas,
            "distances": results_distances,
        }


def get_table_names(db: lancedb.DBConnection) -> list[str]:
    """Obtiene la lista de nombres de tablas de forma robusta."""
    tables_resp = db.list_tables()
    if isinstance(tables_resp, list):
        return tables_resp
    if hasattr(tables_resp, "tables"):
        return list(tables_resp.tables)
    return list(db.table_names())


def get_chroma_collection() -> Any:
    """Inicializa y retorna la tabla de LanceDB envuelta en LanceDBCollectionWrapper."""
    try:
        config.LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(config.LANCEDB_PATH))
        table_name = "monorepo_code"
        table = db.create_table(table_name, schema=CodeChunk, exist_ok=True)

        # Inicializar tabla de relaciones de grafo
        db.create_table("code_relationships", schema=CodeRelationship, exist_ok=True)

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
            db = lancedb.connect(str(config.LANCEDB_PATH))
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
        db = lancedb.connect(str(config.LANCEDB_PATH))
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
        config.LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(config.LANCEDB_PATH))
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

        embeddings = None
        max_batch_attempts = 5
        for attempt in range(max_batch_attempts):
            try:
                embeddings = get_embeddings(batch_texts)
                if embeddings is not None:
                    break
            except Exception as e:
                if attempt < max_batch_attempts - 1:
                    sleep_time = 10 * (attempt + 1)
                    logger.warning(
                        f"Error al obtener embeddings para el lote "
                        f"{batch_num}/{total_batches}: {e}. "
                        f"Reintentando lote completo en {sleep_time}s..."
                    )
                    time.sleep(sleep_time)
                else:
                    logger.error(
                        f"Fallo definitivo al obtener embeddings para el lote "
                        f"{batch_num}/{total_batches} tras {max_batch_attempts} "
                        f"intentos: {e}."
                    )

        if not embeddings:
            logger.warning(
                f"Fallo al obtener embeddings para el lote {batch_num} en bloque. "
                "Entrando en modo de recuperación (procesando uno a uno)..."
            )
            for chunk in batch:
                try:
                    single_emb = None
                    for single_attempt in range(3):
                        try:
                            single_emb = get_embeddings([chunk.text])
                            if single_emb:
                                break
                        except Exception as single_ex:
                            if single_attempt < 2:
                                sleep_time = 5 * (single_attempt + 1)
                                logger.warning(
                                    f"Error en fragmento "
                                    f"{chunk.source}#L{chunk.start_line}: "
                                    f"{single_ex}. Reintentando en "
                                    f"{sleep_time}s..."
                                )
                                time.sleep(sleep_time)
                            else:
                                raise

                    if not single_emb:
                        raise EmbeddingError(
                            "No se pudo obtener el embedding para el fragmento "
                            f"individual {chunk.source}#L{chunk.start_line}."
                        )

                    rec = _prepare_chunk_record(chunk, single_emb[0])

                    with db_lock:
                        collection.upsert(
                            ids=[rec["id"]],
                            embeddings=cast(Any, [rec["vector"]]),
                            documents=[rec["text"]],
                            metadatas=cast(Any, [rec["metadata"]]),
                        )
                    success_count += 1
                except Exception as ex:
                    logger.error(
                        f"Fallo crítico en recuperación individual para "
                        f"{chunk.source}#L{chunk.start_line}: {ex}. "
                        "Abortando la indexación de los lotes restantes."
                    )
                    raise EmbeddingError(
                        f"Abortado por fallo crítico en indexado del "
                        f"lote {batch_num}: {ex}"
                    ) from ex
        else:
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
                logger.error(
                    f"Error indexando lote {batch_num} en la base de datos: {e}. "
                    "Intentando recuperación uno por uno para este lote..."
                )
                for chunk in batch:
                    try:
                        single_emb = get_embeddings([chunk.text])
                        if not single_emb:
                            raise EmbeddingError(
                                "No se pudo obtener el embedding individual."
                            )
                        rec = _prepare_chunk_record(chunk, single_emb[0])
                        with db_lock:
                            collection.upsert(
                                ids=[rec["id"]],
                                embeddings=cast(Any, [rec["vector"]]),
                                documents=[rec["text"]],
                                metadatas=cast(Any, [rec["metadata"]]),
                            )
                        success_count += 1
                    except Exception as ex:
                        logger.error(
                            f"Fallo crítico en recuperación individual de "
                            f"emergencia para {chunk.source}#L{chunk.start_line}: "
                            f"{ex}. Abortando la indexación de los lotes "
                            "restantes."
                        )
                        raise EmbeddingError(
                            f"Abortado por fallo crítico en indexado del "
                            f"lote {batch_num}: {ex}"
                        ) from ex

        if batch_callback:
            with contextlib.suppress(TypeError):
                batch_callback(batch_num, total_batches, len(batch), "success")

        if batch_idx < total_batches - 1:
            time.sleep(1.0)

    return success_count


# Re-exportamos explícitamente para mantener compatibilidad total con la API existente
__all__ = [
    "chunk_file",
    "delete_file_chunks",
    "get_chroma_collection",
    "get_file_hash",
    "get_relative_path",
    "get_table_names",
    "index_chunks",
    "load_cache",
    "query_db",
    "save_cache",
    "save_file_relationships",
    "scan_files",
]
