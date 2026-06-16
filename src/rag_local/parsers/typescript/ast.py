import re


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
