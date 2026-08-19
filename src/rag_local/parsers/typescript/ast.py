import json
import re

# Patrones pre-compilados para rendimiento
_RE_CLASSNAME_DOUBLE = re.compile(r'(?:className|class)\s*=\s*"([^"]+)"')
_RE_CLASSNAME_SINGLE = re.compile(r"(?:className|class)\s*=\s*'([^']+)'")
_RE_CLASSNAME_TMPL = re.compile(r"(?:className|class)\s*=\s*\$\{([^}]+)\}")
_RE_CLEAN_INTERP = re.compile(r"\$\{[^}]+\}")
_RE_HELPERS = re.compile(
    r"\b(?:cn|clsx|cva|twMerge|twJoin)\s*\(\s*([^)]+)\)",
    re.DOTALL,
)
_RE_STR_LITERALS = re.compile(r'["\']([^"\']+)["\']')
# Prefijos dinámicos en templates o atributos: `status-${var}` o `user-avatar--${var}`
_RE_DYNAMIC_PREFIX = re.compile(r"\b([a-zA-Z0-9_-]+[-_])\$\{")
_RE_VALID_TOKEN = re.compile(r"^[a-zA-Z_][\w-]*$")
# Template literals con class mixta: `base-class ${cond ? 'ok' : 'err'}`
_RE_CLASS_TMPL_BODY = re.compile(r"`([^`]+)`")
# Propiedades de objeto con clave Class/ClassName (ej: sysClassName)
_RE_OBJ_CLASS_PROP = re.compile(
    r"[a-zA-Z_]\w*[Cc]lass(?:[Nn]ame)?\s*:\s*'([^']+)'"
    r"|[a-zA-Z_]\w*[Cc]lass(?:[Nn]ame)?\s*:\s*\"([^\"]+)\""
)
# Asignaciones de variables: const xyzClass = cond ? 'ok' : 'err'
_RE_VAR_CLASS_ASSIGN = re.compile(
    r"(?:const|let|var)\s+\w*[Cc]lass(?:[Nn]ame)?\s*=\s*([^;\n]+)"
)


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

    # Detección adicional por regex para líneas complejas o únicas
    class_text = "".join(lc for _, lc in class_lines)
    from rag_local.parsers.typescript.cleaner import clean_typescript_code

    clean_text = clean_typescript_code(class_text)
    regex_matches = re.findall(r"\b([a-zA-Z_]\w*)\s*\([^)]*\)\s*\{", clean_text)
    excluded = {"if", "for", "while", "switch", "catch", "with", "constructor"}
    for m in regex_matches:
        if m not in excluded and m not in methods:
            methods.append(m)

    return methods


def get_all_class_names(node) -> list[str]:
    """Obtiene de manera recursiva todos los nombres de clases anidadas en un nodo."""
    names = []
    if node.type == "class_declaration":
        name_node = node.child_by_field_name("name")
        if name_node and name_node.text is not None:
            names.append(name_node.text.decode("utf-8", errors="ignore"))
    for child in node.children:
        names.extend(get_all_class_names(child))
    return names


def get_class_methods(node) -> list[str]:
    """Obtiene los nombres de métodos dentro de una clase (saltando clases anidadas)."""
    methods = []

    def helper(n):
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


def _tokens_from_string(value: str) -> list[str]:
    """Extrae tokens válidos de clase CSS desde un string literal."""
    tokens: list[str] = []
    for token in value.split():
        clean = token.strip()
        if clean and _RE_VALID_TOKEN.match(clean):
            tokens.append(clean)
    return tokens


def extract_jsx_css_classes(text: str) -> list[str]:
    """Extrae nombres de clases CSS usadas en JSX/TSX y asignaciones JS.

    Detecta clases de:
    - className="clase1 clase2" / class="..."
    - className={cond ? 'a' : 'b'} (expresiones entre llaves)
    - cn/clsx/cva/twMerge/twJoin(...) helpers
    - Prefijos BEM en templates: `prefix--${var}` -> "[BEM]prefix--"
    - Template literals con clase dinámica: `base ${cond ? 'ok' : 'err'}`
    - Propiedades de objeto con clave *ClassName/*Class: { sysClassName: 'sys-raid' }
    """
    classes: set[str] = set()

    # 1. Atributos con comillas estáticas: class="..." / className="..."
    # También captura interpolaciones ${cond ? 'ok' : 'err'} dentro del valor
    for match in _RE_CLASSNAME_DOUBLE.finditer(text):
        val = match.group(1)
        classes.update(_tokens_from_string(_RE_CLEAN_INTERP.sub("", val)))
        for interp in re.finditer(r"\$\{([^}]+)\}", val):
            for lit in _RE_STR_LITERALS.findall(interp.group(1)):
                classes.update(_tokens_from_string(lit))
    for match in _RE_CLASSNAME_SINGLE.finditer(text):
        val = match.group(1)
        classes.update(_tokens_from_string(_RE_CLEAN_INTERP.sub("", val)))
        for interp in re.finditer(r"\$\{([^}]+)\}", val):
            for lit in _RE_STR_LITERALS.findall(interp.group(1)):
                classes.update(_tokens_from_string(lit))

    # 2. Atributos con template literal: class=${...}
    for match in _RE_CLASSNAME_TMPL.finditer(text):
        for lit in _RE_STR_LITERALS.findall(match.group(1)):
            classes.update(_tokens_from_string(lit))

    # 3. Expresiones entre llaves: className={cond ? 'a' : 'b'}
    # Captura literales de string dentro del contexto de className={}
    for m in re.finditer(r"(?:className|class)\s*=\s*\{([^}]+)\}", text):
        for lit in _RE_STR_LITERALS.findall(m.group(1)):
            classes.update(_tokens_from_string(lit))

    # 4. Helpers de clase: cn(...), clsx(...), cva(...), twMerge(...), twJoin(...)
    for match in _RE_HELPERS.finditer(text):
        for lit in _RE_STR_LITERALS.findall(match.group(1)):
            classes.update(_tokens_from_string(lit))

    # 5. Prefijos dinámicos en templates o atributos: `status-${var}`
    # Se almacenan con marcador [BEM] para que el servicio los trate como prefijos
    for match in _RE_DYNAMIC_PREFIX.finditer(text):
        prefix = match.group(1)  # ej: "status-" o "user-avatar--"
        classes.add(f"[BEM]{prefix}")

    # 6. Template literals con ternario de clase: `base-class ${cond ? 'ok' : 'err'}`
    # Extrae los literales de string dentro de interpolaciones ${...} en templates
    for tmpl in _RE_CLASS_TMPL_BODY.finditer(text):
        body = tmpl.group(1)
        for interp in re.finditer(r"\$\{([^}]+)\}", body):
            for lit in _RE_STR_LITERALS.findall(interp.group(1)):
                classes.update(_tokens_from_string(lit))

    # 7. Propiedades de objeto con clave *ClassName / *Class:
    # ej: { sysClassName: 'sys-raid', toastClassName: 'toast-cheer' }
    for m in _RE_OBJ_CLASS_PROP.finditer(text):
        val = m.group(1) or m.group(2)
        if val:
            classes.update(_tokens_from_string(val))

    # 8. Asignaciones de variables: const xyzClass = cond ? 'ok' : 'err'
    # Cubre patrones como: const rpmDotClass = is_blocked ? 'block' : 'warn'
    for m in _RE_VAR_CLASS_ASSIGN.finditer(text):
        expr = m.group(1)
        for lit in _RE_STR_LITERALS.findall(expr):
            classes.update(_tokens_from_string(lit))

    classes.discard("")
    return sorted(classes)


_RE_JSX_CLASS_ATTR = re.compile(r'(?:className|class)\s*=\s*["\'`]?([^"\'`>]+)["\'`]')


def extract_jsx_class_parents(text: str) -> str:
    """Extrae jerarquía de ancestros CSS en JSX/TSX usando ventana de pila.

    Retorna: '{"child_class": ["parent_class1", ...]}' o '' si está vacío.
    """
    if not text or not text.strip():
        return ""
    parent_map: dict[str, set[str]] = {}
    stack: list[set[str]] = []
    for match in _RE_JSX_CLASS_ATTR.finditer(text):
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


_RE_SOCKET_EMIT = re.compile(
    r"""\b(?:socket|emitter|client|ws|io|eventEmitter|events|window)\.emit\(\s*['"]([^'"]+)['"]"""
)
_RE_SOCKET_ON = re.compile(
    r"""\b(?:socket|emitter|client|ws|io|eventEmitter|events|window)\.on\(\s*['"]([^'"]+)['"]"""
)
_RE_SUBSCRIBE_MSG = re.compile(r"""@SubscribeMessage\(\s*['"]([^'"]+)['"]""")
_RE_DISPATCH_TYPE = re.compile(r"""\bdispatch\(\s*\{\s*type:\s*['"]([^'"]+)['"]""")


def extract_event_and_action_tags(text: str) -> list[str]:
    """Extrae tags normalizados de eventos y acciones en código TypeScript/JavaScript.

    Detecta:
    - WebSockets: socket.emit('evt'), socket.on('evt') -> 'event:<nombre>'
    - NestJS WebSockets: @SubscribeMessage('event') -> 'event:<nombre>'
    - Redux/Dispatch actions: dispatch({ type: 'ACTION' }) -> 'action:<nombre>'
    """
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
