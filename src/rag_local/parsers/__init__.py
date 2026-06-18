import re
from pathlib import Path

from rag_local.core.config import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE_BYTES,
    MAX_LINES_PER_CHUNK,
)
from rag_local.core.logging import logger
from rag_local.core.models import Chunk, ChunkMetadata
from rag_local.parsers.common import is_file_empty_or_only_comments
from rag_local.parsers.html import chunk_html, extract_html_metadata
from rag_local.parsers.prisma import chunk_prisma
from rag_local.parsers.python import chunk_python
from rag_local.parsers.typescript import (
    chunk_typescript,
    clean_typescript_code,
    extract_ts_methods,
    get_class_dependencies,
    parse_ts_imports,
)


def chunk_small_file(lines: list[str], suffix: str) -> list[Chunk]:
    """Genera un único fragmento para archivos pequeños con

    sus metadatos correspondientes.
    """
    text = "".join(lines)
    total_lines = len(lines)
    if total_lines == 0:
        return []

    metadata_dict = {
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
        metadata_dict["imports"] = imports_list

        class_names = re.findall(r"\bclass\s+(\w+)", text)
        metadata_dict["class_name"] = ",".join(class_names) if class_names else ""

        clean_text = clean_typescript_code(text)
        clean_lines = clean_text.splitlines(keepends=True)
        while len(clean_lines) < len(lines):
            clean_lines.append("")

        class_lines = [(i + 1, lines[i]) for i in range(len(lines))]
        clean_class_lines = [clean_lines[i] for i in range(len(lines))]
        extracted_methods = extract_ts_methods(class_lines, clean_class_lines)
        metadata_dict["method_name"] = (
            ",".join(extracted_methods) if extracted_methods else ""
        )

        metadata_dict["dependencies"] = get_class_dependencies(text, local_imports)

    elif suffix == ".prisma":
        models = re.findall(r"^(?:model|enum)\s+(\w+)", text, re.MULTILINE)
        metadata_dict["models"] = models
        if models:
            metadata_dict["class_name"] = models[0]
            match = re.search(
                r"^(model|enum|datasource|generator|type)\s+(\w+)", text, re.MULTILINE
            )
            if match:
                metadata_dict["type"] = match.group(1)

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
        metadata_dict["dependencies"] = sorted(dependencies_set)

    elif suffix == ".html":
        html_meta = extract_html_metadata(text)
        metadata_dict.update(html_meta.model_dump())

    elif suffix == ".py":
        import_re = re.compile(
            r"^\s*(?:import\s+[\w\s,]+|from\s+[\w\.]+\s+import\s+[\w\s,\*\(\)]+)"
        )
        global_imports = [
            line.strip() for line in lines if import_re.match(line.strip())
        ]
        metadata_dict["imports"] = global_imports

        class_names = re.findall(r"^\s*class\s+(\w+)", text, re.MULTILINE)
        metadata_dict["class_name"] = ",".join(class_names) if class_names else ""

        method_names = re.findall(r"^\s*def\s+(\w+)", text, re.MULTILINE)
        metadata_dict["method_name"] = ",".join(method_names) if method_names else ""

    return [
        Chunk(
            text=text,
            start_line=1,
            end_line=total_lines,
            metadata=ChunkMetadata(**metadata_dict),
        )
    ]


def chunk_file(file_path: Path) -> list[Chunk]:
    """Divide un archivo en fragmentos (chunks) de líneas que se solapan."""
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
            logger.warning(
                f"El archivo {file_path} supera el limite maximo "
                f"({MAX_FILE_SIZE_BYTES} bytes) y sera ignorado."
            )
            return []
    except Exception as e:
        logger.error(f"Error al verificar tamaño de {file_path}: {e}")
        return []

    chunks: list[Chunk] = []
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
    elif suffix == ".py":
        return chunk_python(lines)

    total_lines = len(lines)
    if total_lines == 0:
        return []

    if total_lines <= MAX_LINES_PER_CHUNK:
        text = "".join(lines)
        chunks.append(
            Chunk(
                text=text,
                start_line=1,
                end_line=total_lines,
                metadata=ChunkMetadata(
                    class_name="",
                    method_name="",
                    imports=[],
                    dependencies=[],
                    tags=[],
                    title="",
                    type="",
                ),
            )
        )
        return chunks

    from rag_local.core.config import OVERLAP_LINES

    start = 0
    while start < total_lines:
        end = min(start + MAX_LINES_PER_CHUNK, total_lines)
        chunk_lines = lines[start:end]
        text = "".join(chunk_lines)

        chunks.append(
            Chunk(
                text=text,
                start_line=start + 1,
                end_line=end,
                metadata=ChunkMetadata(
                    class_name="",
                    method_name="",
                    imports=[],
                    dependencies=[],
                    tags=[],
                    title="",
                    type="",
                ),
            )
        )

        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES
        if start >= total_lines - OVERLAP_LINES:
            break

    return chunks
