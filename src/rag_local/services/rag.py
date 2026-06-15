from typing import Any

from rag_local.services.db import query_db
from rag_local.services.gemini import generate_content


def process_query(
    query_text: str,
    scope: str | None = None,
    respond_in_english: bool = False,
    k: int = 4,
) -> dict[str, Any]:
    """Orquesta el flujo RAG: consulta la BD y genera la respuesta."""
    # 1. Consultar base de datos vectorial
    results = query_db(query_text, scope, k=k)

    documents = results.get("documents")
    metadatas = results.get("metadatas")
    ids = results.get("ids")

    docs_list = documents[0] if documents else []
    meta_list = metadatas[0] if metadatas else []
    ids_list = ids[0] if ids else []

    # 2. Formatear chunks recuperados
    retrieved_chunks = []
    context_blocks = []

    for i in range(len(docs_list)):
        doc = docs_list[i]
        meta = meta_list[i] if i < len(meta_list) else {}
        chunk_id = ids_list[i] if i < len(ids_list) else f"chunk_{i}"

        source = meta.get("source", "unknown")
        chunk_scope = meta.get("scope", "unknown")
        start_line = meta.get("start_line", 1)
        end_line = meta.get("end_line", 1)

        retrieved_chunks.append(
            {
                "id": chunk_id,
                "source": source,
                "scope": chunk_scope,
                "start_line": start_line,
                "end_line": end_line,
                "content": doc,
            }
        )

        block = (
            f"--- START SOURCE FILE: {source} "
            f"(Scope: {chunk_scope.upper()}, Lines: {start_line}-{end_line}) ---\n"
            f"{doc}\n"
            f"--- END SOURCE FILE: {source} ---\n"
        )
        context_blocks.append(block)

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
        "4. If the answer cannot be deduced from the provided code, state it clearly, "
        "but try to respond helpfully with what is available.\n"
    )

    prompt = (
        f"CONTEXTO DE CÓDIGO FUENTE RECUPERADO:\n"
        f"{context_str}\n\n"
        f"PREGUNTA DEL USUARIO:\n"
        f"{query_text}\n\n"
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
