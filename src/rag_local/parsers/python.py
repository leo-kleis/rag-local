import re
from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK, OVERLAP_LINES
from rag_local.core.models import Chunk, ChunkMetadata

_py_parser: Any = None


def get_python_parser() -> Any:
    """Obtiene o inicializa el Parser de Python de forma perezosa."""
    global _py_parser
    if _py_parser is None:
        import tree_sitter_python
        from tree_sitter import Language, Parser

        _py_parser = Parser(Language(tree_sitter_python.language()))
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


def chunk_python(lines: list[str]) -> list[Chunk]:
    """Divide un archivo de Python (.py) usando tree-sitter.

    Implementa Hierarchical AST Chunking.
    """
    code = "".join(lines)
    parser = get_python_parser()
    tree = parser.parse(bytes(code, "utf8"))
    root_node = tree.root_node

    import_lines, imports_list, _ = parse_py_imports(lines)
    local_imports = []
    for imp in imports_list:
        if "from ." in imp or "import ." in imp:
            local_imports.append(imp)

    import_text = "".join(import_lines) if import_lines else ""

    chunks: list[Chunk] = []

    if import_lines:
        chunks.append(
            Chunk(
                text=import_text,
                start_line=1,
                end_line=len(import_lines),
                metadata=ChunkMetadata(
                    class_name="",
                    method_name="",
                    imports=imports_list,
                    dependencies=local_imports,
                ),
            )
        )

    nodes = []
    for child in root_node.children:
        if child.type in ("import_statement", "import_from_statement"):
            continue
        nodes.append(child)

    def chunk_flat_lines(line_tuples: list[tuple[int, str]]) -> list[Chunk]:
        """Divide líneas de código plano (sin clases) con solapamiento."""
        chunks_local = []
        total_lines = len(line_tuples)
        if total_lines == 0:
            return []

        if total_lines <= MAX_LINES_PER_CHUNK:
            text = "".join(lc for _, lc in line_tuples)
            start_line = line_tuples[0][0]
            end_line = line_tuples[-1][0]
            chunks_local.append(
                Chunk(
                    text=text,
                    start_line=start_line,
                    end_line=end_line,
                    metadata=ChunkMetadata(
                        class_name="",
                        method_name="",
                        imports=imports_list,
                        dependencies=local_imports,
                        tags=extract_python_event_tags(text),
                    ),
                )
            )
            return chunks_local

        start = 0
        while start < total_lines:
            end = min(start + MAX_LINES_PER_CHUNK, total_lines)
            chunk_lines = line_tuples[start:end]
            text = "".join(lc for _, lc in chunk_lines)
            start_line = chunk_lines[0][0]
            end_line = chunk_lines[-1][0]

            chunks_local.append(
                Chunk(
                    text=text,
                    start_line=start_line,
                    end_line=end_line,
                    metadata=ChunkMetadata(
                        class_name="",
                        method_name="",
                        imports=imports_list,
                        dependencies=local_imports,
                        tags=extract_python_event_tags(text),
                    ),
                )
            )
            start += MAX_LINES_PER_CHUNK - OVERLAP_LINES
            if start >= total_lines - OVERLAP_LINES:
                break
        return chunks_local

    def chunk_flat_nodes(flat_nodes: list[Any]) -> list[Chunk]:
        if not flat_nodes:
            return []
        line_tuples = []
        for fn in flat_nodes:
            fn_start = fn.start_point[0] + 1
            fn_end = fn.end_point[0] + 1
            for lnum in range(fn_start, fn_end + 1):
                if 1 <= lnum <= len(lines):
                    line_tuples.append((lnum, lines[lnum - 1]))
        seen = set()
        unique_line_tuples = []
        for lnum, lcontent in line_tuples:
            if lnum not in seen:
                seen.add(lnum)
                unique_line_tuples.append((lnum, lcontent))
        return chunk_flat_lines(unique_line_tuples)

    def get_class_methods_py(class_node: Any) -> list[str]:
        methods = []
        body_node = class_node.child_by_field_name("body")
        if body_node:
            for child in body_node.children:
                if child.type == "function_definition":
                    name_node = child.child_by_field_name("name")
                    if name_node and name_node.text:
                        methods.append(name_node.text.decode("utf-8", errors="ignore"))
        return methods

    pending_flat_nodes = []
    for node in nodes:
        raw_node = node
        actual_node = node
        if node.type == "decorated_definition":
            for sub in node.children:
                if sub.type in ("class_definition", "function_definition"):
                    actual_node = sub
                    break

        is_class = actual_node.type == "class_definition"
        is_function = actual_node.type == "function_definition"

        if is_class:
            if pending_flat_nodes:
                chunks.extend(chunk_flat_nodes(pending_flat_nodes))
                pending_flat_nodes = []

            start_line = raw_node.start_point[0] + 1
            end_line = raw_node.end_point[0] + 1
            start_line = max(1, min(start_line, len(lines)))
            end_line = max(1, min(end_line, len(lines)))

            node_text = "".join(lines[start_line - 1 : end_line])
            class_name_node = actual_node.child_by_field_name("name")
            class_name_str = (
                class_name_node.text.decode("utf-8", errors="ignore")
                if class_name_node and class_name_node.text
                else ""
            )

            # Si la clase es pequeña, procesarla como un único fragmento completo
            if (end_line - start_line + 1) <= MAX_LINES_PER_CHUNK:
                method_names = get_class_methods_py(actual_node)
                method_name_str = ",".join(method_names) if method_names else ""

                dependencies_set = set()
                words = re.findall(r"\b[A-Za-z_]\w*\b", node_text)
                for w in words:
                    if w not in (class_name_str, "") and len(w) > 3:
                        dependencies_set.add(w)

                chunks.append(
                    Chunk(
                        text=node_text,
                        start_line=start_line,
                        end_line=end_line,
                        metadata=ChunkMetadata(
                            class_name=class_name_str,
                            method_name=method_name_str,
                            imports=imports_list,
                            dependencies=sorted(dependencies_set) + local_imports,
                            tags=extract_python_event_tags(node_text),
                        ),
                    )
                )
            else:
                body_node = actual_node.child_by_field_name("body")
                init_node = None
                method_nodes = []
                if body_node:
                    for member in body_node.children:
                        if member.type == "function_definition":
                            name_node = member.child_by_field_name("name")
                            if name_node and name_node.text:
                                name_str = name_node.text.decode(
                                    "utf-8", errors="ignore"
                                )
                                if name_str == "__init__":
                                    init_node = member
                                else:
                                    method_nodes.append(member)

                method_nodes.sort(key=lambda x: x.start_point[0])

                if body_node:
                    header_end_line = body_node.start_point[0] + 1
                    header_end_line = max(start_line, min(header_end_line, end_line))
                    class_header_text = "".join(lines[start_line - 1 : header_end_line])
                else:
                    class_header_text = f"class {class_name_str}:\n"

                if init_node:
                    first_chunk_end_line = init_node.end_point[0] + 1
                elif method_nodes:
                    first_chunk_end_line = method_nodes[0].start_point[0]
                else:
                    first_chunk_end_line = end_line

                first_chunk_end_line = max(
                    start_line, min(first_chunk_end_line, end_line)
                )
                first_chunk_text = "".join(lines[start_line - 1 : first_chunk_end_line])

                if not first_chunk_text.strip().startswith(
                    "import"
                ) and not first_chunk_text.strip().startswith("from"):
                    first_chunk_text = f"{import_text}\n{first_chunk_text}"

                first_chunk_deps = set()
                words = re.findall(r"\b[A-Za-z_]\w*\b", first_chunk_text)
                for w in words:
                    if w not in (class_name_str, "__init__") and len(w) > 3:
                        first_chunk_deps.add(w)

                chunks.append(
                    Chunk(
                        text=first_chunk_text,
                        start_line=start_line,
                        end_line=first_chunk_end_line,
                        metadata=ChunkMetadata(
                            class_name=class_name_str,
                            method_name="__init__" if init_node else "",
                            imports=imports_list,
                            dependencies=sorted(first_chunk_deps) + local_imports,
                            tags=extract_python_event_tags(first_chunk_text),
                        ),
                    )
                )

                for m_node in method_nodes:
                    m_start = m_node.start_point[0] + 1
                    m_end = m_node.end_point[0] + 1
                    m_start = max(1, min(m_start, len(lines)))
                    m_end = max(1, min(m_end, len(lines)))
                    m_text = "".join(lines[m_start - 1 : m_end])

                    m_name_node = m_node.child_by_field_name("name")
                    m_name = (
                        m_name_node.text.decode("utf-8", errors="ignore")
                        if m_name_node and m_name_node.text
                        else ""
                    )

                    hierarchical_text = (
                        f"{import_text}\n{class_header_text}\n{m_text}\n"
                    )

                    m_deps = set()
                    words = re.findall(r"\b[A-Za-z_]\w*\b", hierarchical_text)
                    for w in words:
                        if w not in (class_name_str, m_name) and len(w) > 3:
                            m_deps.add(w)

                    chunks.append(
                        Chunk(
                            text=hierarchical_text,
                            start_line=m_start,
                            end_line=m_end,
                            metadata=ChunkMetadata(
                                class_name=class_name_str,
                                method_name=m_name,
                                imports=imports_list,
                                dependencies=sorted(m_deps) + local_imports,
                                tags=extract_python_event_tags(m_text),
                            ),
                        )
                    )

        elif is_function:
            if pending_flat_nodes:
                chunks.extend(chunk_flat_nodes(pending_flat_nodes))
                pending_flat_nodes = []

            start_line = raw_node.start_point[0] + 1
            end_line = raw_node.end_point[0] + 1
            start_line = max(1, min(start_line, len(lines)))
            end_line = max(1, min(end_line, len(lines)))
            node_text = "".join(lines[start_line - 1 : end_line])

            fn_name_node = actual_node.child_by_field_name("name")
            fn_name = (
                fn_name_node.text.decode("utf-8", errors="ignore")
                if fn_name_node and fn_name_node.text
                else ""
            )

            hierarchical_text = f"{import_text}\n{node_text}\n"

            fn_deps = set()
            words = re.findall(r"\b[A-Za-z_]\w*\b", hierarchical_text)
            for w in words:
                if w not in (fn_name, "") and len(w) > 3:
                    fn_deps.add(w)

            chunks.append(
                Chunk(
                    text=hierarchical_text,
                    start_line=start_line,
                    end_line=end_line,
                    metadata=ChunkMetadata(
                        class_name="",
                        method_name=fn_name,
                        imports=imports_list,
                        dependencies=sorted(fn_deps) + local_imports,
                        type="function",
                        tags=extract_python_event_tags(node_text),
                    ),
                )
            )
        else:
            pending_flat_nodes.append(node)

    if pending_flat_nodes:
        chunks.extend(chunk_flat_nodes(pending_flat_nodes))

    return chunks
