from typing import Any

from rag_local.core.logging import logger
from rag_local.services.db import query_db
from rag_local.services.gemini import generate_content


def xml_escape(text: str) -> str:
    """Escapa los caracteres especiales para evitar inyecciones XML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


_reranker: Any = None


def process_query(
    query_text: str,
    scope: str | None = None,
    respond_in_english: bool = False,
    k: int = 4,
) -> dict[str, Any]:
    """Orquesta el flujo RAG: consulta la BD, opcionalmente re-rankea

    y genera la respuesta.
    """
    import os

    is_mock = os.getenv("RAG_MOCK_API") == "1"

    # 1. Consultar base de datos vectorial
    if is_mock:
        results = query_db(query_text, scope, k=k)
    else:
        results = query_db(query_text, scope, k=15)

    documents = results.get("documents")
    metadatas = results.get("metadatas")
    ids = results.get("ids")

    docs_list = documents[0] if documents else []
    meta_list = metadatas[0] if metadatas else []
    ids_list = ids[0] if ids else []

    # 2. Re-ranking
    if not is_mock and docs_list:
        try:
            import torch
            from rerankers import Reranker

            global _reranker
            if _reranker is None:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                _reranker = Reranker(
                    "cross-encoder/ms-marco-MiniLM-L-6-v2", device=device
                )

            ranked_results = _reranker.rank(query=query_text, docs=docs_list)

            new_docs = []
            new_metas = []
            new_ids = []
            for item in ranked_results:
                orig_idx = int(item.doc_id)
                new_docs.append(docs_list[orig_idx])
                if orig_idx < len(meta_list):
                    new_metas.append(meta_list[orig_idx])
                if orig_idx < len(ids_list):
                    new_ids.append(ids_list[orig_idx])

            docs_list = new_docs[:k]
            meta_list = new_metas[:k]
            ids_list = new_ids[:k]
        except Exception as e:
            logger.error(f"Error al re-rankear resultados, usando fallback: {e}")
            docs_list = docs_list[:k]
            meta_list = meta_list[:k]
            ids_list = ids_list[:k]

    # 2. Agrupar y fusionar chunks por archivo
    chunks_by_file: dict[str, list[dict[str, Any]]] = {}
    for i in range(len(docs_list)):
        doc = docs_list[i]
        meta = meta_list[i] if i < len(meta_list) else {}
        chunk_id = ids_list[i] if i < len(ids_list) else f"chunk_{i}"

        source = meta.get("source", "unknown")
        chunk_scope = meta.get("scope", "unknown")
        start_line = int(meta.get("start_line", 1))
        end_line = int(meta.get("end_line", 1))

        chunks_by_file.setdefault(source, []).append(
            {
                "id": chunk_id,
                "source": source,
                "scope": chunk_scope,
                "start_line": start_line,
                "end_line": end_line,
                "content": doc,
            }
        )

    retrieved_chunks = []
    context_blocks = []

    for source, file_chunks in chunks_by_file.items():
        file_lines = {}
        for chunk in file_chunks:
            # Reconstruir las líneas asociadas a su número
            content_lines = chunk["content"].splitlines()
            start = chunk["start_line"]
            for idx, line in enumerate(content_lines):
                file_lines[start + idx] = line

        if not file_lines:
            continue

        # Encontrar bloques contiguos de líneas
        sorted_lines = sorted(file_lines.keys())
        blocks = []
        current_block = []

        for num in sorted_lines:
            if not current_block or num == current_block[-1] + 1:
                current_block.append(num)
            else:
                blocks.append(current_block)
                current_block = [num]
        if current_block:
            blocks.append(current_block)

        # Crear chunks fusionados y sus bloques XML
        for block in blocks:
            b_start = block[0]
            b_end = block[-1]
            b_content = "\n".join(file_lines[n] for n in block)

            chunk_scope = file_chunks[0]["scope"]

            retrieved_chunks.append(
                {
                    "id": f"{source}#L{b_start}-{b_end}",
                    "source": source,
                    "scope": chunk_scope,
                    "start_line": b_start,
                    "end_line": b_end,
                    "content": b_content,
                }
            )

            escaped_content = xml_escape(b_content)
            xml_block = (
                f'<file path="{source}" start_line="{b_start}" end_line="{b_end}">\n'
                f"{escaped_content}\n"
                f"</file>"
            )
            context_blocks.append(xml_block)

    # 3. Preparar contexto e instrucciones de Gemini
    if not retrieved_chunks:
        return {
            "query": query_text,
            "scope": scope,
            "retrieved_chunks": [],
            "response": (
                "No se encontraron fragmentos de código fuente "
                "relevantes en el contexto."
            ),
        }

    context_str = "\n".join(context_blocks)

    # Límite máximo de caracteres de contexto
    max_context_chars = 15000
    is_truncated = False
    if len(context_str) > max_context_chars:
        context_str = context_str[:max_context_chars]
        is_truncated = True

    if is_truncated:
        context_str_wrapped = f"<context>\n{context_str}\n[TRUNCATED]\n</context>"
    else:
        context_str_wrapped = f"<context>\n{context_str}\n</context>"

    target_language = "ENGLISH" if respond_in_english else "SPANISH"

    system_instruction = (
        "You are a Senior AI Engineer expert in software development, "
        "Angular 21, NestJS 11, Fastify, and Prisma.\n"
        "Your task is to answer the user's question based strictly on the provided "
        "source code context.\n"
        "Follow these strict rules:\n"
        f"1. ALWAYS RESPOND IN {target_language} to the user.\n"
        "2. Be clear, educational, and direct.\n"
        "3. Use code blocks when necessary to illustrate or explain the solution.\n"
        "4. If the answer cannot be deduced from the provided code, state it clearly.\n"
        "5. You MUST wrap your entire response inside a <response> tag."
    )

    prompt = (
        f"CONTEXTO DE CÓDIGO FUENTE RECUPERADO:\n"
        f"{context_str_wrapped}\n\n"
        f"PREGUNTA DEL USUARIO:\n"
        f"{xml_escape(query_text)}\n\n"
        f"RESPUESTA:"
    )

    # 4. Generar respuesta
    answer = generate_content(prompt, system_instruction)

    return {
        "query": query_text,
        "scope": scope,
        "retrieved_chunks": retrieved_chunks,
        "response": answer,
    }
