import re
from collections.abc import Callable

from rag_local.core.config import MAX_LINES_PER_CHUNK, OVERLAP_LINES
from rag_local.core.models import Chunk, ChunkMetadata


def is_ts_only_comments_and_whitespace(text: str) -> bool:
    """Verifica si el texto de TypeScript contiene solo comentarios y espacios."""
    text_no_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text_no_comments = re.sub(r"//.*", "", text_no_comments)
    return text_no_comments.strip() == ""


def is_prisma_only_comments_and_whitespace(text: str) -> bool:
    """Verifica si el texto de Prisma contiene solo comentarios y espacios."""
    text_no_comments = re.sub(r"//.*", "", text)
    return text_no_comments.strip() == ""


def is_html_only_comments_and_whitespace(text: str) -> bool:
    """Verifica si el texto de HTML contiene solo comentarios y espacios."""
    text_no_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text_no_comments.strip() == ""


def is_css_only_comments_and_whitespace(text: str) -> bool:
    """Verifica si el texto CSS contiene solo comentarios y espacios."""
    text_no_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text_no_comments.strip() == ""


def is_file_empty_or_only_comments(lines: list[str], suffix: str) -> bool:
    """Determina si un archivo contiene solo comentarios y espacios en blanco."""
    text = "".join(lines)
    if not text.strip():
        return True
    if suffix in (".ts", ".js"):
        return is_ts_only_comments_and_whitespace(text)
    elif suffix == ".prisma":
        return is_prisma_only_comments_and_whitespace(text)
    elif suffix == ".html":
        return is_html_only_comments_and_whitespace(text)
    elif suffix == ".css":
        return is_css_only_comments_and_whitespace(text)
    return False


def chunk_flat_lines_window(
    line_tuples: list[tuple[int, str]],
    metadata_factory: Callable[[str], ChunkMetadata],
    max_lines: int = MAX_LINES_PER_CHUNK,
    overlap_lines: int = OVERLAP_LINES,
) -> list[Chunk]:
    """Divide líneas numeradas en fragmentos usando ventana deslizante."""
    total_lines = len(line_tuples)
    if total_lines == 0:
        return []

    if total_lines <= max_lines:
        text = "".join(lc for _, lc in line_tuples)
        start_line = line_tuples[0][0]
        end_line = line_tuples[-1][0]
        return [
            Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                metadata=metadata_factory(text),
            )
        ]

    chunks: list[Chunk] = []
    start = 0
    while start < total_lines:
        end = min(start + max_lines, total_lines)
        chunk_lines = line_tuples[start:end]
        text = "".join(lc for _, lc in chunk_lines)
        start_line = chunk_lines[0][0]
        end_line = chunk_lines[-1][0]

        chunks.append(
            Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                metadata=metadata_factory(text),
            )
        )
        start += max_lines - overlap_lines
        if start >= total_lines - overlap_lines:
            break

    return chunks


def extract_dependency_identifiers(
    text: str,
    excluded: set[str] | None = None,
    min_length: int = 4,
) -> list[str]:
    """Extrae identificadores de código que coincidan con nombres de variables/tipos."""
    excl = excluded or set()
    found: set[str] = set()
    for word in re.findall(r"\b[A-Za-z_]\w*\b", text):
        if len(word) >= min_length and word not in excl:
            found.add(word)
    return sorted(found)
