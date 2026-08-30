import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.services.db import get_table_names
from rag_local.services.rag_formatters import (
    compress_code,
    merge_and_format_file_chunks,
    xml_escape,
)


def _parse_json_with_comments(file_path: Path) -> dict[str, Any]:
    """Carga y parsea un archivo JSON permitiendo comentarios y comas finales."""
    try:
        content = file_path.read_text(encoding="utf-8")
        content_no_comments = re.sub(
            r"//.*?$|/\*.*?\*/", "", content, flags=re.MULTILINE | re.DOTALL
        )
        content_clean = re.sub(r",\s*([\]}])", r"\1", content_no_comments)
        data = json.loads(content_clean)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug(f"No se pudo cargar o parsear el archivo {file_path}: {e}")
        return {}


@lru_cache(maxsize=32)
def _load_tsconfig_compiler_options(
    tsconfig_path_str: str, max_depth: int = 3
) -> tuple[str, tuple[tuple[str, tuple[str, ...]], ...]]:
    """Extrae baseUrl y paths resolviendo posibles herencias de tsconfig."""
    tsconfig_path = Path(tsconfig_path_str)
    if not tsconfig_path.is_file() or max_depth <= 0:
        return ".", ()

    data = _parse_json_with_comments(tsconfig_path)
    base_url = "."
    paths_dict: dict[str, list[str]] = {}

    extends_val = data.get("extends")
    if isinstance(extends_val, str) and extends_val:
        ext_path = (tsconfig_path.parent / extends_val).resolve()
        if not ext_path.suffix:
            ext_path = ext_path.with_suffix(".json")
        if ext_path.is_file():
            parent_base, parent_paths = _load_tsconfig_compiler_options(
                str(ext_path), max_depth=max_depth - 1
            )
            base_url = parent_base
            for k, v in parent_paths:
                paths_dict[k] = list(v)

    compiler_options = data.get("compilerOptions")
    if isinstance(compiler_options, dict):
        if "baseUrl" in compiler_options and isinstance(
            compiler_options["baseUrl"], str
        ):
            base_url = compiler_options["baseUrl"]
        if "paths" in compiler_options and isinstance(compiler_options["paths"], dict):
            for k, v in compiler_options["paths"].items():
                if isinstance(v, list):
                    paths_dict[k] = [item for item in v if isinstance(item, str)]
                elif isinstance(v, str):
                    paths_dict[k] = [v]

    paths_tuple = tuple((k, tuple(v)) for k, v in paths_dict.items())
    return base_url, paths_tuple


def _find_tsconfig_for_source(
    source_file: str,
) -> tuple[Path, str, dict[str, list[str]]] | None:
    """Localiza el tsconfig.json más cercano a source_file o en la raíz del repo."""
    repo_root = config.REPO_ROOT.resolve()

    abs_source = Path(source_file)
    if not abs_source.is_absolute():
        abs_source = (repo_root / source_file).resolve()

    curr_dir = abs_source.parent

    while True:
        candidate_ts = curr_dir / "tsconfig.json"
        if candidate_ts.is_file():
            base_url, paths_tuple = _load_tsconfig_compiler_options(str(candidate_ts))
            paths_dict = {k: list(v) for k, v in paths_tuple}
            return candidate_ts, base_url, paths_dict

        if curr_dir == repo_root or repo_root not in curr_dir.parents:
            break
        curr_dir = curr_dir.parent

    root_ts = repo_root / "tsconfig.json"
    if root_ts.is_file():
        base_url, paths_tuple = _load_tsconfig_compiler_options(str(root_ts))
        paths_dict = {k: list(v) for k, v in paths_tuple}
        return root_ts, base_url, paths_dict

    return None


def _generate_ts_candidates(base_path: str) -> list[str]:
    """Genera variantes de extensiones e índices para una ruta TypeScript/JavaScript."""
    base_clean = base_path.replace("\\", "/").rstrip("/")
    if not base_clean:
        return []

    if base_clean.endswith((".ts", ".tsx", ".js", ".jsx", ".py", ".json", ".css")):
        return [base_clean]

    return [
        base_clean,
        f"{base_clean}.ts",
        f"{base_clean}.tsx",
        f"{base_clean}.js",
        f"{base_clean}.jsx",
        f"{base_clean}/index.ts",
        f"{base_clean}/index.tsx",
        f"{base_clean}/index.js",
        f"{base_clean}/index.jsx",
    ]


def resolve_relative_import(source_file: str, target: str) -> list[str]:
    """Resuelve rutas de importación relativas y path aliases (TS y Python)."""
    candidates: list[str] = []
    source_dir = os.path.dirname(source_file)

    if target.startswith("."):
        resolved_rel = os.path.normpath(os.path.join(source_dir, target)).replace(
            "\\", "/"
        )
        candidates.extend(_generate_ts_candidates(resolved_rel))

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
    else:
        repo_root = config.REPO_ROOT.resolve()
        abs_source = Path(source_file)
        if not abs_source.is_absolute():
            abs_source = (repo_root / source_file).resolve()

        ts_info = _find_tsconfig_for_source(source_file)
        matched_by_tsconfig = False

        if ts_info:
            tsconfig_file, base_url_setting, paths_map = ts_info
            tsconfig_dir = tsconfig_file.parent.resolve()
            abs_base_dir = (tsconfig_dir / base_url_setting).resolve()

            try:
                rel_base_dir = str(abs_base_dir.relative_to(repo_root)).replace(
                    "\\", "/"
                )
                if rel_base_dir == ".":
                    rel_base_dir = ""
            except ValueError:
                rel_base_dir = ""

            for pattern, replacements in paths_map.items():
                if "*" in pattern:
                    prefix, _, suffix = pattern.partition("*")
                    if target.startswith(prefix) and target.endswith(suffix):
                        matched_by_tsconfig = True
                        suffix_len = len(suffix) if suffix else 0
                        matched_wildcard = target[
                            len(prefix) : len(target) - suffix_len
                        ]
                        for repl in replacements:
                            repl_path = repl.replace("*", matched_wildcard)
                            resolved_rel = os.path.normpath(
                                os.path.join(rel_base_dir, repl_path)
                            ).replace("\\", "/")
                            candidates.extend(_generate_ts_candidates(resolved_rel))
                elif target == pattern:
                    matched_by_tsconfig = True
                    for repl in replacements:
                        resolved_rel = os.path.normpath(
                            os.path.join(rel_base_dir, repl)
                        ).replace("\\", "/")
                        candidates.extend(_generate_ts_candidates(resolved_rel))

            if not matched_by_tsconfig and base_url_setting not in (".", "./"):
                resolved_rel = os.path.normpath(
                    os.path.join(rel_base_dir, target)
                ).replace("\\", "/")
                candidates.extend(_generate_ts_candidates(resolved_rel))
                matched_by_tsconfig = True

        if not matched_by_tsconfig:
            alias_prefixes = ("@/", "~/")
            for alias_pfx in alias_prefixes:
                if target.startswith(alias_pfx):
                    remainder = target[len(alias_pfx) :]

                    try:
                        rel_source_dir = str(
                            abs_source.parent.relative_to(repo_root)
                        ).replace("\\", "/")
                    except ValueError:
                        rel_source_dir = source_dir

                    parts = [p for p in rel_source_dir.split("/") if p and p != "."]
                    search_prefixes = [""]
                    if parts and parts[0] not in ("src", "app"):
                        search_prefixes.insert(0, parts[0])
                        if len(parts) > 1 and parts[1] not in ("src", "app"):
                            search_prefixes.insert(0, f"{parts[0]}/{parts[1]}")

                    for pfx in search_prefixes:
                        for sub_folder in ("src", "app", ""):
                            candidate_base = os.path.normpath(
                                os.path.join(pfx, sub_folder, remainder)
                            ).replace("\\", "/")
                            candidates.extend(_generate_ts_candidates(candidate_base))

    seen: set[str] = set()
    deduped_candidates: list[str] = []
    for cand in candidates:
        if cand and cand not in seen:
            seen.add(cand)
            deduped_candidates.append(cand)

    return deduped_candidates


def extract_code_skeleton(content: str, source_path: str) -> str:
    """Extrae el esqueleto estructural (firmas, tipos, interfaces)."""
    if not content:
        return ""
    lines = content.splitlines()
    if len(lines) <= 25:
        return content

    suffix = Path(source_path).suffix.lower() if source_path else ""
    if suffix in (".prisma", ".css", ".html"):
        return content

    skeleton_lines = []
    in_function_body = False
    base_indent = 0

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        # Mantener imports, interfaces, tipos, decoradores y declaraciones
        if stripped.startswith(
            (
                "import ",
                "from ",
                "export interface ",
                "interface ",
                "export type ",
                "type ",
                "@",
                "class ",
                "export class ",
                "enum ",
                "export enum ",
                "export const ",
                "const ",
            )
        ):
            in_function_body = False
            skeleton_lines.append(line)
            continue

        if suffix == ".py":
            if stripped.startswith(("def ", "async def ", "class ")):
                skeleton_lines.append(line)
                in_function_body = True
                base_indent = indent
                continue
            if in_function_body:
                if indent <= base_indent and stripped:
                    in_function_body = False
                    skeleton_lines.append(line)
                else:
                    if stripped.startswith(('"""', "'''")):
                        skeleton_lines.append(line)
                    continue
        elif suffix in (".ts", ".js", ".tsx", ".jsx"):
            if re.search(
                r"\b(function|async function|\w+\s*\([^)]*\)\s*[:{])", stripped
            ):
                skeleton_lines.append(line)
                in_function_body = True
                continue
            if in_function_body:
                if stripped.startswith("}"):
                    in_function_body = False
                    skeleton_lines.append(line)
                continue

        if not in_function_body:
            skeleton_lines.append(line)

    res = "\n".join(skeleton_lines)
    return res if res.strip() else content


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

                                content_skeleton = extract_code_skeleton(
                                    content, matched_source_normalized
                                )

                                is_compressed = False
                                content_to_use = content_skeleton
                                if getattr(config, "COMPRESS_CODE_CONTEXT", False):
                                    content_to_use = compress_code(
                                        content_skeleton, matched_source_normalized
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

                                        content_skeleton = extract_code_skeleton(
                                            content, target_source_normalized
                                        )

                                        is_compressed = False
                                        content_to_use = content_skeleton
                                        if getattr(
                                            config,
                                            "COMPRESS_CODE_CONTEXT",
                                            False,
                                        ):
                                            content_to_use = compress_code(
                                                content_skeleton,
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
