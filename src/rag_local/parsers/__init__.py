import re
from pathlib import Path
from typing import Any

from rag_local.core.config import ALLOWED_EXTENSIONS, MAX_LINES_PER_CHUNK
from rag_local.core.logging import logger
from rag_local.parsers.common import is_file_empty_or_only_comments
from rag_local.parsers.html import chunk_html, extract_html_metadata
from rag_local.parsers.prisma import chunk_prisma
from rag_local.parsers.typescript import (
    chunk_typescript,
    clean_typescript_code,
    extract_ts_methods,
    get_class_dependencies,
    parse_ts_imports,
)


def chunk_small_file(lines: list[str], suffix: str) -> list[dict[str, Any]]:
    """Genera un único fragmento para archivos pequeños con

    sus metadatos correspondientes.
    """
    text = "".join(lines)
    total_lines = len(lines)
    if total_lines == 0:
        return []

    metadata: dict[str, Any] = {
        "class_name": "",
        "method_name": "",
        "imports": [],
        "dependencies": [],
        "tags": [],
        "title": "",
        "type": "",
        "models": [],
        "directives": [],
    }

    if suffix == ".ts":
        _, imports_list, _ = parse_ts_imports(lines)
        local_imports = [imp for imp in imports_list if imp.startswith(".")]
        metadata["imports"] = imports_list

        class_names = re.findall(r"\bclass\s+(\w+)", text)
        metadata["class_name"] = ",".join(class_names) if class_names else ""

        clean_text = clean_typescript_code(text)
        clean_lines = clean_text.splitlines(keepends=True)
        while len(clean_lines) < len(lines):
            clean_lines.append("")

        class_lines = [(i + 1, lines[i]) for i in range(len(lines))]
        clean_class_lines = [clean_lines[i] for i in range(len(lines))]
        extracted_methods = extract_ts_methods(class_lines, clean_class_lines)
        metadata["method_name"] = (
            ",".join(extracted_methods) if extracted_methods else ""
        )

        metadata["dependencies"] = get_class_dependencies(text, local_imports)

    elif suffix == ".prisma":
        models = re.findall(r"^(?:model|enum)\s+(\w+)", text, re.MULTILINE)
        metadata["models"] = models
        if models:
            metadata["class_name"] = models[0]
            match = re.search(
                r"^(model|enum|datasource|generator|type)\s+(\w+)", text, re.MULTILINE
            )
            if match:
                metadata["type"] = match.group(1)

        prisma_primitives = {
            "String",
            "Int",
            "Boolean",
            "DateTime",
            "Json",
            "Decimal",
            "Float",
            "Bytes",
            "Unsupported",
        }
        dependencies_set = set()
        words = re.findall(r"\b[A-Z]\w*\b", text)
        for w in words:
            if w not in models and w not in prisma_primitives:
                dependencies_set.add(w)
        metadata["dependencies"] = sorted(dependencies_set)

    elif suffix == ".html":
        metadata.update(extract_html_metadata(text))

    return [
        {
            "text": text,
            "start_line": 1,
            "end_line": total_lines,
            "metadata": metadata,
        }
    ]


def chunk_file(file_path: Path) -> list[dict[str, Any]]:
    """Divide un archivo en fragmentos (chunks) de líneas que se solapan."""
    chunks: list[dict[str, Any]] = []
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Error al leer el archivo {file_path}: {e}")
        return []

    suffix = file_path.suffix.lower()

    # Si está completamente vacío o contiene solo comentarios
    # y/o espacios (y es corto), retornar []
    if not "".join(lines).strip():
        return []
    if len(lines) < 20 and is_file_empty_or_only_comments(lines, suffix):
        return []

    # Optimización: si el archivo es pequeño, procesarlo en un único bloque
    if len(lines) <= MAX_LINES_PER_CHUNK and suffix in ALLOWED_EXTENSIONS:
        return chunk_small_file(lines, suffix)

    if suffix == ".ts":
        return chunk_typescript(lines)
    elif suffix == ".prisma":
        return chunk_prisma(lines)
    elif suffix == ".html":
        return chunk_html(lines)

    total_lines = len(lines)
    if total_lines == 0:
        return []

    if total_lines <= MAX_LINES_PER_CHUNK:
        text = "".join(lines)
        chunks.append(
            {
                "text": text,
                "start_line": 1,
                "end_line": total_lines,
                "metadata": {
                    "class_name": "",
                    "method_name": "",
                    "imports": [],
                    "dependencies": [],
                    "tags": [],
                    "title": "",
                    "type": "",
                },
            }
        )
        return chunks

    from rag_local.core.config import OVERLAP_LINES

    start = 0
    while start < total_lines:
        end = min(start + MAX_LINES_PER_CHUNK, total_lines)
        chunk_lines = lines[start:end]
        text = "".join(chunk_lines)

        chunks.append(
            {
                "text": text,
                "start_line": start + 1,
                "end_line": end,
                "metadata": {
                    "class_name": "",
                    "method_name": "",
                    "imports": [],
                    "dependencies": [],
                    "tags": [],
                    "title": "",
                    "type": "",
                },
            }
        )

        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES
        if start >= total_lines - OVERLAP_LINES:
            break

    return chunks
