import os
from typing import Any

from rag_local.core import config
from rag_local.core.exceptions import QueryError
from rag_local.core.logging import logger
from rag_local.services.db import (
    get_chroma_collection,
    query_db,
    sanitize_sql_value,
)
from rag_local.services.gemini import generate_content
from rag_local.services.rag_enrichment import (
    enrich_rag_context,
    resolve_relative_import,
)
from rag_local.services.rag_formatters import (
    compress_code,
    merge_and_format_file_chunks,
    truncate_xml_safe,
    xml_escape,
)
from rag_local.services.rag_reranker import get_reranker

# Alias para mantener compatibilidad privada si algún test invoca _truncate_xml_safe
_truncate_xml_safe = truncate_xml_safe


def process_query(
    query_text: str,
    scope: str | None = None,
    respond_in_english: bool = False,
    k: int = 4,
    generate_response: bool = True,
    full_block: bool = False,
) -> dict[str, Any]:
    """Orquesta el flujo RAG: consulta la BD, opcionalmente re-rankea

    y genera la respuesta.
    """
    try:
        is_mock = os.getenv("RAG_MOCK_API") == "1"

        # 1. Consultar base de datos vectorial
        if is_mock:
            results = query_db(query_text, scope, k=k)
        else:
            results = query_db(query_text, scope, k=config.INITIAL_K_FOR_RERANK)

        documents = results.get("documents")
        metadatas = results.get("metadatas")
        ids = results.get("ids")

        docs_list = documents[0] if documents else []
        meta_list = metadatas[0] if metadatas else []
        ids_list = ids[0] if ids else []

        # 2. Re-ranking con filtro de relevancia
        if not is_mock and docs_list:
            try:
                from rag_local.daemon.client import try_daemon_rerank

                ranked_results = try_daemon_rerank(query_text, docs_list)
                if ranked_results is None:
                    reranker = get_reranker()
                    ranked_results = reranker.rank(query=query_text, docs=docs_list)

                new_docs = []
                new_metas = []
                new_ids = []
                rerank_scores = []
                for item in ranked_results:
                    orig_idx = int(getattr(item, "doc_id", 0))
                    score = float(getattr(item, "score", 0.0))
                    rerank_scores.append(score)
                    new_docs.append(docs_list[orig_idx])
                    if orig_idx < len(meta_list):
                        new_metas.append(meta_list[orig_idx])
                    if orig_idx < len(ids_list):
                        new_ids.append(ids_list[orig_idx])

                min_score = config.MIN_RERANK_SCORE
                filtered = [
                    (d, m, i)
                    for d, m, i, s in zip(
                        new_docs, new_metas, new_ids, rerank_scores, strict=False
                    )
                    if s >= min_score
                ]

                if filtered:
                    f_docs, f_metas, f_ids = zip(*filtered, strict=False)
                    docs_list = list(f_docs)[:k]
                    meta_list = list(f_metas)[:k]
                    ids_list = list(f_ids)[:k]
                    logger.info(
                        f"  Re-rank: {len(filtered)} chunks sobre threshold "
                        f"({min_score}), top score: {rerank_scores[0]:.4f}"
                    )
                else:
                    top_score = rerank_scores[0] if rerank_scores else None
                    logger.info(
                        f"  Re-rank: todos los chunks bajo threshold ({min_score}). "
                        f"Top score: {top_score:.4f}"
                        if top_score is not None
                        else f"  Re-rank: sin resultados tras filtro ({min_score})."
                    )
                    docs_list = []
                    meta_list = []
                    ids_list = []
            except Exception as e:
                logger.warning(f"Error al re-rankear resultados, usando fallback: {e}")
                docs_list = docs_list[:k]
                meta_list = meta_list[:k]
                ids_list = ids_list[:k]

        # 2.1 Expansión de contexto inteligente (Enclosing Scope desde LanceDB)
        if full_block and meta_list:
            try:
                collection = get_chroma_collection()
                expanded_docs = list(docs_list)
                expanded_metas = list(meta_list)
                expanded_ids = list(ids_list)
                seen_ids = set(ids_list)

                for meta in meta_list:
                    src = meta.get("source")
                    c_name = meta.get("class_name")
                    m_name = meta.get("method_name")
                    where_clause = None

                    if src and c_name:
                        escaped_src = sanitize_sql_value(src)
                        escaped_cname = sanitize_sql_value(c_name)
                        where_clause = (
                            f"source = '{escaped_src}' "
                            f"AND class_name = '{escaped_cname}'"
                        )
                    elif src and m_name:
                        escaped_src = sanitize_sql_value(src)
                        escaped_mname = sanitize_sql_value(m_name)
                        where_clause = (
                            f"source = '{escaped_src}' "
                            f"AND method_name = '{escaped_mname}'"
                        )

                    if where_clause:
                        sibling_rows = (
                            collection.table.search()
                            .where(where_clause)
                            .limit(50)
                            .to_list()
                        )
                        for row in sibling_rows:
                            row_id = row.get("id", "")
                            if row_id and row_id not in seen_ids:
                                seen_ids.add(row_id)
                                expanded_ids.append(row_id)
                                expanded_docs.append(row.get("text", ""))
                                expanded_metas.append(
                                    {
                                        "source": row.get("source", ""),
                                        "scope": row.get("scope", ""),
                                        "start_line": int(row.get("start_line", 1)),
                                        "end_line": int(row.get("end_line", 1)),
                                        "class_name": row.get("class_name", ""),
                                        "method_name": row.get("method_name", ""),
                                    }
                                )
                docs_list = expanded_docs
                meta_list = expanded_metas
                ids_list = expanded_ids
            except Exception as ex:
                logger.warning(
                    f"No se pudieron expandir los bloques desde LanceDB: {ex}"
                )

        # 3. Agrupar y fusionar chunks por archivo
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
                content_lines = chunk["content"].splitlines()
                start = chunk["start_line"]
                for idx, line in enumerate(content_lines):
                    file_lines[start + idx] = line

            if not file_lines:
                continue

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

            for block in blocks:
                b_start = block[0]
                b_end = block[-1]
                b_content = "\n".join(file_lines[n] for n in block)

                chunk_scope = file_chunks[0]["scope"]
                source_normalized = source.replace("\\", "/")

                is_compressed = False
                content_to_use = b_content
                if getattr(config, "COMPRESS_CODE_CONTEXT", False):
                    content_to_use = compress_code(b_content, source_normalized)
                    is_compressed = True

                retrieved_chunks.append(
                    {
                        "id": f"{source_normalized}#L{b_start}-{b_end}",
                        "source": source_normalized,
                        "scope": chunk_scope,
                        "start_line": b_start,
                        "end_line": b_end,
                        "content": content_to_use,
                    }
                )

                escaped_content = xml_escape(content_to_use)
                compressed_attr = ' compressed="true"' if is_compressed else ""
                xml_block = (
                    f'<file path="{xml_escape(source_normalized)}"'
                    f' start_line="{b_start}"'
                    f' end_line="{b_end}"{compressed_attr}>\n'
                    f"{escaped_content}\n"
                    f"</file>"
                )
                context_blocks.append(xml_block)

        # 4. Enriquecimiento de contexto (Graph-RAG)
        collection = get_chroma_collection()
        enriched_blocks = enrich_rag_context(meta_list, collection)

        # 5. Preparar contexto e instrucciones de Gemini
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

        direct_context = "\n".join(context_blocks)
        max_context_chars = config.MAX_CONTEXT_CHARS
        remaining_chars = max_context_chars - len(direct_context)

        included_enriched = []
        enriched_chars_used = 0
        for block in enriched_blocks:
            if enriched_chars_used + len(block) > remaining_chars:
                break
            included_enriched.append(block)
            enriched_chars_used += len(block)

        context_str = "\n".join(context_blocks + included_enriched)
        is_truncated = len(context_str) > max_context_chars
        if is_truncated:
            context_str = truncate_xml_safe(context_str, max_context_chars)

        context_str_wrapped = f"<context>\n{context_str}\n</context>"

        target_language = "ENGLISH" if respond_in_english else "SPANISH"

        system_instruction = (
            "You are a Senior AI Engineer expert in software development, "
            f"{config.SYSTEM_INSTRUCTION_TECH_STACK}.\n"
            "Your task is to answer the user's question "
            "based strictly on the provided source code context.\n"
            "Follow these strict rules:\n"
            f"1. ALWAYS RESPOND IN {target_language} to the user.\n"
            "2. Be clear, educational, and direct.\n"
            "3. Use code blocks when necessary to illustrate or explain the solution.\n"
            "4. If the answer cannot be deduced from the "
            "provided code, state it clearly.\n"
            "5. You MUST wrap your entire response inside a <response> tag."
        )

        prompt = (
            f"CONTEXTO DE CÓDIGO FUENTE RECUPERADO:\n"
            f"{context_str_wrapped}\n\n"
            f"PREGUNTA DEL USUARIO:\n"
            f"{xml_escape(query_text)}\n\n"
            f"RESPUESTA:"
        )

        # 6. Generar respuesta si se solicita
        if generate_response:
            answer = generate_content(prompt, system_instruction)
        else:
            answer = ""

        return {
            "query": query_text,
            "scope": scope,
            "retrieved_chunks": retrieved_chunks,
            "context": context_str_wrapped,
            "response": answer,
        }
    except QueryError:
        raise
    except Exception as e:
        logger.exception("Error inesperado en process_query")
        raise QueryError(f"Error inesperado en process_query: {e}") from e


__all__ = [
    "_truncate_xml_safe",
    "compress_code",
    "enrich_rag_context",
    "get_reranker",
    "merge_and_format_file_chunks",
    "process_query",
    "resolve_relative_import",
    "truncate_xml_safe",
    "xml_escape",
]
