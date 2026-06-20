import html
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.exceptions import QueryError
from rag_local.core.logging import logger
from rag_local.services.db import get_chroma_collection, get_table_names, query_db
from rag_local.services.gemini import generate_content


def compress_code(text: str, file_path: str) -> str:
    """Comprime el código fuente eliminando comentarios irrelevantes.

    Conserva directivas de compilador y linters críticas, y remueve líneas
    vacías extras.
    """
    if not text:
        return ""

    suffix = Path(file_path).suffix.lower() if file_path else ""
    lines = text.splitlines()
    compressed_lines = []

    # Expresiones regulares para directivas críticas que se deben conservar
    ts_directive_pat = re.compile(r"^\s*//\s*@(ts-|eslint-|ng)")
    py_directive_pat = re.compile(r"^\s*#\s*(type:|pylint:|coding:)")

    for line in lines:
        stripped = line.strip()

        # Eliminar comentarios puros específicos del lenguaje
        if suffix in (".ts", ".tsx", ".prisma"):
            if stripped.startswith("//") and not ts_directive_pat.match(line):
                continue
            if stripped.startswith("/*") and stripped.endswith("*/"):
                # Comentario de bloque en una sola línea
                continue
        elif (
            suffix == ".py"
            and stripped.startswith("#")
            and not py_directive_pat.match(line)
        ) or (
            suffix in (".html", ".htm")
            and stripped.startswith("<!--")
            and stripped.endswith("-->")
        ):
            continue

        # rstrip para eliminar espacios innecesarios al final
        compressed_lines.append(line.rstrip())

    # Colapsar múltiples líneas vacías consecutivas en una sola
    final_lines = []
    prev_was_empty = False
    for line in compressed_lines:
        if not line:
            if prev_was_empty:
                continue
            prev_was_empty = True
        else:
            prev_was_empty = False
        final_lines.append(line)

    return "\n".join(final_lines)


def xml_escape(text: str) -> str:
    """Escapa los caracteres especiales para evitar inyecciones XML

    y filtra caracteres de control.
    """
    if not text:
        return ""
    # Filtrar caracteres de control Unicode (categorías Cc y Cf, excepto \n, \r, \t)
    clean_chars = []
    for c in text:
        cat = unicodedata.category(c)
        if cat in ("Cc", "Cf") and c not in ("\n", "\r", "\t"):
            continue
        clean_chars.append(c)
    clean_text = "".join(clean_chars)

    escaped = html.escape(clean_text, quote=True)
    # Normalizar comillas simples a &apos; para compatibilidad XML
    return (
        escaped.replace("'", "&apos;")
        .replace("&#x27;", "&apos;")
        .replace("&#39;", "&apos;")
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


def get_reranker() -> Any:
    """Obtiene o inicializa el Reranker de forma perezosa."""
    global _reranker
    if _reranker is None:
        import torch
        from rerankers import Reranker

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", device=device)
    return _reranker


def _truncate_xml_safe(context: str, max_chars: int) -> str:
    """Trunca el contexto XML sin romper etiquetas abiertas.

    Busca el último cierre de tag completo antes del límite.
    """
    if len(context) <= max_chars:
        return context

    # Buscar el último cierre de tag XML completo antes del límite
    truncated = context[:max_chars]
    # Buscar la última etiqueta de cierre completa
    close_tags = ["</file>", "</imported_file>", "</related_model>"]
    last_safe_pos = -1
    for tag in close_tags:
        pos = truncated.rfind(tag)
        if pos != -1:
            end_pos = pos + len(tag)
            if end_pos > last_safe_pos:
                last_safe_pos = end_pos

    if last_safe_pos > 0:
        return truncated[:last_safe_pos] + "\n[TRUNCATED]"
    return truncated + "\n[TRUNCATED]"


def process_query(
    query_text: str,
    scope: str | None = None,
    respond_in_english: bool = False,
    k: int = 4,
    generate_response: bool = True,
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

        # 2. Re-ranking
        if not is_mock and docs_list:
            try:
                reranker = get_reranker()
                ranked_results = reranker.rank(query=query_text, docs=docs_list)

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
                logger.warning(f"Error al re-rankear resultados, usando fallback: {e}")
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
            max_enriched = config.MAX_ENRICHED_CONTEXT_FILES
            enriched_count = 0

            for meta in meta_list:
                if enriched_count >= max_enriched:
                    break

                source = meta.get("source", "")
                if not source:
                    continue

                # 1. Enriquecimiento genérico (TypeScript y Python)
                # basado en la base de datos de grafo
                if source.endswith((".ts", ".tsx", ".py")):
                    import_targets = []
                    depends_targets = []

                    try:
                        db_path = str(config.LANCEDB_PATH)
                        import lancedb

                        db_conn = lancedb.connect(db_path)
                        if "code_relationships" in get_table_names(db_conn):
                            table_rel = db_conn.open_table("code_relationships")
                            # Sanitizar valor SQL para evitar
                            # inyección o rotura de strings
                            sanitized_source = source.replace("'", "''")
                            rel_rows = (
                                table_rel.search()
                                .where(f"source_file = '{sanitized_source}'")
                                .to_list()
                            )

                            for row in rel_rows:
                                target_symbol = row.get("target_symbol", "")
                                rel_type = row.get("relationship_type", "")
                                if target_symbol:
                                    if rel_type == "import":
                                        import_targets.append(target_symbol)
                                    elif rel_type == "depends_on":
                                        depends_targets.append(target_symbol)
                    except Exception as e:
                        logger.warning(
                            f"Error al consultar la tabla code_relationships: {e}"
                        )

                    # Si falló la consulta o la tabla no existía, usar
                    # fallback de metadatos en caliente
                    if not import_targets and not depends_targets:
                        imports_str = meta.get("imports", "")
                        dependencies_str = meta.get("dependencies", "")
                        if imports_str:
                            import_targets.extend(
                                [i.strip() for i in imports_str.split(",") if i.strip()]
                            )
                        if dependencies_str:
                            depends_targets.extend(
                                [
                                    d.strip()
                                    for d in dependencies_str.split(",")
                                    if d.strip()
                                ]
                            )

                    seen_targets = set()

                    # Helper para resolver imports relativos de TS y Python
                    def resolve_relative_import(
                        source_file: str, target: str
                    ) -> list[str]:
                        candidates = []
                        source_dir = os.path.dirname(source_file)

                        # TypeScript relativo
                        if target.startswith("."):
                            resolved_rel = os.path.normpath(
                                os.path.join(source_dir, target)
                            ).replace("\\", "/")
                            candidates.extend(
                                [
                                    resolved_rel,
                                    f"{resolved_rel}.ts",
                                    f"{resolved_rel}.tsx",
                                    f"{resolved_rel}/index.ts",
                                    f"{resolved_rel}/index.tsx",
                                ]
                            )
                        # Python relativo
                        elif "from ." in target or "import ." in target:
                            match = re.search(
                                r"(?:from|import)\s+(\.+)([\w\.]+)?", target
                            )
                            if match:
                                dots = match.group(1)
                                module_path = match.group(2) or ""

                                steps = len(dots) - 1
                                current_dir = source_dir
                                for _ in range(steps):
                                    current_dir = os.path.dirname(current_dir)

                                module_rel = module_path.replace(".", "/")
                                resolved_rel = os.path.normpath(
                                    os.path.join(current_dir, module_rel)
                                ).replace("\\", "/")

                                candidates.extend(
                                    [
                                        f"{resolved_rel}.py",
                                        os.path.join(
                                            resolved_rel, "__init__.py"
                                        ).replace("\\", "/"),
                                        resolved_rel,
                                    ]
                                )
                        return candidates

                    # Procesar imports (Caso A)
                    for target in import_targets:
                        if enriched_count >= max_enriched:
                            break
                        if target in seen_targets:
                            continue
                        seen_targets.add(target)

                        # Caso A: Importaciones directas (locales/relativas)
                        candidates = resolve_relative_import(source, target)
                        if candidates:
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
                                    matched_source_normalized = matched_source.replace(
                                        "\\", "/"
                                    )
                                    source_normalized = source.replace("\\", "/")

                                    is_compressed = False
                                    content_to_use = content
                                    if getattr(config, "COMPRESS_CODE_CONTEXT", False):
                                        content_to_use = compress_code(
                                            content, matched_source_normalized
                                        )
                                        is_compressed = True

                                    max_enriched_chars = getattr(
                                        config, "MAX_ENRICHED_CHUNK_CHARS", 3000
                                    )
                                    if len(content_to_use) > max_enriched_chars:
                                        content_to_use = (
                                            content_to_use[:max_enriched_chars]
                                            + "\n[TRUNCATED]"
                                        )
                                    escaped_content = xml_escape(content_to_use)
                                    compressed_attr = (
                                        ' compressed="true"' if is_compressed else ""
                                    )
                                    xml_block = (
                                        f"<imported_file"
                                        f' path="'
                                        f'{xml_escape(matched_source_normalized)}"'
                                        f' relation_type="import"'
                                        f' source_file="'
                                        f'{xml_escape(source_normalized)}"'
                                        f"{compressed_attr}>\n"
                                        f"{escaped_content}\n"
                                        f"</imported_file>"
                                    )
                                    enriched_blocks.append(xml_block)
                                    enriched_count += 1

                    # Procesar dependencias de clase/símbolo (Caso B)
                    for target in depends_targets:
                        if enriched_count >= max_enriched:
                            break
                        if target in seen_targets:
                            continue
                        seen_targets.add(target)

                        # Caso B: Clases dependientes
                        if (
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
                                    target_source = res["metadatas"][0].get(
                                        "source", ""
                                    )
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
                                            target_source_normalized = (
                                                target_source.replace("\\", "/")
                                            )
                                            source_normalized = source.replace(
                                                "\\", "/"
                                            )

                                            is_compressed = False
                                            content_to_use = content
                                            if getattr(
                                                config,
                                                "COMPRESS_CODE_CONTEXT",
                                                False,
                                            ):
                                                content_to_use = compress_code(
                                                    content,
                                                    target_source_normalized,
                                                )
                                                is_compressed = True

                                            max_enriched_chars = getattr(
                                                config,
                                                "MAX_ENRICHED_CHUNK_CHARS",
                                                3000,
                                            )
                                            if len(content_to_use) > max_enriched_chars:
                                                content_to_use = (
                                                    content_to_use[:max_enriched_chars]
                                                    + "\n[TRUNCATED]"
                                                )
                                            escaped_content = xml_escape(content_to_use)
                                            compressed_attr = (
                                                ' compressed="true"'
                                                if is_compressed
                                                else ""
                                            )
                                            xml_block = (
                                                f"<imported_file"
                                                f' path="'
                                                f'{xml_escape(target_source_normalized)}"'
                                                f" dependency_class="
                                                f'"{xml_escape(target)}"'
                                                f' relation_type="dependency"'
                                                f' source_file="'
                                                f'{xml_escape(source_normalized)}"'
                                                f"{compressed_attr}>\n"
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
                                    target_source = res["metadatas"][0].get(
                                        "source", "prisma/schema.prisma"
                                    )
                                    target_source_normalized = target_source.replace(
                                        "\\", "/"
                                    )

                                    is_compressed = False
                                    content_to_use = content
                                    if getattr(config, "COMPRESS_CODE_CONTEXT", False):
                                        content_to_use = compress_code(
                                            content, target_source_normalized
                                        )
                                        is_compressed = True

                                    # Limitar tamaño individual del archivo
                                    # enriquecido
                                    max_enriched = getattr(
                                        config, "MAX_ENRICHED_CHUNK_CHARS", 3000
                                    )
                                    if len(content_to_use) > max_enriched:
                                        content_to_use = (
                                            content_to_use[:max_enriched]
                                            + "\n[TRUNCATED]"
                                        )
                                    escaped_content = xml_escape(content_to_use)
                                    compressed_attr = (
                                        ' compressed="true"' if is_compressed else ""
                                    )
                                    xml_block = (
                                        f'<related_model name="{xml_escape(rel)}"'
                                        f' source_file="'
                                        f'{xml_escape(target_source_normalized)}"'
                                        f' parent_model="'
                                        f'{xml_escape(meta.get("class_name", ""))}"'
                                        f"{compressed_attr}>\n"
                                        f"{escaped_content}\n"
                                        f"</related_model>"
                                    )
                                    enriched_blocks.append(xml_block)
                                    enriched_count += 1
                        except Exception as e:
                            logger.debug(f"Error al buscar modelo prisma {rel}: {e}")
        except Exception:
            logger.exception("Error durante el enriquecimiento de contexto RAG")

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

        # Priorizar contexto directo: construirlo primero
        direct_context = "\n".join(context_blocks)

        # Calcular espacio restante para enriquecimiento
        max_context_chars = config.MAX_CONTEXT_CHARS
        remaining_chars = max_context_chars - len(direct_context)

        # Agregar bloques enriquecidos solo si hay espacio
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
            context_str = _truncate_xml_safe(context_str, max_context_chars)

        if is_truncated:
            context_str_wrapped = f"<context>\n{context_str}\n</context>"
        else:
            context_str_wrapped = f"<context>\n{context_str}\n</context>"

        target_language = "ENGLISH" if respond_in_english else "SPANISH"

        system_instruction = (
            "You are a Senior AI Engineer expert in software development, "
            "Angular 21, NestJS 11, Fastify, and Prisma.\n"
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

        # 4. Generar respuesta si se solicita
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
