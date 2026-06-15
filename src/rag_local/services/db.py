from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import chromadb

from rag_local.core.config import (
    ALLOWED_EXTENSIONS,
    BATCH_SIZE,
    CHROMA_PATH,
    IGNORE_DIRS,
    MAX_LINES_PER_CHUNK,
    OVERLAP_LINES,
    REPO_ROOT,
    SCAN_DIRS,
)
from rag_local.core.logging import logger
from rag_local.services.gemini import get_embeddings


def get_chroma_collection() -> Any:
    """Inicializa y retorna la colección persistente de ChromaDB."""
    try:
        chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        return chroma_client.get_or_create_collection(name="monorepo_code")
    except Exception as e:
        logger.error(f"Error al conectar con ChromaDB: {e}")
        raise e


def get_relative_path(path: Path) -> str:
    """Retorna la ruta relativa al repositorio con barras inclinadas."""
    try:
        rel_path = path.relative_to(REPO_ROOT)
        return str(rel_path).replace("\\", "/")
    except ValueError:
        return str(path)


def scan_files() -> list[Path]:
    """Escanea recursivamente carpetas buscando archivos de código."""
    files_to_process: list[Path] = []
    for dir_name in SCAN_DIRS:
        target_dir = REPO_ROOT / dir_name
        if not target_dir.exists() or not target_dir.is_dir():
            logger.warning(
                f"Advertencia: El directorio a escanear '{target_dir}' no existe."
            )
            continue

        for path in target_dir.rglob("*"):
            if path.is_file() and path.suffix in ALLOWED_EXTENSIONS:
                parts = path.relative_to(REPO_ROOT).parts
                if not any(ignored in parts for ignored in IGNORE_DIRS):
                    files_to_process.append(path)
    return files_to_process


def chunk_file(file_path: Path) -> list[dict[str, Any]]:
    """Divide un archivo en fragmentos (chunks) de líneas que se solapan."""
    chunks: list[dict[str, Any]] = []
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Error al leer el archivo {file_path}: {e}")
        return []

    total_lines = len(lines)
    if total_lines == 0:
        return []

    if total_lines <= MAX_LINES_PER_CHUNK:
        text = "".join(lines)
        chunks.append({"text": text, "start_line": 1, "end_line": total_lines})
        return chunks

    start = 0
    while start < total_lines:
        end = min(start + MAX_LINES_PER_CHUNK, total_lines)
        chunk_lines = lines[start:end]
        text = "".join(chunk_lines)

        chunks.append({"text": text, "start_line": start + 1, "end_line": end})

        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES
        if start >= total_lines - OVERLAP_LINES:
            break

    return chunks


def query_db(query_text: str, scope: str | None = None, k: int = 4) -> Any:
    """Genera embeddings para la consulta y busca en ChromaDB."""
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
        logger.error(f"Error al consultar la colección de ChromaDB: {e}")
        raise e


def index_chunks(
    collection: Any,
    chunks: list[dict[str, Any]],
    batch_callback: Callable[[int, int, int], None] | None = None,
) -> int:
    """Indexa una lista de chunks en ChromaDB procesándolos por lotes.

    Llama opcionalmente a un callback para reportar el progreso del lote.
    Retorna el número de chunks que se indexaron exitosamente.
    """
    total_chunks = len(chunks)
    success_count = 0

    for i in range(0, total_chunks, BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        batch_texts = [c["text"] for c in batch]

        batch_num = i // BATCH_SIZE + 1
        total_batches = (total_chunks - 1) // BATCH_SIZE + 1

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
                metadatas.append(
                    {
                        "source": chunk["source"],
                        "scope": chunk["scope"],
                        "start_line": chunk["start_line"],
                        "end_line": chunk["end_line"],
                    }
                )

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
