import re

from rag_local.core.config import MAX_LINES_PER_CHUNK, OVERLAP_LINES
from rag_local.core.models import Chunk, ChunkMetadata

# Patrones pre-compilados para rendimiento
_RE_BLOCK_COMMENTS = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_URL_DATA = re.compile(r"url\([^)]+\)")
_RE_CSS_CLASSES = re.compile(
    r"(?<![\d.])\.([a-zA-Z_][a-zA-Z0-9_-]*)(?=[ \t\r\n.:,>+~\[]|$)"
)
_RE_CSS_VARIABLES = re.compile(r"(--[a-zA-Z0-9_-]+)")
# Captura solo el nombre de la directiva y su argumento principal (sin trailing spaces)
_RE_CSS_DIRECTIVES = re.compile(r"(@[a-zA-Z0-9_-]+(?:\s+[\w-]+)?)")


def extract_css_selectors_and_vars(text: str) -> tuple[list[str], list[str], list[str]]:
    """Extrae clases (.clase) y variables (--var) del contenido CSS."""
    if not text or not text.strip():
        return [], [], []

    text_clean = _RE_BLOCK_COMMENTS.sub("", text)
    text_clean = _RE_URL_DATA.sub("", text_clean)

    # Clases CSS: .nombre-clase (evitando números decimales como 1.2fr o 0.5em)
    classes = sorted(set(_RE_CSS_CLASSES.findall(text_clean)))
    variables = sorted(set(_RE_CSS_VARIABLES.findall(text_clean)))
    directives = sorted({m.strip() for m in _RE_CSS_DIRECTIVES.findall(text_clean)})

    return classes, variables, directives


def chunk_css(lines: list[str]) -> list[Chunk]:
    """Divide un archivo CSS en fragmentos estructurados con metadatos."""
    total_lines = len(lines)
    if total_lines == 0:
        return []

    text = "".join(lines)
    classes, variables, directives = extract_css_selectors_and_vars(text)

    if total_lines <= MAX_LINES_PER_CHUNK:
        return [
            Chunk(
                text=text,
                start_line=1,
                end_line=total_lines,
                metadata=ChunkMetadata(
                    class_name="",
                    method_name="",
                    imports=[],
                    dependencies=variables,
                    tags=classes,
                    title="CSS Rules",
                    type="css",
                    directives=directives,
                ),
            )
        ]

    chunks: list[Chunk] = []
    start = 0
    while start < total_lines:
        end = min(start + MAX_LINES_PER_CHUNK, total_lines)
        chunk_lines = lines[start:end]
        chunk_text = "".join(chunk_lines)

        c_classes, c_vars, c_dirs = extract_css_selectors_and_vars(chunk_text)

        chunks.append(
            Chunk(
                text=chunk_text,
                start_line=start + 1,
                end_line=end,
                metadata=ChunkMetadata(
                    class_name="",
                    method_name="",
                    imports=[],
                    dependencies=c_vars,
                    tags=c_classes,
                    title="CSS Block",
                    type="css",
                    directives=c_dirs,
                ),
            )
        )

        if end == total_lines:
            break
        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES

    return chunks
