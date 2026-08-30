import json
import re
from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK, MAX_TOKENS_PER_CHUNK
from rag_local.core.models import Chunk, ChunkMetadata
from rag_local.parsers.common import count_code_tokens

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
_RE_HTML_OPENING_TAG = re.compile(r"<([a-zA-Z0-9:-]+)\b([^>]*)>", re.DOTALL)
_RE_ANGULAR_FOR = re.compile(r"\*ngFor\b")
_RE_ANGULAR_FOR_BLOCK = re.compile(r"@for\s*\([^)]+\)")
_RE_ANGULAR_TEXT_BIND = re.compile(r"\[(?:innerText|textContent|innerHTML)\]\s*=")
_RE_ANGULAR_INTERP = re.compile(r"\{\{[^}]+\}\}")
_RE_JS_INTERP = re.compile(r"\$\{[^}]+\}")
_RE_COLLECTION_SIGNAL = re.compile(r"\.(?:map|flatMap)\s*\(|for\s*\([^)]+of\s+")


_RE_HTML_TAG = re.compile(
    r"<\s*(/)?\s*([a-zA-Z0-9:-]+|\$\{[^}]+\})(?=[ \t\r\n>/]|$)([^>]*)>",
    re.DOTALL,
)
_RE_DECL_COMP = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:function|class|const|let|var)\s+([A-Z][a-zA-Z0-9_]*)"
)
_RE_HTML_STYLE_ATTR = re.compile(r'style\s*=\s*["\']([^"\']+)["\']', re.DOTALL)
_RE_PORTAL_PATTERNS = [
    re.compile(
        r"render\s*\(\s*[^,]+,\s*(?:document\.body|portalRoot|portalContainer|portal|root)\b",
        re.I,
    ),
    re.compile(r"createPortal\s*\(", re.I),
    re.compile(r"document\.body\.appendChild\s*\(", re.I),
    re.compile(r"id=['\"](?:[a-zA-Z0-9_-]+-)?portal(?:-[a-zA-Z0-9_-]+)?['\"]", re.I),
    re.compile(r"['\"]#?(?:[a-zA-Z0-9_-]+-)?portal(?:-root|-container)?['\"]", re.I),
]
VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def parse_inline_styles(style_str: str) -> dict[str, str]:
    """Parsea una cadena de estilo en línea CSS en un diccionario de propiedades."""
    props: dict[str, str] = {}
    if not style_str:
        return props
    for decl in style_str.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            k_clean = k.strip().lower()
            v_clean = v.strip()
            if k_clean and v_clean:
                props[k_clean] = v_clean
    return props


def extract_html_class_parents(text: str) -> str:
    """Extrae jerarquía de ancestros y metadatos en HTML, Angular y templates JS.

    Utiliza una pila DOM balanceada para registrar ancestros exactos y captura
    estilos en línea de contenedores intermedios.
    """
    if not text or not text.strip():
        return ""

    class_data: dict[str, dict[str, Any]] = {}
    inline_rules: list[dict[str, Any]] = []

    declared_components = [c for c in _RE_DECL_COMP.findall(text) if not c.isupper()]
    initial_classes = {f"[COMP]{c}" for c in declared_components}
    stack: list[dict[str, Any]] = (
        [
            {
                "tag": "root",
                "raw_tag": "root",
                "classes": initial_classes,
                "styles": {},
                "line": 1,
            }
        ]
        if initial_classes
        else []
    )

    in_for_block = bool(_RE_ANGULAR_FOR_BLOCK.search(text)) or bool(
        _RE_COLLECTION_SIGNAL.search(text)
    )

    min_stack_len = 1 if initial_classes else 0

    for match in _RE_HTML_TAG.finditer(text):
        is_closing = bool(match.group(1))
        raw_tag_name = match.group(2).strip()
        tag_name = raw_tag_name.lower()
        attrs = match.group(3) or ""
        is_self_closing = attrs.strip().endswith("/")
        line_num = text[: match.start()].count("\n") + 1

        if is_closing:
            if len(stack) > min_stack_len:
                pop_idx = -1
                for i in range(len(stack) - 1, min_stack_len - 1, -1):
                    if (
                        stack[i]["tag"] == tag_name
                        or stack[i]["raw_tag"] == raw_tag_name
                    ):
                        pop_idx = i
                        break
                if pop_idx != -1:
                    stack = stack[:pop_idx]
                else:
                    stack.pop()
            continue

        raw_classes: set[str] = set()
        for c_match in _RE_HTML_CLASS_ATTR.finditer(attrs):
            val = c_match.group(1).strip()
            clean_val = _RE_INTERP.sub("", val)
            tokens = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_-]*\b", clean_val)
            raw_classes.update(tokens)

        for d1, d2 in _RE_DIRECTIVE_CLASS.findall(attrs):
            cname = d1 or d2
            if cname:
                raw_classes.add(cname)

        comp_id = None
        if raw_tag_name.startswith("${") and raw_tag_name.endswith("}"):
            inner = raw_tag_name[2:-1].strip()
            if inner and inner[0].isupper() and not inner.isupper():
                comp_id = f"[COMP]{inner}"
        elif (
            raw_tag_name and raw_tag_name[0].isupper() and not raw_tag_name.isupper()
        ) or ("-" in raw_tag_name and not raw_tag_name.startswith(("<!--", "<!"))):
            comp_id = f"[COMP]{raw_tag_name}"

        if comp_id:
            raw_classes.add(comp_id)

        style_match = _RE_HTML_STYLE_ATTR.search(attrs)
        style_dict = parse_inline_styles(style_match.group(1)) if style_match else {}

        all_parent_classes: set[str] = set()
        for frame in stack:
            all_parent_classes.update(frame["classes"])

        is_collection = in_for_block or bool(_RE_ANGULAR_FOR.search(attrs))
        has_dynamic = bool(_RE_ANGULAR_TEXT_BIND.search(attrs))
        tag_end = match.end()
        next_chunk = text[tag_end : tag_end + 300]
        if _RE_ANGULAR_INTERP.search(next_chunk) or _RE_JS_INTERP.search(next_chunk):
            has_dynamic = True

        is_native_tag = raw_tag_name[0].islower() and not raw_tag_name.startswith("$")

        if style_dict:
            inline_rules.append(
                {
                    "line": line_num,
                    "tag": tag_name,
                    "classes": sorted(raw_classes),
                    "properties": style_dict,
                    "parents": sorted(all_parent_classes),
                }
            )

        for c in raw_classes:
            if c not in class_data:
                class_data[c] = {
                    "parents": set(),
                    "has_dynamic_text": False,
                    "is_collection": False,
                    "own_tags": set(),
                    "inline_styles": [],
                }
            class_data[c]["parents"].update(all_parent_classes)
            if has_dynamic:
                class_data[c]["has_dynamic_text"] = True
            if is_collection:
                class_data[c]["is_collection"] = True
            if is_native_tag:
                class_data[c]["own_tags"].add(tag_name)
            if style_dict:
                class_data[c]["inline_styles"].append(style_dict)

        if not is_self_closing and tag_name not in VOID_HTML_TAGS:
            stack.append(
                {
                    "tag": tag_name,
                    "raw_tag": raw_tag_name,
                    "classes": raw_classes,
                    "styles": style_dict,
                    "line": line_num,
                }
            )

    if not class_data and not inline_rules:
        return ""

    res: dict[str, Any] = {
        c: {
            "parents": sorted(info["parents"]),
            "has_dynamic_text": info["has_dynamic_text"],
            "is_collection": info["is_collection"],
            "own_tags": sorted(info["own_tags"]),
            "inline_styles": info.get("inline_styles", []),
        }
        for c, info in class_data.items()
    }
    if inline_rules:
        res["__inline_rules__"] = inline_rules

    if any(p.search(text) for p in _RE_PORTAL_PATTERNS):
        portal_classes = [
            c
            for c in class_data
            if not c.startswith("__") and not c.startswith("[COMP]")
        ]
        if portal_classes:
            res["__portal_classes__"] = portal_classes

    return json.dumps(res, ensure_ascii=False)


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

        node_text = "".join(lines[start_line - 1 : end_line])
        element_children = [c for c in node.children if c.type == "element"]
        if (
            (end_line - start_line + 1) <= MAX_LINES_PER_CHUNK
            and count_code_tokens(node_text) <= MAX_TOKENS_PER_CHUNK
        ) or not element_children:
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
