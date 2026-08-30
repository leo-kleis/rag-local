import contextlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.config import (
    MAX_LINES_PER_CHUNK,
    MAX_TOKENS_PER_CHUNK,
    OVERLAP_LINES,
    OVERLAP_TOKENS,
)
from rag_local.core.models import Chunk, ChunkMetadata

_tokenizer_instance: Any = None
_tokenizer_initialized: bool = False


def get_code_tokenizer() -> Any:
    """Obtiene o inicializa el tokenizer del modelo de código de forma perezosa."""
    global _tokenizer_instance, _tokenizer_initialized
    if _tokenizer_initialized:
        return _tokenizer_instance

    _tokenizer_initialized = True
    with contextlib.suppress(Exception):
        from tokenizers import Tokenizer

        # 1. Buscar en cache de modelos del daemon
        emb_repo = getattr(
            config, "ONNX_EMBEDDING_MODEL", "jinaai/jina-code-embeddings-0.5b"
        )
        repo_name = Path(emb_repo).name
        local_tok = config.DAEMON_CACHE_DIR / "models" / repo_name / "tokenizer.json"
        if local_tok.is_file():
            _tokenizer_instance = Tokenizer.from_file(str(local_tok))
            return _tokenizer_instance

        # 2. Buscar en cache de Hugging Face
        from huggingface_hub import hf_hub_download

        with contextlib.suppress(Exception):
            cached_path = hf_hub_download(
                repo_id=emb_repo,
                filename="tokenizer.json",
                local_files_only=True,
            )
            if cached_path and Path(cached_path).is_file():
                _tokenizer_instance = Tokenizer.from_file(cached_path)
                return _tokenizer_instance

    return _tokenizer_instance


def count_code_tokens(text: str) -> int:
    """Calcula el número de tokens para un fragmento de texto."""
    if not text:
        return 0

    tok = get_code_tokenizer()
    if tok is not None:
        with contextlib.suppress(Exception):
            return len(tok.encode(text, add_special_tokens=False).ids)

    # Heurística BPE estándar: ~3.6 caracteres por token en código fuente
    return max(1, int(len(text) / 3.6))


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
    max_tokens: int = MAX_TOKENS_PER_CHUNK,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    """Divide líneas numeradas en fragmentos usando control de presupuesto de tokens."""
    total_lines = len(line_tuples)
    if total_lines == 0:
        return []

    full_text = "".join(lc for _, lc in line_tuples)
    total_tokens = count_code_tokens(full_text)

    if total_lines <= max_lines and total_tokens <= max_tokens:
        start_line = line_tuples[0][0]
        end_line = line_tuples[-1][0]
        return [
            Chunk(
                text=full_text,
                start_line=start_line,
                end_line=end_line,
                metadata=metadata_factory(full_text),
            )
        ]

    chunks: list[Chunk] = []
    start = 0

    while start < total_lines:
        # Construir ventana que no exceda max_tokens ni max_lines * 2
        current_lines = []
        current_text_parts = []
        current_tokens = 0
        idx = start

        while idx < total_lines:
            _ln_num, ln_txt = line_tuples[idx]
            line_toks = count_code_tokens(ln_txt)
            if current_lines and (
                current_tokens + line_toks > max_tokens
                or len(current_lines) >= max_lines
            ):
                break
            current_lines.append(line_tuples[idx])
            current_text_parts.append(ln_txt)
            current_tokens += line_toks
            idx += 1

        if not current_lines:
            current_lines.append(line_tuples[start])
            current_text_parts.append(line_tuples[start][1])
            idx = start + 1

        chunk_text = "".join(current_text_parts)
        start_line = current_lines[0][0]
        end_line = current_lines[-1][0]

        chunks.append(
            Chunk(
                text=chunk_text,
                start_line=start_line,
                end_line=end_line,
                metadata=metadata_factory(chunk_text),
            )
        )

        if idx >= total_lines:
            break

        # Calcular retroceso de solapamiento en tokens
        step_back = 0
        back_tokens = 0
        for back_idx in range(len(current_lines) - 1, -1, -1):
            line_tok = count_code_tokens(current_lines[back_idx][1])
            if back_tokens + line_tok > overlap_tokens:
                break
            back_tokens += line_tok
            step_back += 1

        # Avanzar al menos 1 línea
        advance = max(1, len(current_lines) - max(1, min(step_back, overlap_lines)))
        start += advance

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
