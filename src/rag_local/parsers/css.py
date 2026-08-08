import functools
import json
import re
from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK, OVERLAP_LINES
from rag_local.core.logging import logger
from rag_local.core.models import Chunk, ChunkMetadata

# Patrones pre-compilados para rendimiento
_RE_BLOCK_COMMENTS = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_URL_DATA = re.compile(r"url\([^)]+\)")
_RE_CSS_CLASSES = re.compile(
    r"(?<![\d.])\.([a-zA-Z_][a-zA-Z0-9_-]*)(?=[ \t\r\n.:,>+~\[]|$)"
)
_RE_CSS_VARIABLES = re.compile(r"(--[a-zA-Z0-9_-]+)")
_RE_CSS_DIRECTIVES = re.compile(r"(@[a-zA-Z0-9_-]+(?:\s+[\w-]+)?)")

_css_parser: Any = None


def get_css_parser() -> Any:
    """Obtiene o inicializa el Parser de CSS usando tree-sitter-css."""
    global _css_parser
    if _css_parser is None:
        try:
            import tree_sitter_css
            from tree_sitter import Language, Parser

            _css_parser = Parser(Language(tree_sitter_css.language()))
        except Exception:
            _css_parser = False
    return _css_parser


@functools.lru_cache(maxsize=256)
def _parse_css_rules_cached(code: str) -> tuple[dict[str, Any], ...]:
    """Implementación cachead de parse_css_rules returning tuples imperturbables."""
    if not code or not code.strip():
        return ()

    parser = get_css_parser()
    rules: list[dict[str, Any]] = []

    if parser:
        try:
            code_bytes = code.encode("utf-8")
            tree = parser.parse(code_bytes)
            root = tree.root_node

            def _traverse(node: Any) -> None:
                if node.type == "rule_set":
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    selectors_str = ""
                    properties: dict[str, str] = {}

                    for child in node.children:
                        if child.type in (
                            "selectors",
                            "selector_group",
                            "feature_selector",
                            "class_selector",
                        ):
                            selectors_str = (
                                code_bytes[child.start_byte : child.end_byte]
                                .decode("utf-8", errors="replace")
                                .strip()
                            )
                        elif child.type == "block":
                            for decl in child.children:
                                if decl.type == "declaration":
                                    decl_text = (
                                        code_bytes[decl.start_byte : decl.end_byte]
                                        .decode("utf-8", errors="replace")
                                        .strip()
                                    )
                                    if ":" in decl_text:
                                        parts = decl_text.split(":", 1)
                                        prop_name = parts[0].strip().lower()
                                        prop_val = parts[1].rstrip(";").strip()
                                        if prop_name and prop_val:
                                            properties[prop_name] = prop_val

                    if not selectors_str:
                        # Extraer texto antes del bloque '{'
                        block_child = next(
                            (c for c in node.children if c.type == "block"), None
                        )
                        if block_child:
                            selectors_str = (
                                code_bytes[node.start_byte : block_child.start_byte]
                                .decode("utf-8", errors="replace")
                                .strip()
                            )
                        else:
                            selectors_str = (
                                code_bytes[node.start_byte : node.end_byte]
                                .decode("utf-8", errors="replace")
                                .split("{")[0]
                                .strip()
                            )

                    # Capturar directiva @media o @supports contenedora si existe
                    current_media = ""
                    p = node.parent
                    while p:
                        if p.type in (
                            "media_statement",
                            "supports_statement",
                            "at_rule",
                        ):
                            block_child = next(
                                (c for c in p.children if c.type == "block"), None
                            )
                            if block_child:
                                current_media = (
                                    code_bytes[p.start_byte : block_child.start_byte]
                                    .decode("utf-8", errors="replace")
                                    .strip()
                                )
                                current_media = re.sub(r"\s+", " ", current_media)
                            break
                        p = p.parent

                    # Limpiar saltos de línea innecesarios en selectores
                    selectors_str = re.sub(r"\s+", " ", selectors_str)
                    rule_classes = sorted(set(_RE_CSS_CLASSES.findall(selectors_str)))

                    if selectors_str and (properties or rule_classes):
                        rules.append(
                            {
                                "selector": selectors_str,
                                "classes": rule_classes,
                                "start_line": start_line,
                                "end_line": end_line,
                                "properties": properties,
                                "media_query": current_media,
                            }
                        )

                for child in node.children:
                    _traverse(child)

            _traverse(root)
            if rules:
                return tuple(rules)
        except Exception as ex:
            logger.warning(f"Error parseando CSS con tree-sitter: {ex}")

    # Fallback con expresiones regulares si tree-sitter falla
    rule_regex = re.compile(r"([^{]+)\{([^}]+)\}", re.DOTALL)
    for match in rule_regex.finditer(code):
        sel_text = match.group(1).strip()
        body_text = match.group(2).strip()
        start_line = code[: match.start()].count("\n") + 1
        end_line = code[: match.end()].count("\n") + 1

        rule_classes = sorted(set(_RE_CSS_CLASSES.findall(sel_text)))
        props: dict[str, str] = {}
        for decl in body_text.split(";"):
            decl = decl.strip()
            if ":" in decl:
                k, v = decl.split(":", 1)
                props[k.strip().lower()] = v.strip()

        if sel_text and (props or rule_classes):
            media_match = re.search(r"@media[^{]+", code[: match.start()])
            current_media = media_match.group(0).strip() if media_match else ""
            rules.append(
                {
                    "selector": sel_text,
                    "classes": rule_classes,
                    "start_line": start_line,
                    "end_line": end_line,
                    "properties": props,
                    "media_query": current_media,
                }
            )

    return tuple(rules)


def parse_css_rules(code: str) -> list[dict[str, Any]]:
    """Parsea un texto CSS retornando reglas estructuradas con selectores,

    propiedades y rango de líneas. Utiliza caché para evitar repeticiones de parsing.
    """
    cached_rules = _parse_css_rules_cached(code)
    # Retornar una copia mutable (lista de dicts)
    return [dict(r) for r in cached_rules]


def extract_css_selectors_and_vars(text: str) -> tuple[list[str], list[str], list[str]]:
    """Extrae clases (.clase) y variables (--var) del contenido CSS."""
    if not text or not text.strip():
        return [], [], []

    text_clean = _RE_BLOCK_COMMENTS.sub("", text)
    text_clean = _RE_URL_DATA.sub("", text_clean)

    classes = sorted(set(_RE_CSS_CLASSES.findall(text_clean)))
    variables = sorted(set(_RE_CSS_VARIABLES.findall(text_clean)))
    directives = sorted({m.strip() for m in _RE_CSS_DIRECTIVES.findall(text_clean)})

    return classes, variables, directives


def _count_css_lines_code(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(("/*", "*", "//"))
    )


def chunk_css(lines: list[str]) -> list[Chunk]:
    """Divide un archivo CSS en fragmentos estructurados con metadatos."""
    total_lines = len(lines)
    if total_lines == 0:
        return []

    text = "".join(lines)
    classes, variables, directives = extract_css_selectors_and_vars(text)
    parsed_rules = parse_css_rules(text)
    serialized_rules = json.dumps(parsed_rules, ensure_ascii=False)
    file_lines_code = _count_css_lines_code(text)

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
                    lines_code=file_lines_code,
                    css_rules=serialized_rules,
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
        c_lines_code = _count_css_lines_code(chunk_text)

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
                    lines_code=c_lines_code,
                    css_rules=serialized_rules,
                ),
            )
        )

        if end == total_lines:
            break
        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES

    return chunks
