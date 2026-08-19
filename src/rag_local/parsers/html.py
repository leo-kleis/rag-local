import json
import re
from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK
from rag_local.core.models import Chunk, ChunkMetadata

# Patrones pre-compilados para rendimiento
# Separados por tipo de comilla para evitar desbordamiento entre atributos
_RE_CLASS_DOUBLE = re.compile(
    r'(?:className|class|\[ngClass\]|:class)\s*=\s*"([^">]+)"'
)
_RE_CLASS_SINGLE = re.compile(
    r"(?:className|class|\[ngClass\]|:class)\s*=\s*'([^'>]+)'"
)
_RE_DIRECTIVE_CLASS = re.compile(
    r"(?:\[class\.([a-zA-Z0-9_-]+)\]|class:([a-zA-Z0-9_-]+))"
)
# Elimina interpolaciones ${...} o {...} dentro de valores de atributos
_RE_INTERP = re.compile(r"\$?\{[^}]+\}")
_RE_VALID_TOKEN = re.compile(r"^[a-zA-Z_][\w-]*$")

_RE_HTML_CLASS_ATTR = re.compile(
    r'(?:className|class|\[ngClass\]|:class)\s*=\s*["\'`]?([^"\'`>]+)["\'`]'
)


def extract_html_class_parents(text: str) -> str:
    """Construye un mapa de jerarquía de clases CSS desde HTML/templates.

    Mapeo: child_class -> [parent_classes].
    Retorna un string JSON con el diccionario o '' si está vacío.
    """
    if not text or not text.strip():
        return ""
    parent_map: dict[str, set[str]] = {}
    stack: list[set[str]] = []
    for match in _RE_HTML_CLASS_ATTR.finditer(text):
        val = match.group(1).strip()
        raw_classes = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_-]*\b", val))
        if not raw_classes:
            continue
        for c in raw_classes:
            if c not in parent_map:
                parent_map[c] = set()
            for parent_set in stack:
                parent_map[c].update(parent_set)
        stack.append(raw_classes)
        if len(stack) > 6:
            stack.pop(0)
    res = {k: sorted(v) for k, v in parent_map.items() if v}
    return json.dumps(res, ensure_ascii=False) if res else ""


def extract_html_metadata(text: str) -> ChunkMetadata:
    """Extrae metadatos ricos de HTML (tags, clases, scripts, links)."""
    element_tags = re.findall(r"<([a-zA-Z0-9:-]+)", text)
    element_tags = [t for t in element_tags if t and not t.startswith("!")]

    css_classes: set[str] = set()

    # Capturar clases desde atributos con comillas dobles y simples por separado
    for match in _RE_CLASS_DOUBLE.finditer(text):
        clean_attr = _RE_INTERP.sub("", match.group(1))
        for c in clean_attr.split():
            c = c.strip()
            if c and _RE_VALID_TOKEN.match(c):
                css_classes.add(c)

    for match in _RE_CLASS_SINGLE.finditer(text):
        clean_attr = _RE_INTERP.sub("", match.group(1))
        for c in clean_attr.split():
            c = c.strip()
            if c and _RE_VALID_TOKEN.match(c):
                css_classes.add(c)

    # Directivas Angular/Svelte: [class.is-open]="val" o class:is-active={val}
    for d1, d2 in _RE_DIRECTIVE_CLASS.findall(text):
        cname = d1 or d2
        if cname:
            css_classes.add(cname)

    all_tags = sorted(set(element_tags + list(css_classes)))

    scripts = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', text, re.IGNORECASE
    )
    links = re.findall(r'<link\b[^>]*\bhref=["\']([^"\']+)["\']', text, re.IGNORECASE)
    dependencies = sorted(set(scripts + links))

    title_match = re.search(r"<title\b[^>]*>([^<]+)</title>", text, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""

    directives = sorted({t for t in element_tags if "-" in t})
    class_parents_str = extract_html_class_parents(text)

    return ChunkMetadata(
        tags=all_tags,
        dependencies=dependencies,
        title=title,
        directives=directives,
        class_parents=class_parents_str,
    )


def get_html_safe_split_points(lines: list[str]) -> list[bool]:
    """Retorna una lista de booleanos indicando si es seguro dividir

    después de cada línea.
    """
    text = "".join(lines)
    safe_points = [True] * len(lines)

    inside_comment = False
    inside_script = False
    inside_style = False
    inside_tag = False

    line_indices = []
    current_line = 0
    for char in text:
        line_indices.append(current_line)
        if char == "\n":
            current_line += 1

    i = 0
    n = len(text)
    while i < n:
        curr_line = line_indices[i]

        if (
            not inside_comment
            and not inside_script
            and not inside_style
            and not inside_tag
            and text[i : i + 4] == "<!--"
        ):
            inside_comment = True
            i += 4
            continue

        if inside_comment:
            if text[i : i + 3] == "-->":
                inside_comment = False
                i += 3
                continue
            i += 1
            continue

        # Bloques script y style
        if not inside_script and not inside_style and not inside_tag:
            if text[i : i + 7].lower() == "<script":
                inside_script = True
                inside_tag = True
                i += 7
                continue
            if text[i : i + 6].lower() == "<style":
                inside_style = True
                inside_tag = True
                i += 6
                continue

        if inside_script and text[i : i + 9].lower() == "</script>":
            inside_script = False
            i += 9
            continue

        if inside_style and text[i : i + 8].lower() == "</style>":
            inside_style = False
            i += 8
            continue

        # Etiquetas HTML
        if not inside_tag:
            if (
                text[i] == "<"
                and i + 1 < n
                and (text[i + 1].isalpha() or text[i + 1] in ("/", "!"))
            ):
                inside_tag = True
        else:
            if text[i] == ">":
                inside_tag = False

        if (
            inside_comment or inside_script or inside_style or inside_tag
        ) and curr_line < len(safe_points):
            safe_points[curr_line] = False

        i += 1

    return safe_points


_html_parser: Any = None


def get_html_parser() -> Any:
    """Obtiene o inicializa el Parser de HTML de forma perezosa."""
    global _html_parser
    if _html_parser is None:
        import tree_sitter_html
        from tree_sitter import Language, Parser

        _html_parser = Parser(Language(tree_sitter_html.language()))
    return _html_parser


def chunk_html(lines: list[str]) -> list[Chunk]:
    """Divide un archivo HTML en fragmentos basados en su estructura

    usando tree-sitter.
    """
    total_lines = len(lines)
    if total_lines == 0:
        return []

    code = "".join(lines)
    parser = get_html_parser()
    tree = parser.parse(bytes(code, "utf8"))
    root_node = tree.root_node

    def group_chunks(group_nodes) -> list[Chunk]:
        if not group_nodes:
            return []
        start_line = group_nodes[0].start_point[0] + 1
        end_line = group_nodes[-1].end_point[0] + 1
        start_line = max(1, min(start_line, len(lines)))
        end_line = max(1, min(end_line, len(lines)))
        text = "".join(lines[start_line - 1 : end_line])
        return [
            Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                metadata=extract_html_metadata(text),
            )
        ]

    def chunk_node(node) -> list[Chunk]:
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        start_line = max(1, min(start_line, len(lines)))
        end_line = max(1, min(end_line, len(lines)))

        element_children = [c for c in node.children if c.type == "element"]
        if (end_line - start_line + 1) <= MAX_LINES_PER_CHUNK or not element_children:
            node_text = "".join(lines[start_line - 1 : end_line])
            return [
                Chunk(
                    text=node_text,
                    start_line=start_line,
                    end_line=end_line,
                    metadata=extract_html_metadata(node_text),
                )
            ]

        chunks = []
        current_group = []
        current_lines = 0

        for child in node.children:
            child_start = child.start_point[0] + 1
            child_end = child.end_point[0] + 1
            child_start = max(1, min(child_start, len(lines)))
            child_end = max(1, min(child_end, len(lines)))
            child_len = child_end - child_start + 1

            if child_len > MAX_LINES_PER_CHUNK:
                if current_group:
                    chunks.extend(group_chunks(current_group))
                    current_group = []
                    current_lines = 0
                chunks.extend(chunk_node(child))
            else:
                if current_lines + child_len > MAX_LINES_PER_CHUNK:
                    if current_group:
                        chunks.extend(group_chunks(current_group))
                    current_group = [child]
                    current_lines = child_len
                else:
                    current_group.append(child)
                    current_lines += child_len

        if current_group:
            chunks.extend(group_chunks(current_group))
        return chunks

    chunks = chunk_node(root_node)
    if not chunks:
        # Fallback to single chunk of the whole file
        text = "".join(lines)
        chunks = [
            Chunk(
                text=text,
                start_line=1,
                end_line=len(lines),
                metadata=extract_html_metadata(text),
            )
        ]
    return chunks
