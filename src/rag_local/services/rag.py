from typing import Any

from rag_local.core.logging import logger
from rag_local.services.db import get_chroma_collection, query_db
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


def merge_and_format_file_chunks(
    documents: list[str], metadatas: list[dict[str, Any]]
) -> str:
    """Combina y ordena fragmentos usando start_line."""
    file_lines = {}
    for doc, meta in zip(documents, metadatas, strict=False):
        start = int(meta.get("start_line", 1))
        content_lines = doc.splitlines()
        for idx, line in enumerate(content_lines):
            file_lines[start + idx] = line
    if not file_lines:
        return ""
    sorted_lines = sorted(file_lines.keys())
    return "\n".join(file_lines[n] for n in sorted_lines)


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

    # Enriquecimiento de contexto (Graph-RAG para TS y Prisma)
    enriched_blocks = []
    try:
        collection = get_chroma_collection()
        retrieved_files = {
            meta.get("source") for meta in meta_list if meta.get("source")
        }
        retrieved_models = set()
        for meta in meta_list:
            models_str = meta.get("models", "")
            if models_str:
                for m in models_str.split(","):
                    m_clean = m.strip()
                    if m_clean:
                        retrieved_models.add(m_clean)

        enriched_files = set()
        enriched_models = set()
        max_enriched = 10
        enriched_count = 0

        for meta in meta_list:
            if enriched_count >= max_enriched:
                break

            source = meta.get("source", "")
            if not source:
                continue

            # 1. Enriquecimiento para TypeScript/NestJS
            if source.endswith((".ts", ".tsx")):
                imports_str = meta.get("imports", "")
                dependencies_str = meta.get("dependencies", "")

                import_targets = []
                if imports_str:
                    import_targets.extend(
                        [i.strip() for i in imports_str.split(",") if i.strip()]
                    )
                if dependencies_str:
                    import_targets.extend(
                        [d.strip() for d in dependencies_str.split(",") if d.strip()]
                    )

                seen_targets = set()
                for target in import_targets:
                    if enriched_count >= max_enriched:
                        break
                    if target in seen_targets:
                        continue
                    seen_targets.add(target)

                    # Caso A: Importaciones directas (locales)
                    if target.startswith("."):
                        source_dir = os.path.dirname(source)
                        resolved_rel = os.path.normpath(
                            os.path.join(source_dir, target)
                        ).replace("\\", "/")

                        candidates = [
                            resolved_rel,
                            f"{resolved_rel}.ts",
                            f"{resolved_rel}.tsx",
                        ]
                        found_chunks = None
                        matched_source = None
                        for cand in candidates:
                            if cand in retrieved_files or cand in enriched_files:
                                matched_source = cand
                                break
                            try:
                                res = collection.get(where={"source": cand})
                                if res and res.get("documents"):
                                    found_chunks = res
                                    matched_source = cand
                                    break
                            except Exception as e:
                                logger.debug(f"Error al buscar source {cand}: {e}")

                        if (
                            found_chunks
                            and matched_source
                            and matched_source not in enriched_files
                            and matched_source not in retrieved_files
                        ):
                            enriched_files.add(matched_source)
                            content = merge_and_format_file_chunks(
                                found_chunks["documents"],
                                found_chunks["metadatas"],
                            )
                            if content.strip():
                                escaped_content = xml_escape(content)
                                xml_block = (
                                    f'<imported_file path="{matched_source}" '
                                    f'relation_type="import" source_file="{source}">\n'
                                    f"{escaped_content}\n"
                                    f"</imported_file>"
                                )
                                enriched_blocks.append(xml_block)
                                enriched_count += 1

                    # Caso B: Clases dependientes
                    elif (
                        target
                        and target[0].isupper()
                        and target
                        not in (
                            "String",
                            "Int",
                            "Boolean",
                            "DateTime",
                            "Json",
                            "Decimal",
                            "Float",
                            "Bytes",
                        )
                    ):
                        if target in enriched_files:
                            continue
                        try:
                            res = collection.get(where={"class_name": target})
                            if res and res.get("documents"):
                                target_source = res["metadatas"][0].get("source", "")
                                if (
                                    target_source
                                    and target_source not in retrieved_files
                                    and target_source not in enriched_files
                                ):
                                    enriched_files.add(target_source)
                                    content = merge_and_format_file_chunks(
                                        res["documents"], res["metadatas"]
                                    )
                                    if content.strip():
                                        escaped_content = xml_escape(content)
                                        xml_block = (
                                            f'<imported_file path="{target_source}" '
                                            f'dependency_class="{target}" '
                                            f'relation_type="dependency" '
                                            f'source_file="{source}">\n'
                                            f"{escaped_content}\n"
                                            f"</imported_file>"
                                        )
                                        enriched_blocks.append(xml_block)
                                        enriched_count += 1
                        except Exception as e:
                            logger.debug(f"Error al buscar clase {target}: {e}")

            # 2. Enriquecimiento para Prisma
            elif source.endswith(".prisma"):
                deps_str = meta.get("dependencies", "")
                models_str = meta.get("models", "")

                rel_models = []
                if deps_str:
                    rel_models.extend(
                        [m.strip() for m in deps_str.split(",") if m.strip()]
                    )
                if models_str:
                    rel_models.extend(
                        [m.strip() for m in models_str.split(",") if m.strip()]
                    )

                for rel in rel_models:
                    if enriched_count >= max_enriched:
                        break
                    if rel in retrieved_models or rel in enriched_models:
                        continue

                    try:
                        res = collection.get(where={"class_name": rel})
                        if res and res.get("documents"):
                            enriched_models.add(rel)
                            content = merge_and_format_file_chunks(
                                res["documents"], res["metadatas"]
                            )
                            if content.strip():
                                escaped_content = xml_escape(content)
                                target_source = res["metadatas"][0].get(
                                    "source", "prisma/schema.prisma"
                                )
                                xml_block = (
                                    f'<related_model name="{rel}" '
                                    f'source_file="{target_source}" '
                                    f'parent_model="{meta.get("class_name", "")}">\n'
                                    f"{escaped_content}\n"
                                    f"</related_model>"
                                )
                                enriched_blocks.append(xml_block)
                                enriched_count += 1
                    except Exception as e:
                        logger.debug(f"Error al buscar modelo prisma {rel}: {e}")
    except Exception as e:
        logger.error(f"Error durante el enriquecimiento de contexto RAG: {e}")

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

    context_str = "\n".join(context_blocks + enriched_blocks)

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
