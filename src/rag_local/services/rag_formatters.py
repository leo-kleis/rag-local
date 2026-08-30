import html
import re
import unicodedata
from pathlib import Path
from typing import Any


def compress_code(text: str, file_path: str) -> str:
    """Comprime el código fuente eliminando comentarios irrelevantes.

    Conserva directivas de compilador y linters críticas, y remueve líneas
    vacías extras y bloques de comentarios multilínea.
    """
    if not text:
        return ""

    suffix = Path(file_path).suffix.lower() if file_path else ""
    lines = text.splitlines()
    compressed_lines = []

    ts_directive_pat = re.compile(r"^\s*//\s*@(ts-|eslint-|ng)")
    py_directive_pat = re.compile(r"^\s*#\s*(type:|pylint:|coding:)")

    in_multiline_comment = False
    multiline_close = ""

    for line in lines:
        stripped = line.strip()

        if in_multiline_comment:
            if multiline_close in stripped:
                in_multiline_comment = False
            continue

        if suffix in (".ts", ".js", ".tsx", ".prisma", ".css"):
            if stripped.startswith("//") and not ts_directive_pat.match(line):
                continue
            if stripped.startswith("/*"):
                if "*/" not in stripped[2:]:
                    in_multiline_comment = True
                    multiline_close = "*/"
                continue
        elif suffix in (".html", ".htm"):
            if stripped.startswith("<!--"):
                if "-->" not in stripped[4:]:
                    in_multiline_comment = True
                    multiline_close = "-->"
                continue
        elif suffix == ".py":
            if stripped.startswith("#") and not py_directive_pat.match(line):
                continue

        compressed_lines.append(line.rstrip())

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
    clean_chars = []
    for c in text:
        cat = unicodedata.category(c)
        if cat in ("Cc", "Cf") and c not in ("\n", "\r", "\t"):
            continue
        clean_chars.append(c)
    clean_text = "".join(clean_chars)

    escaped = html.escape(clean_text, quote=True)
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


def truncate_xml_safe(context: str, max_chars: int) -> str:
    """Trunca el contexto XML sin romper etiquetas abiertas.

    Busca el último cierre de tag completo antes del límite.
    """
    if len(context) <= max_chars:
        return context

    truncated = context[:max_chars]
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
