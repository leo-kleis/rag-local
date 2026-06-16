import re
from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK, OVERLAP_LINES


def clean_typescript_code(text: str) -> str:
    """Reemplaza comentarios, strings y expresiones regulares literales por espacios,
    preservando los saltos de línea para mantener la estructura original.
    """
    pattern = re.compile(
        r"(?P<single_comment>//[^\r\n]*)"
        r"|(?P<multi_comment>/\*.*?\*/)"
        r'|(?P<double_string>"(?:\\.|[^"\\])*")'
        r"|(?P<single_string>\'(?:\\.|[^\'\\])*\')"
        r"|(?P<template_string>`(?:\\.|[^`\\])*`)"
        r"|(?P<regex_literal>/(?:\\.|[^/\\\r\n])+/[gimyus]*)",
        re.DOTALL,
    )

    def replacer(match: re.Match) -> str:
        val = match.group(0)
        res = []
        for char in val:
            if char == "\n":
                res.append("\n")
            else:
                res.append(" ")
        return "".join(res)

    return pattern.sub(replacer, text)


def count_braces(line: str) -> tuple[int, int]:
    """Cuenta las llaves de apertura y cierre en una línea de forma segura."""
    # Eliminar comentarios de una línea
    s = re.sub(r"//.*", "", line)
    # Eliminar comentarios multilínea de una sola línea
    s = re.sub(r"/\*.*?\*/", "", s)
    # Eliminar cadenas de texto para evitar llaves falsas
    s = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', "", s)
    s = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "", s)
    s = re.sub(r"`[^`\\]*(?:\\.[^`\\]*)*`", "", s)
    # Eliminar expresiones regulares literales
    s = re.sub(r"/(?:\\.|[^/\\\r\n])+/[gimyus]*", "", s)
    return s.count("{"), s.count("}")


def parse_ts_imports(lines: list[str]) -> tuple[list[str], list[str], int]:
    """Extrae las declaraciones de importación de TypeScript al inicio."""
    import_lines: list[str] = []
    imports_list: list[str] = []
    next_line_idx = 0
    in_import = False
    current_import: list[str] = []

    import_patterns = [
        re.compile(r"import\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"from\s+['\"]([^'\"]+)['\"]"),
    ]

    for idx, line in enumerate(lines):
        stripped = line.strip()
        is_comment_or_empty = (
            stripped == ""
            or stripped.startswith("//")
            or stripped.startswith("/*")
            or stripped.startswith("*")
            or stripped.endswith("*/")
        )
        is_import_start = (
            stripped.startswith("import ")
            or stripped.startswith("import{")
            or stripped.startswith("import*")
        )

        if is_import_start:
            in_import = True
            current_import.append(line)
            import_lines.append(line)
            if ";" in stripped or "from" in stripped:
                for pattern in import_patterns:
                    m = pattern.search(stripped)
                    if m:
                        imports_list.append(m.group(1))
                in_import = False
                current_import = []
            next_line_idx = idx + 1
        elif in_import:
            current_import.append(line)
            import_lines.append(line)
            if (
                ";" in stripped
                or "from" in stripped
                or ("'" in stripped or '"' in stripped)
            ):
                full_import_str = "".join(current_import)
                for pattern in import_patterns:
                    m = pattern.search(full_import_str)
                    if m:
                        imports_list.append(m.group(1))
                in_import = False
                current_import = []
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
            or last_stripped.startswith("//")
            or last_stripped.startswith("/*")
            or last_stripped.startswith("*")
            or last_stripped.endswith("*/")
        ):
            import_lines.pop()
            next_line_idx -= 1
        else:
            break

    if not imports_list:
        return [], [], 0

    return import_lines, imports_list[:100], next_line_idx


def get_class_dependencies(
    class_text: str, global_dependencies: list[str]
) -> list[str]:
    """Extrae las dependencias inyectadas y los imports locales en una clase."""
    deps: list[str] = []
    constructor_match = re.search(r"constructor\s*\(([^)]*)\)", class_text)
    if constructor_match:
        params_text = constructor_match.group(1)
        types = re.findall(r"\b\w+\s*:\s*([A-Z]\w*)", params_text)
        for t in types:
            if t not in deps:
                deps.append(t)

    prop_types = re.findall(r"\bprivate\s+readonly\s+\w+\s*:\s*([A-Z]\w*)", class_text)
    for pt in prop_types:
        if pt not in deps:
            deps.append(pt)

    for imp in global_dependencies:
        if imp not in deps:
            deps.append(imp)

    return sorted(deps)


def extract_ts_methods(
    class_lines: list[tuple[int, str]], clean_class_lines: list[str]
) -> list[str]:
    """Extrae todos los métodos/funciones contenidos en una clase."""
    methods = []
    brace_level = 0
    for idx, (_line_num, line) in enumerate(class_lines):
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
    clean_text = clean_typescript_code(class_text)
    regex_matches = re.findall(r"\b([a-zA-Z_]\w*)\s*\([^)]*\)\s*\{", clean_text)
    excluded = {"if", "for", "while", "switch", "catch", "with", "constructor"}
    for m in regex_matches:
        if m not in excluded and m not in methods:
            methods.append(m)

    return methods


def split_ts_class(
    class_lines: list[tuple[int, str]],
    clean_class_lines: list[str],
    class_name: str,
    imports_list: list[str],
    global_dependencies: list[str],
) -> list[dict[str, Any]]:
    """Divide una clase TypeScript grande en constructor y métodos."""
    methods: list[dict[str, Any]] = []

    brace_level = 0
    in_method = False
    method_name = ""
    method_start_idx = -1
    method_decorators: list[tuple[int, str]] = []
    method_has_opened = False

    pending_decorators: list[tuple[int, str]] = []
    inside_decorator = False
    decorator_braces = 0
    decorator_parens = 0

    for idx, (line_num, line) in enumerate(class_lines):
        stripped = line.strip()
        clean_line = clean_class_lines[idx]
        open_braces = clean_line.count("{")
        close_braces = clean_line.count("}")

        prev_brace_level = brace_level
        brace_level += open_braces - close_braces

        # Procesamiento de decoradores
        if prev_brace_level == 1 and not in_method:
            if not inside_decorator:
                if stripped.startswith("@"):
                    inside_decorator = True
                    decorator_braces = 0
                    decorator_parens = 0

                    open_p = clean_line.count("(")
                    close_p = clean_line.count(")")
                    decorator_braces += open_braces - close_braces
                    decorator_parens += open_p - close_p

                    pending_decorators.append((line_num, line))
                    if decorator_braces <= 0 and decorator_parens <= 0:
                        inside_decorator = False
                    continue
            else:
                pending_decorators.append((line_num, line))
                open_p = clean_line.count("(")
                close_p = clean_line.count(")")
                decorator_braces += open_braces - close_braces
                decorator_parens += open_p - close_p
                if decorator_braces <= 0 and decorator_parens <= 0:
                    inside_decorator = False
                continue

        # Detección de inicio de método
        if prev_brace_level == 1 and not in_method:
            is_method_start = False
            if "(" in line and "=" not in line:
                match = re.search(r"\b(constructor|[a-zA-Z_]\w*)\s*\(", line)
                if match:
                    m_name = match.group(1)
                    if m_name not in {"if", "for", "while", "switch", "catch"}:
                        is_method_start = True
                        in_method = True
                        method_name = m_name
                        method_start_idx = idx
                        method_decorators = pending_decorators
                        pending_decorators = []
                        method_has_opened = open_braces > 0

            # Si no es inicio de método y no es decorador, pero hay
            # decoradores pendientes: eran decoradores de propiedades.
            # Los descartamos para que no se asocien al método siguiente.
            if (
                not is_method_start
                and not stripped.startswith("@")
                and pending_decorators
            ):
                pending_decorators = []

        if in_method:
            if brace_level > 1:
                method_has_opened = True

            if method_has_opened and brace_level <= 1:
                method_line_tuples = class_lines[method_start_idx : idx + 1]
                full_method_lines = method_decorators + method_line_tuples

                method_text = "".join(lc for _, lc in full_method_lines)
                m_start_line = full_method_lines[0][0]
                m_end_line = full_method_lines[-1][0]

                # Detección de clases anidadas
                found_classes = re.findall(r"\bclass\s+(\w+)", method_text)
                all_classes = [class_name]
                for c in found_classes:
                    if c not in all_classes:
                        all_classes.append(c)
                chunk_class_name = ",".join(all_classes)

                methods.append(
                    {
                        "text": method_text,
                        "start_line": m_start_line,
                        "end_line": m_end_line,
                        "method_name": method_name,
                        "class_name": chunk_class_name,
                    }
                )
                in_method = False
                method_name = ""
                method_start_idx = -1
                method_decorators = []
                method_has_opened = False

    constructor_chunk = None
    for m in methods:
        if m["method_name"] == "constructor":
            constructor_chunk = m
            break

    first_chunk_end_line = -1
    if constructor_chunk:
        first_chunk_end_line = constructor_chunk["end_line"]
    elif methods:
        sorted_methods = sorted(methods, key=lambda x: int(x["start_line"]))
        first_chunk_end_line = sorted_methods[0]["start_line"] - 1
    else:
        first_chunk_end_line = class_lines[-1][0]

    first_chunk_line_tuples = [
        (ln, lc) for ln, lc in class_lines if ln <= first_chunk_end_line
    ]

    chunks: list[dict[str, Any]] = []
    if first_chunk_line_tuples:
        first_chunk_text = "".join(lc for _, lc in first_chunk_line_tuples)
        first_chunk_start = first_chunk_line_tuples[0][0]
        first_chunk_end = first_chunk_line_tuples[-1][0]

        first_chunk_deps = []
        constructor_match = re.search(r"constructor\s*\(([^)]*)\)", first_chunk_text)
        if constructor_match:
            params_text = constructor_match.group(1)
            types = re.findall(r"\b\w+\s*:\s*([A-Z]\w*)", params_text)
            for t in types:
                if t not in first_chunk_deps:
                    first_chunk_deps.append(t)

        prop_types = re.findall(
            r"\bprivate\s+readonly\s+\w+\s*:\s*([A-Z]\w*)", first_chunk_text
        )
        for pt in prop_types:
            if pt not in first_chunk_deps:
                first_chunk_deps.append(pt)

        for imp in global_dependencies:
            if imp not in first_chunk_deps:
                first_chunk_deps.append(imp)

        # Clases anidadas en el primer chunk
        found_classes = re.findall(r"\bclass\s+(\w+)", first_chunk_text)
        all_classes = [class_name]
        for c in found_classes:
            if c not in all_classes:
                all_classes.append(c)
        chunk_class_name = ",".join(all_classes)

        chunks.append(
            {
                "text": first_chunk_text,
                "start_line": first_chunk_start,
                "end_line": first_chunk_end,
                "metadata": {
                    "class_name": chunk_class_name,
                    "method_name": "constructor" if constructor_chunk else "",
                    "imports": imports_list,
                    "dependencies": sorted(first_chunk_deps),
                },
            }
        )

    for m in methods:
        if m["method_name"] != "constructor" and m["start_line"] > first_chunk_end_line:
            chunks.append(
                {
                    "text": m["text"],
                    "start_line": m["start_line"],
                    "end_line": m["end_line"],
                    "metadata": {
                        "class_name": m["class_name"],
                        "method_name": m["method_name"],
                        "imports": imports_list,
                        "dependencies": sorted(global_dependencies),
                    },
                }
            )

    return chunks


def chunk_flat_lines(
    line_tuples: list[tuple[int, str]],
    imports_list: list[str],
    local_imports: list[str],
) -> list[dict[str, Any]]:
    """Divide líneas TypeScript planas con solapamiento."""
    chunks = []
    total_lines = len(line_tuples)
    if total_lines == 0:
        return []

    if total_lines <= MAX_LINES_PER_CHUNK:
        text = "".join(lc for _, lc in line_tuples)
        start_line = line_tuples[0][0]
        end_line = line_tuples[-1][0]

        # Clases anidadas en líneas planas
        found_classes = re.findall(r"\bclass\s+(\w+)", text)
        chunk_class_name = ",".join(found_classes) if found_classes else ""

        chunks.append(
            {
                "text": text,
                "start_line": start_line,
                "end_line": end_line,
                "metadata": {
                    "class_name": chunk_class_name,
                    "method_name": "",
                    "imports": imports_list,
                    "dependencies": local_imports,
                },
            }
        )
        return chunks

    start = 0
    while start < total_lines:
        end = min(start + MAX_LINES_PER_CHUNK, total_lines)
        chunk_lines = line_tuples[start:end]
        text = "".join(lc for _, lc in chunk_lines)
        start_line = chunk_lines[0][0]
        end_line = chunk_lines[-1][0]

        found_classes = re.findall(r"\bclass\s+(\w+)", text)
        chunk_class_name = ",".join(found_classes) if found_classes else ""

        chunks.append(
            {
                "text": text,
                "start_line": start_line,
                "end_line": end_line,
                "metadata": {
                    "class_name": chunk_class_name,
                    "method_name": "",
                    "imports": imports_list,
                    "dependencies": local_imports,
                },
            }
        )

        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES
        if start >= total_lines - OVERLAP_LINES:
            break

    return chunks


def chunk_typescript(lines: list[str]) -> list[dict[str, Any]]:
    """Divide un archivo TypeScript en base a sus clases, decoradores e imports

    usando tree-sitter.
    """
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    code = "".join(lines)
    parser = Parser(Language(tree_sitter_typescript.language_typescript()))
    tree = parser.parse(bytes(code, "utf8"))
    root_node = tree.root_node

    import_lines, imports_list, _next_line_idx = parse_ts_imports(lines)
    local_imports = [imp for imp in imports_list if imp.startswith(".")]

    chunks: list[dict[str, Any]] = []
    if import_lines:
        text = "".join(import_lines)
        chunks.append(
            {
                "text": text,
                "start_line": 1,
                "end_line": len(import_lines),
                "metadata": {
                    "class_name": "",
                    "method_name": "",
                    "imports": imports_list,
                    "dependencies": local_imports,
                },
            }
        )

    # Helper function to get nested classes recursively
    def get_all_class_names(node) -> list[str]:
        names = []
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text is not None:
                names.append(name_node.text.decode("utf-8", errors="ignore"))
        for child in node.children:
            names.extend(get_all_class_names(child))
        return names

    # Helper function to get methods inside a class (skipping nested classes)
    def get_class_methods(node) -> list[str]:
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

    # Traverse top-level nodes of interest
    nodes = []
    for child in root_node.children:
        # Skip import statements at the root level as they were already processed
        if child.type == "import_statement":
            continue
        # Check export statements
        inner = child
        if inner.type == "export_statement":
            for sub in inner.children:
                if sub.type in (
                    "class_declaration",
                    "function_declaration",
                    "interface_declaration",
                ):
                    inner = sub
                    break
        nodes.append(inner)

    # Group pending flat nodes (statements/functions that are not classes)
    def chunk_flat_nodes(flat_nodes) -> list[dict[str, Any]]:
        if not flat_nodes:
            return []
        line_tuples = []
        for fn in flat_nodes:
            fn_start = fn.start_point[0] + 1
            fn_end = fn.end_point[0] + 1
            for lnum in range(fn_start, fn_end + 1):
                # Ensure the line index is within lines range
                if 1 <= lnum <= len(lines):
                    line_tuples.append((lnum, lines[lnum - 1]))
        # Deduplicate line tuples
        seen = set()
        unique_line_tuples = []
        for lnum, lcontent in line_tuples:
            if lnum not in seen:
                seen.add(lnum)
                unique_line_tuples.append((lnum, lcontent))
        return chunk_flat_lines(unique_line_tuples, imports_list, local_imports)

    pending_flat_nodes = []
    for node in nodes:
        is_class = node.type == "class_declaration"

        if is_class:
            if pending_flat_nodes:
                chunks.extend(chunk_flat_nodes(pending_flat_nodes))
                pending_flat_nodes = []

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            # Adjust boundaries
            start_line = max(1, min(start_line, len(lines)))
            end_line = max(1, min(end_line, len(lines)))

            node_text = "".join(lines[start_line - 1 : end_line])
            class_names = get_all_class_names(node)
            class_name_str = ",".join(class_names) if class_names else ""

            if (end_line - start_line + 1) <= MAX_LINES_PER_CHUNK:
                method_names = get_class_methods(node)
                method_name_str = ",".join(method_names) if method_names else ""
                chunks.append(
                    {
                        "text": node_text,
                        "start_line": start_line,
                        "end_line": end_line,
                        "metadata": {
                            "class_name": class_name_str,
                            "method_name": method_name_str,
                            "imports": imports_list,
                            "dependencies": get_class_dependencies(
                                node_text, local_imports
                            ),
                        },
                    }
                )
            else:
                # Split large class using methods
                class_body = None
                for child in node.children:
                    if child.type == "class_body":
                        class_body = child
                        break
                constructor_node = None
                method_nodes = []
                if class_body:
                    for member in class_body.children:
                        if member.type == "method_definition":
                            name_node = member.child_by_field_name("name")
                            if name_node and name_node.text is not None:
                                name_str = name_node.text.decode(
                                    "utf-8", errors="ignore"
                                )
                                if name_str == "constructor":
                                    constructor_node = member
                                else:
                                    method_nodes.append(member)

                method_nodes.sort(key=lambda x: x.start_point[0])

                if constructor_node:
                    first_chunk_end_line = constructor_node.end_point[0] + 1
                elif method_nodes:
                    first_chunk_end_line = method_nodes[0].start_point[0]
                else:
                    first_chunk_end_line = end_line

                first_chunk_end_line = max(
                    start_line, min(first_chunk_end_line, end_line)
                )
                first_chunk_text = "".join(lines[start_line - 1 : first_chunk_end_line])
                first_chunk_deps = get_class_dependencies(
                    first_chunk_text, local_imports
                )

                chunks.append(
                    {
                        "text": first_chunk_text,
                        "start_line": start_line,
                        "end_line": first_chunk_end_line,
                        "metadata": {
                            "class_name": class_name_str,
                            "method_name": "constructor" if constructor_node else "",
                            "imports": imports_list,
                            "dependencies": first_chunk_deps,
                        },
                    }
                )

                for m_node in method_nodes:
                    m_start = m_node.start_point[0] + 1
                    m_end = m_node.end_point[0] + 1
                    m_start = max(1, min(m_start, len(lines)))
                    m_end = max(1, min(m_end, len(lines)))
                    m_text = "".join(lines[m_start - 1 : m_end])
                    m_name_node = m_node.child_by_field_name("name")
                    m_name = ""
                    if m_name_node and m_name_node.text is not None:
                        m_name = m_name_node.text.decode(
                            "utf-8", errors="ignore"
                        )

                    chunks.append(
                        {
                            "text": m_text,
                            "start_line": m_start,
                            "end_line": m_end,
                            "metadata": {
                                "class_name": class_name_str,
                                "method_name": m_name,
                                "imports": imports_list,
                                "dependencies": sorted(local_imports),
                            },
                        }
                    )
        else:
            pending_flat_nodes.append(node)

    if pending_flat_nodes:
        chunks.extend(chunk_flat_nodes(pending_flat_nodes))

    return chunks
