import json
from typing import TYPE_CHECKING, Any, cast

import lancedb
from lancedb.pydantic import LanceModel, Vector

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.parsers import chunk_file
from rag_local.services.cache import get_file_hash, load_cache, save_cache
from rag_local.services.gemini import get_embeddings
from rag_local.services.scanner import get_relative_path, scan_files

if TYPE_CHECKING:
    VectorType = Any
else:
    VectorType = Vector(768)


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
            ids_str = ", ".join(f"'{val}'" for val in ids)
            conditions.append(f"id IN ({ids_str})")
        if where:
            for k, v in where.items():
                conditions.append(f"{k} = '{v}'")

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
        arrow_table = self.table.to_arrow()
        rows = arrow_table.to_pylist()

        if ids:
            rows = [r for r in rows if r["id"] in ids]
        if where:
            rows = [r for r in rows if all(r.get(k) == v for k, v in where.items())]

        if limit is not None:
            rows = rows[:limit]

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
    ) -> dict[str, Any]:
        self.table = self.db.open_table(self.table_name)
        results_ids = []
        results_docs = []
        results_metadatas = []
        results_distances = []

        for q_emb in query_embeddings:
            query_builder = self.table.search(q_emb)
            if where:
                conditions = []
                for k, v in where.items():
                    conditions.append(f"{k} = '{v}'")
                filter_str = " AND ".join(conditions)
                query_builder = query_builder.where(filter_str)

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
                q_distances.append(item.get("_distance", 0.0))

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


def get_chroma_collection() -> Any:
    """Inicializa y retorna la tabla de LanceDB envuelta en LanceDBCollectionWrapper."""
    try:
        config.LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(config.LANCEDB_PATH))
        table_name = "monorepo_code"
        table = db.create_table(table_name, schema=CodeChunk, exist_ok=True)
        return LanceDBCollectionWrapper(table, db, table_name)
    except Exception as e:
        logger.error(f"Error al conectar con LanceDB: {e}")
        raise e


def delete_file_chunks(collection: Any, file_path_rel: str) -> None:
    """Borra los chunks en LanceDB cuya metadata 'source' sea igual a file_path_rel."""
    try:
        collection.delete(where={"source": file_path_rel})
    except Exception as e:
        logger.error(f"Error al eliminar chunks obsoletos para {file_path_rel}: {e}")
        raise e


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
        )
        return results
    except Exception as e:
        logger.error(f"Error al consultar la colección de LanceDB: {e}")
        raise e


def index_chunks(
    collection: Any,
    chunks: list[dict[str, Any]],
    batch_callback: Any = None,
) -> int:
    """Indexa una lista de chunks en LanceDB procesándolos por lotes."""
    total_chunks = len(chunks)
    success_count = 0

    for i in range(0, total_chunks, config.BATCH_SIZE):
        batch = chunks[i : i + config.BATCH_SIZE]
        batch_texts = [c["text"] for c in batch]

        batch_num = i // config.BATCH_SIZE + 1
        total_batches = (total_chunks - 1) // config.BATCH_SIZE + 1

        if batch_callback:
            batch_callback(batch_num, total_batches, len(batch))

        try:
            embeddings = get_embeddings(batch_texts)
            if not embeddings:
                logger.warning(
                    f"Saltando lote {batch_num} debido a problemas "
                    "con la API de embeddings."
                )
                continue

            ids = []
            documents = []
            metadatas = []

            for chunk in batch:
                chunk_id = (
                    f"{chunk['source']}#L{chunk['start_line']}-{chunk['end_line']}"
                )
                ids.append(chunk_id)
                documents.append(chunk["text"])

                meta = {
                    "source": chunk["source"],
                    "scope": chunk["scope"],
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                }

                chunk_meta = chunk.get("metadata", {})
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
                    val = chunk_meta.get(key, "")
                    if val is None:
                        val = ""

                    if isinstance(val, list):
                        val = ",".join(str(item) for item in val)
                    elif isinstance(val, dict):
                        val = json.dumps(val)
                    elif not isinstance(val, (str, int, float, bool)):
                        val = str(val)

                    meta[key] = val

                metadatas.append(meta)

            collection.upsert(
                ids=ids,
                embeddings=cast(Any, embeddings),
                documents=documents,
                metadatas=cast(Any, metadatas),
            )
            success_count += len(batch)

        except Exception as e:
            logger.error(f"Error indexando el lote que inicia en el índice {i}: {e}")
            logger.info("Continuando con el siguiente lote...")

    return success_count


# Re-exportamos explícitamente para mantener compatibilidad total con la API existente
__all__ = [
    "chunk_file",
    "delete_file_chunks",
    "get_chroma_collection",
    "get_file_hash",
    "get_relative_path",
    "index_chunks",
    "load_cache",
    "query_db",
    "save_cache",
    "scan_files",
]
