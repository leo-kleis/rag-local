import json
import re
from typing import Any

# Patrones pre-compilados para rendimiento
_RE_CLASSNAME_ATTRS = re.compile(
    r"""(?:className|class)\s*=\s*(?:"([^"]+)"|'([^']+)'|\$\{([^}]+)\})"""
)
_RE_CLEAN_INTERP = re.compile(r"\$\{[^}]+\}")
_RE_HELPERS = re.compile(
    r"\b(?:cn|clsx|cva|twMerge|twJoin)\s*\(\s*([^)]+)\)",
    re.DOTALL,
)
_RE_STR_LITERALS = re.compile(r'["\']([^"\']+)["\']')
_RE_DYNAMIC_PREFIX = re.compile(r"\b([a-zA-Z0-9_-]+[-_])\$\{")
_RE_VALID_TOKEN = re.compile(r"^[a-zA-Z_][\w-]*$")
_RE_CLASS_TMPL_BODY = re.compile(r"`([^`]+)`")
_RE_OBJ_CLASS_PROP = re.compile(
    r"[a-zA-Z_]\w*[Cc]lass(?:[Nn]ame)?\s*:\s*(?:'([^']+)'|\"([^\"]+)\")"
)
_RE_VAR_CLASS_ASSIGN = re.compile(
    r"(?:const|let|var)\s+\w*[Cc]lass(?:[Nn]ame)?\s*=\s*([^;\n]+)"
)
_RE_CLASSNAME_BRACES = re.compile(r"(?:className|class)\s*=\s*\{([^}]+)\}")


def _tokens_from_string(value: str) -> list[str]:
    """Extrae tokens válidos de clase CSS desde un string literal."""
    tokens: list[str] = []
    for token in value.split():
        clean = token.strip()
        if clean and _RE_VALID_TOKEN.match(clean):
            tokens.append(clean)
    return tokens


def _extract_literals_and_tokenize(expression: str) -> list[str]:
    """Extrae literales de strings dentro de una expresión y retorna sus tokens."""
    tokens: list[str] = []
    for lit in _RE_STR_LITERALS.findall(expression):
        tokens.extend(_tokens_from_string(lit))
    return tokens


def extract_ts_methods(
    class_lines: list[tuple[int, str]], clean_class_lines: list[str]
) -> list[str]:
    """Extrae todos los métodos/funciones contenidos en una clase."""
    methods = []
    brace_level = 0
    for idx, (_, line) in enumerate(class_lines):
        stripped = line.strip()
        clean_line = clean_class_lines[idx]
        open_braces = clean_line.count("{")
        close_braces = clean_line.count("}")
        prev_brace_level = brace_level
        brace_level += open_braces - close_braces
        if prev_brace_level == 1:
            if stripped.startswith("@"):
                continue
            if "(" in line and "=" not in line:
                match = re.search(r"\b(constructor|[a-zA-Z_]\w*)\s*\(", line)
                if match:
                    m_name = match.group(1)
                    if m_name not in {"if", "for", "while", "switch", "catch"}:
                        methods.append(m_name)

    # Detección adicional por regex para líneas complejas
    class_text = "".join(lc for _, lc in class_lines)
    from rag_local.parsers.typescript.cleaner import clean_typescript_code

    clean_text = clean_typescript_code(class_text)
    regex_matches = re.findall(r"\b([a-zA-Z_]\w*)\s*\([^)]*\)\s*\{", clean_text)
    excluded = {"if", "for", "while", "switch", "catch", "with", "constructor"}
    for m in regex_matches:
        if m not in excluded and m not in methods:
            methods.append(m)

    return methods


def get_all_class_names(node: Any) -> list[str]:
    """Obtiene de manera recursiva todos los nombres de clases anidadas en un nodo."""
    names = []
    if node.type == "class_declaration":
        name_node = node.child_by_field_name("name")
        if name_node and name_node.text is not None:
            names.append(name_node.text.decode("utf-8", errors="ignore"))
    for child in node.children:
        names.extend(get_all_class_names(child))
    return names


def get_class_methods(node: Any) -> list[str]:
    """Obtiene los nombres de métodos dentro de una clase (saltando clases anidadas)."""
    methods = []

    def helper(n: Any) -> None:
        if n.type == "method_definition":
            name_node = n.child_by_field_name("name")
            if name_node and name_node.text is not None:
                methods.append(name_node.text.decode("utf-8", errors="ignore"))
        if n.type == "class_declaration" and n != node:
            return
        for child in n.children:
            helper(child)

    helper(node)
    return methods


def extract_jsx_css_classes(text: str) -> list[str]:
    """Extrae nombres de clases CSS usadas en JSX/TSX y asignaciones JS."""
    classes: set[str] = set()

    # 1. Atributos con comillas o template: className="..." / class='...'
    for match in _RE_CLASSNAME_ATTRS.finditer(text):
        d_val, s_val, tmpl_val = match.groups()
        val = d_val or s_val
        if val:
            classes.update(_tokens_from_string(_RE_CLEAN_INTERP.sub("", val)))
            for interp in re.finditer(r"\$\{([^}]+)\}", val):
                classes.update(_extract_literals_and_tokenize(interp.group(1)))
        elif tmpl_val:
            classes.update(_extract_literals_and_tokenize(tmpl_val))

    # 2. Expresiones entre llaves: className={cond ? 'a' : 'b'}
    for m in _RE_CLASSNAME_BRACES.finditer(text):
        classes.update(_extract_literals_and_tokenize(m.group(1)))

    # 3. Helpers de clase: cn(...), clsx(...), cva(...), twMerge(...), twJoin(...)
    for match in _RE_HELPERS.finditer(text):
        classes.update(_extract_literals_and_tokenize(match.group(1)))

    # 4. Prefijos dinámicos en templates o atributos: `status-${var}`
    for match in _RE_DYNAMIC_PREFIX.finditer(text):
        classes.add(f"[BEM]{match.group(1)}")

    # 5. Template literals con ternario de clase: `base-class ${cond ? 'ok' : 'err'}`
    for tmpl in _RE_CLASS_TMPL_BODY.finditer(text):
        for interp in re.finditer(r"\$\{([^}]+)\}", tmpl.group(1)):
            classes.update(_extract_literals_and_tokenize(interp.group(1)))

    # 6. Propiedades de objeto con clave *ClassName / *Class:
    for m in _RE_OBJ_CLASS_PROP.finditer(text):
        val = m.group(1) or m.group(2)
        if val:
            classes.update(_tokens_from_string(val))

    # 7. Asignaciones de variables: const xyzClass = cond ? 'ok' : 'err'
    for m in _RE_VAR_CLASS_ASSIGN.finditer(text):
        classes.update(_extract_literals_and_tokenize(m.group(1)))

    classes.discard("")
    return sorted(classes)


_tsx_ast_parser: Any = None


def _get_tsx_ast_parser() -> Any:
    """Obtiene o inicializa el Parser de TSX de Tree-sitter para análisis AST."""
    global _tsx_ast_parser
    if _tsx_ast_parser is None:
        import tree_sitter_typescript
        from tree_sitter import Language, Parser

        _tsx_ast_parser = Parser(Language(tree_sitter_typescript.language_tsx()))
    return _tsx_ast_parser


def _is_jsx_component_expression(node: Any) -> bool:
    """Verifica recursivamente si una expresión JSX evalúa a un componente/elemento."""
    if node.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
        return True
    if node.type == "identifier":
        name = node.text.decode("utf-8", errors="ignore")
        if name and name[0].isupper():
            return True
    if node.type == "binary_expression":
        for child in node.children:
            if child.type not in ("&&", "||", "??", "!", "(", ")") and (
                _is_jsx_component_expression(child)
            ):
                return True
    if node.type in ("ternary_expression", "conditional_expression"):
        consequence = node.child_by_field_name("consequence")
        alternative = node.child_by_field_name("alternative")
        if (consequence and _is_jsx_component_expression(consequence)) or (
            alternative and _is_jsx_component_expression(alternative)
        ):
            return True
    return False


def _is_dynamic_text_expression(node: Any) -> bool:
    """Verifica si un jsx_expression en posición de hijo representa texto dinámico."""
    inner_nodes = [c for c in node.children if c.type not in ("{", "}")]
    if not inner_nodes:
        return False
    return not any(_is_jsx_component_expression(n) for n in inner_nodes)


_RE_DECL_COMP = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:function|class|const|let|var)\s+([A-Z][a-zA-Z0-9_]*)"
)


def _extract_classes_from_jsx_opening(node: Any) -> set[str]:
    """Extrae nombres de clase declarados en un nodo de apertura JSX."""
    classes: set[str] = set()

    name_field = node.child_by_field_name("name")
    if name_field:
        tag_name = name_field.text.decode("utf-8", errors="ignore")
        if tag_name and tag_name[0].isupper() and not tag_name.isupper():
            classes.add(f"[COMP]{tag_name}")

    for child in node.children:
        if child.type == "jsx_attribute":
            attr_name_node = None
            attr_val_node = None
            for c in child.children:
                if c.type in ("property_identifier", "identifier"):
                    attr_name_node = c
                elif c.type in ("string", "jsx_expression", "string_fragment"):
                    attr_val_node = c

            if attr_name_node:
                attr_name = attr_name_node.text.decode("utf-8", errors="ignore")
                if attr_name in ("className", "class") and attr_val_node:
                    val_text = attr_val_node.text.decode("utf-8", errors="ignore")
                    tokens = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_-]*\b", val_text)
                    classes.update(tokens)
    classes.discard("className")
    classes.discard("class")
    return classes


def extract_jsx_class_parents(text: str) -> str:
    """Extrae jerarquía de ancestros y metadatos sintácticos en JSX/TSX."""
    if not text or not text.strip():
        return ""

    try:
        parser = _get_tsx_ast_parser()
        tree = parser.parse(text.encode("utf-8", errors="ignore"))
    except Exception:
        return ""

    class_data: dict[str, dict[str, Any]] = {}
    declared_components = [c for c in _RE_DECL_COMP.findall(text) if not c.isupper()]
    initial_stack = (
        [{f"[COMP]{c}" for c in declared_components}] if declared_components else []
    )

    def traverse(node: Any, stack: list[set[str]], in_collection: bool) -> None:
        current_in_collection = in_collection
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn and fn.type == "member_expression":
                prop = fn.child_by_field_name("property")
                if prop:
                    method_name = prop.text.decode("utf-8", errors="ignore")
                    if method_name in ("map", "flatMap"):
                        current_in_collection = True

        if node.type in ("jsx_element", "jsx_self_closing_element"):
            opening = node
            if node.type == "jsx_element":
                for child in node.children:
                    if child.type == "jsx_opening_element":
                        opening = child
                        break

            node_classes = _extract_classes_from_jsx_opening(opening)
            has_dynamic = False
            if node.type == "jsx_element":
                for child in node.children:
                    if child.type == "jsx_expression" and _is_dynamic_text_expression(
                        child
                    ):
                        has_dynamic = True
                        break

            for c in node_classes:
                if c not in class_data:
                    class_data[c] = {
                        "parents": set(),
                        "has_dynamic_text": False,
                        "is_collection": False,
                        "own_tags": set(),
                    }
                for parent_set in stack:
                    class_data[c]["parents"].update(parent_set)
                if has_dynamic:
                    class_data[c]["has_dynamic_text"] = True
                if current_in_collection:
                    class_data[c]["is_collection"] = True
                name_field = opening.child_by_field_name("name")
                if name_field:
                    original_tag = name_field.text.decode("utf-8", errors="ignore")
                    if original_tag and original_tag[0].islower():
                        class_data[c]["own_tags"].add(original_tag.lower())

            new_stack = list(stack)
            if node_classes:
                new_stack.append(node_classes)
                if len(new_stack) > 8:
                    new_stack.pop(0)

            for child in node.children:
                traverse(child, new_stack, current_in_collection)
        else:
            for child in node.children:
                traverse(child, stack, current_in_collection)

    traverse(tree.root_node, initial_stack, False)

    # Extracción de templates HTML / tagged template literals (ej. Preact htm, Lit)
    from rag_local.parsers.html import extract_html_class_parents

    inline_rules: list[dict[str, Any]] = []
    html_parents_str = extract_html_class_parents(text)
    portal_classes: list[str] = []
    if html_parents_str:
        try:
            html_data = json.loads(html_parents_str)
            for c, info in html_data.items():
                if c == "__inline_rules__":
                    if isinstance(info, list):
                        inline_rules.extend(info)
                    continue
                if c == "__portal_classes__":
                    if isinstance(info, list):
                        portal_classes.extend(info)
                    continue
                if c not in class_data:
                    class_data[c] = {
                        "parents": set(info.get("parents", [])),
                        "has_dynamic_text": info.get("has_dynamic_text", False),
                        "is_collection": info.get("is_collection", False),
                        "own_tags": set(info.get("own_tags", [])),
                    }
                else:
                    class_data[c]["parents"].update(info.get("parents", []))
                    if info.get("has_dynamic_text"):
                        class_data[c]["has_dynamic_text"] = True
                    if info.get("is_collection"):
                        class_data[c]["is_collection"] = True
                    class_data[c]["own_tags"].update(info.get("own_tags", []))
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    if not class_data and not inline_rules:
        return ""

    res: dict[str, Any] = {
        c: {
            "parents": sorted(info["parents"]),
            "has_dynamic_text": info["has_dynamic_text"],
            "is_collection": info["is_collection"],
            "own_tags": sorted(info["own_tags"]),
        }
        for c, info in class_data.items()
    }
    if inline_rules:
        res["__inline_rules__"] = inline_rules
    if portal_classes:
        res["__portal_classes__"] = sorted(set(portal_classes))
    return json.dumps(res, ensure_ascii=False)


_RE_SOCKET_EMIT = re.compile(
    r"""\b(?:socket|emitter|client|ws|io|eventEmitter|events|window)\.emit\(\s*['"]([^'"]+)['"]"""
)
_RE_SOCKET_ON = re.compile(
    r"""\b(?:socket|emitter|client|ws|io|eventEmitter|events|window)\.on\(\s*['"]([^'"]+)['"]"""
)
_RE_SUBSCRIBE_MSG = re.compile(r"""@SubscribeMessage\(\s*['"]([^'"]+)['"]""")
_RE_DISPATCH_TYPE = re.compile(r"""\bdispatch\(\s*\{\s*type:\s*['"]([^'"]+)['"]""")


def extract_event_and_action_tags(text: str) -> list[str]:
    """Extrae tags normalizados de eventos y acciones en TypeScript/JavaScript."""
    tags: set[str] = set()

    for m in _RE_SOCKET_EMIT.finditer(text):
        evt = m.group(1).strip()
        if evt:
            tags.add(f"event:{evt}")
    for m in _RE_SOCKET_ON.finditer(text):
        evt = m.group(1).strip()
        if evt:
            tags.add(f"event:{evt}")
    for m in _RE_SUBSCRIBE_MSG.finditer(text):
        evt = m.group(1).strip()
        if evt:
            tags.add(f"event:{evt}")
    for m in _RE_DISPATCH_TYPE.finditer(text):
        act = m.group(1).strip()
        if act:
            tags.add(f"action:{act}")

    tags.discard("")
    return sorted(tags)


_RE_JSDOC_COMMENT = re.compile(r"/\*\*\s*([\s\S]*?)\*/")


def extract_ts_jsdoc_and_signature(text: str) -> str:
    """Extrae la primera línea del JSDoc o comentario descriptivo en TypeScript."""
    m = _RE_JSDOC_COMMENT.search(text)
    if m:
        content = m.group(1).strip()
        lines = [line.strip().lstrip("*").strip() for line in content.splitlines()]
        clean_lines = [line for line in lines if line and not line.startswith("@")]
        if clean_lines:
            return clean_lines[0][:200]
    return ""
