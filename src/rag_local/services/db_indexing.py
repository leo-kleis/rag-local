import contextlib
import json
import threading
from typing import Any, cast

from rag_local.core import config
from rag_local.core.exceptions import EmbeddingError
from rag_local.core.logging import logger
from rag_local.core.models import Chunk
from rag_local.services.embeddings import get_embeddings

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
        "class_parents",
        "payload_schema",
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
        batch_texts = [
            f"[{c.metadata.title}] {c.text}"
            if c.metadata and getattr(c.metadata, "title", None)
            else c.text
            for c in batch
        ]

        if batch_callback:
            try:
                batch_callback(batch_num, total_batches, len(batch), "start")
            except TypeError:
                batch_callback(batch_num, total_batches, len(batch))

        try:
            embeddings = get_embeddings(batch_texts, task="nl2code_document")
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

    # Crear o actualizar índice vectorial IVF_PQ si el volumen lo amerita
    if hasattr(collection, "table"):
        ensure_vector_index(collection.table)

    return success_count


def ensure_vector_index(table: Any, vector_column: str = "vector") -> None:
    """Crea o actualiza el índice vectorial adaptando particiones a la tabla."""
    try:
        import math

        row_count = table.count_rows()
        if row_count < 256:
            return

        if row_count < 10000:
            num_partitions = max(4, math.isqrt(row_count))
        else:
            num_partitions = min(512, max(32, row_count // 256))

        dim = config.EMBEDDING_VECTOR_DIM
        num_sub_vectors = 56 if dim == 896 else max(1, dim // 8)

        table.create_index(
            vector_column_name=vector_column,
            index_type="IVF_PQ",
            metric="cosine",
            num_partitions=num_partitions,
            num_sub_vectors=num_sub_vectors,
            replace=True,
        )
        t_name = getattr(table, "name", "table")
        logger.info(
            f"Índice vectorial IVF_PQ configurado en {t_name} "
            f"(filas: {row_count}, particiones: {num_partitions}, "
            f"sub-vectores: {num_sub_vectors})"
        )
    except Exception as e:
        t_name = getattr(table, "name", "table")
        logger.warning(f"No se pudo crear el índice vectorial en {t_name}: {e}")
