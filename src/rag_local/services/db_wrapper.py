from typing import Any

import lancedb

from rag_local.core.logging import logger


def sanitize_sql_value(val: Any) -> str:
    """Sanitiza un valor para su uso en consultas SQL de LanceDB

    escapando comillas simples.
    """
    return str(val).replace("'", "''")


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
                "lines_code": int(meta.get("lines_code", 0))
                if meta.get("lines_code") is not None
                else 0,
                "css_rules": meta.get("css_rules", "") or "",
                "class_parents": meta.get("class_parents", "") or "",
                "payload_schema": meta.get("payload_schema", "") or "",
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
        try:
            (
                self.table.merge_insert("id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(records)
            )
        except Exception:
            # Fallback para montajes 9p/WSL2 donde merge_insert Tokio executor falla
            self.delete(ids=ids)
            self.table.add(records)
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

        conditions = []
        if ids:
            ids_str = ", ".join(f"'{sanitize_sql_value(val)}'" for val in ids)
            conditions.append(f"id IN ({ids_str})")
        if where:
            for k, v in where.items():
                conditions.append(f"{k} = '{sanitize_sql_value(v)}'")

        filter_str = " AND ".join(conditions) if conditions else None

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
