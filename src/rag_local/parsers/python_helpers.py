import re
from typing import Any

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Query

_py_lang: Language | None = None
_py_parser: Parser | None = None

# Consultas Tree-sitter pre-compiladas
_query_docstring: Query | None = None
_query_signature: Query | None = None
_query_class_fields: Query | None = None
_query_calls: Query | None = None


def get_python_language() -> Language:
    """Obtiene o inicializa el objeto Language de Python de forma perezosa."""
    global _py_lang
    if _py_lang is None:
        _py_lang = Language(tree_sitter_python.language())
    return _py_lang


def get_python_parser() -> Parser:
    """Obtiene o inicializa el Parser de Python de forma perezosa."""
    global _py_parser
    if _py_parser is None:
        lang = get_python_language()
        _py_parser = Parser(lang)
    return _py_parser


_RE_PY_EMIT = re.compile(
    r"""\b(?:socketio|sio|emitter|events|event_bus|bus|client|ws|self)\.(?:emit|publish|dispatch)\(\s*['"]?([A-Za-z0-9_]+)"""
)
_RE_PY_ON = re.compile(
    r"""\b(?:socketio|sio|emitter|events|event_bus|bus|client|ws|self)\.on\(\s*['"]([^'"]+)['"]"""
)
_RE_PY_EVENT_DECORATOR = re.compile(
    r"""@(?:sio|socketio|events|event_handler)\.(?:event|on)\(\s*(?:['"]([^'"]+)['"])?"""
)
_RE_PY_EVENT_INST = re.compile(r"""\b([A-Z][a-zA-Z0-9_]*Event)\s*\(""")
_RE_PY_EVENT_MAP = re.compile(
    r"""\b([A-Z][a-zA-Z0-9_]*Event)\s*:\s*['"]([a-z0-9_]+)['"]"""
)


def _to_snake_event(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    clean = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
    return clean.removesuffix("_event")


def extract_python_event_tags(text: str) -> list[str]:
    """Extrae tags de eventos en código Python (SocketIO, EventEmitters, EventBus)."""
    tags: set[str] = set()
    for m in _RE_PY_EMIT.finditer(text):
        raw_evt = m.group(1).strip()
        if raw_evt:
            tags.add(f"event:{_to_snake_event(raw_evt)}")
    for m in _RE_PY_ON.finditer(text):
        evt = m.group(1).strip()
        if evt:
            tags.add(f"event:{evt}")
    for m in _RE_PY_EVENT_DECORATOR.finditer(text):
        evt = (m.group(1) or "").strip()
        if evt:
            tags.add(f"event:{evt}")
    for m in _RE_PY_EVENT_INST.finditer(text):
        cls_name = m.group(1).strip()
        if cls_name and cls_name != "Event":
            tags.add(f"event:{_to_snake_event(cls_name)}")
    for m in _RE_PY_EVENT_MAP.finditer(text):
        cls_name = m.group(1).strip()
        evt_name = m.group(2).strip()
        if cls_name:
            tags.add(f"event:{_to_snake_event(cls_name)}")
        if evt_name:
            tags.add(f"event:{evt_name}")

    tags.discard("")
    return sorted(tags)


def extract_python_docstring(node: Node | Any) -> str:
    """Extrae la primera línea del docstring de un nodo de clase o función."""
    if node is None:
        return ""

    body_node = node.child_by_field_name("body")
    if not body_node:
        return ""

    for child in body_node.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type == "string" and sub.text:
                    raw = sub.text.decode("utf-8", errors="ignore").strip()
                    for quote in ('"""', "'''", '"', "'"):
                        if (
                            raw.startswith(quote)
                            and raw.endswith(quote)
                            and len(raw) >= 2 * len(quote)
                        ):
                            raw = raw[len(quote) : -len(quote)]
                            break
                    clean = raw.strip()
                    first_line = clean.split("\n")[0].strip()
                    return first_line[:200]
        elif child.type not in ("comment",):
            break
    return ""


def extract_python_signature(node: Node | Any) -> str:
    """Extrae la firma limpia de una función o método en Python."""
    if node is None:
        return ""

    name_node = node.child_by_field_name("name")
    params_node = node.child_by_field_name("parameters")
    return_type_node = node.child_by_field_name("return_type")

    name = (
        name_node.text.decode("utf-8", errors="ignore")
        if name_node and name_node.text
        else ""
    )
    params = (
        params_node.text.decode("utf-8", errors="ignore")
        if params_node and params_node.text
        else "()"
    )
    ret = (
        f" -> {return_type_node.text.decode('utf-8', errors='ignore')}"
        if return_type_node and return_type_node.text
        else ""
    )
    return f"def {name}{params}{ret}"


def parse_py_imports(lines: list[str]) -> tuple[list[str], list[str], int]:
    """Extrae las declaraciones de importación de Python al inicio del archivo."""
    import_lines: list[str] = []
    imports_list: list[str] = []
    next_line_idx = 0

    import_re = re.compile(
        r"^\s*(?:import\s+[\w\s,]+|from\s+[\w\.]+\s+import\s+[\w\s,\*\(\)]+)"
    )

    for idx, line in enumerate(lines):
        stripped = line.strip()
        is_comment_or_empty = (
            stripped == ""
            or stripped.startswith("#")
            or stripped.startswith('"""')
            or stripped.startswith("'''")
        )
        is_import = bool(import_re.match(stripped))

        if is_import:
            imports_list.append(stripped)
            import_lines.append(line)
            next_line_idx = idx + 1
        elif is_comment_or_empty:
            import_lines.append(line)
            next_line_idx = idx + 1
        else:
            break

    while import_lines:
        last_stripped = import_lines[-1].strip()
        if (
            last_stripped == ""
            or last_stripped.startswith("#")
            or last_stripped.startswith('"""')
            or last_stripped.startswith("'''")
        ):
            import_lines.pop()
            next_line_idx -= 1
        else:
            break

    return import_lines, imports_list, next_line_idx


def get_class_methods_py(class_node: Node | Any) -> list[str]:
    """Extrae los nombres de los métodos definidos en una clase de Python."""
    methods = []
    body_node = class_node.child_by_field_name("body")
    if body_node:
        for child in body_node.children:
            if child.type == "function_definition":
                name_node = child.child_by_field_name("name")
                if name_node and name_node.text:
                    methods.append(name_node.text.decode("utf-8", errors="ignore"))
            elif child.type == "decorated_definition":
                for sub in child.children:
                    if sub.type == "function_definition":
                        name_node = sub.child_by_field_name("name")
                        if name_node and name_node.text:
                            methods.append(
                                name_node.text.decode("utf-8", errors="ignore")
                            )
                        break
    return methods


def extract_python_class_schema(class_node: Node | Any) -> str:
    """Extrae atributos y tipos anotados de una clase o modelo en Python."""
    body_node = class_node.child_by_field_name("body")
    if not body_node:
        return ""

    fields: list[str] = []
    for child in body_node.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if not sub.text:
                    continue
                raw = sub.text.decode("utf-8", errors="ignore").strip()
                if (
                    raw.startswith(('"""', "'''", '"', "'"))
                    or raw.endswith(")")
                    or raw.startswith("@")
                ):
                    continue
                if ":" in raw and not raw.startswith("def "):
                    clean_line = " ".join(raw.split())
                    fields.append(clean_line)
        elif child.type == "function_definition":
            name_node = child.child_by_field_name("name")
            if name_node and name_node.text:
                fn_name = name_node.text.decode("utf-8", errors="ignore")
                if fn_name == "__init__" and not fields:
                    params_node = child.child_by_field_name("parameters")
                    if params_node:
                        for p in params_node.children:
                            if (
                                p.type
                                in (
                                    "identifier",
                                    "typed_parameter",
                                    "default_parameter",
                                    "typed_default_parameter",
                                )
                                and p.text
                            ):
                                p_text = p.text.decode("utf-8", errors="ignore").strip()
                                if p_text not in ("self", "cls", "*", "/"):
                                    fields.append(" ".join(p_text.split()))
    return ", ".join(fields)
