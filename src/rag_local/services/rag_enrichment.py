import os
import re
from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.services.db import get_table_names
from rag_local.services.rag_formatters import (
    compress_code,
    merge_and_format_file_chunks,
    xml_escape,
)


def resolve_relative_import(source_file: str, target: str) -> list[str]:
    """Resuelve rutas de importación relativas para TypeScript/JavaScript y Python."""
    candidates = []
    source_dir = os.path.dirname(source_file)

    if target.startswith("."):
        resolved_rel = os.path.normpath(os.path.join(source_dir, target)).replace(
            "\\", "/"
        )
        candidates.extend(
            [
                resolved_rel,
                f"{resolved_rel}.ts",
                f"{resolved_rel}.tsx",
                f"{resolved_rel}.js",
                f"{resolved_rel}/index.ts",
                f"{resolved_rel}/index.tsx",
                f"{resolved_rel}/index.js",
            ]
        )
    elif "from ." in target or "import ." in target:
        match = re.search(r"(?:from|import)\s+(\.+)([\w\.]+)?", target)
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
                    os.path.join(resolved_rel, "__init__.py").replace("\\", "/"),
                    resolved_rel,
                ]
            )
    return candidates


def enrich_rag_context(meta_list: list[dict[str, Any]], collection: Any) -> list[str]:
    """Enriquece el contexto RAG consultando relaciones de código

    y metadatos (Graph-RAG).
    """
    enriched_blocks = []
    try:
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
            if source.endswith((".ts", ".js", ".tsx", ".py")):
                import_targets = []
                depends_targets = []

                try:
                    from rag_local.services.db import get_db_connection

                    db_conn = get_db_connection()
                    if "code_relationships" in get_table_names(db_conn):
                        table_rel = db_conn.open_table("code_relationships")
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

                for target in import_targets:
                    if enriched_count >= max_enriched:
                        break
                    if target in seen_targets:
                        continue
                    seen_targets.add(target)

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

                for target in depends_targets:
                    if enriched_count >= max_enriched:
                        break
                    if target in seen_targets:
                        continue
                    seen_targets.add(target)

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
                                        target_source_normalized = (
                                            target_source.replace("\\", "/")
                                        )
                                        source_normalized = source.replace("\\", "/")

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

                                max_enriched_limit = getattr(
                                    config, "MAX_ENRICHED_CHUNK_CHARS", 3000
                                )
                                if len(content_to_use) > max_enriched_limit:
                                    content_to_use = (
                                        content_to_use[:max_enriched_limit]
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

    return enriched_blocks
