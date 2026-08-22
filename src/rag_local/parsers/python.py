from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK
from rag_local.core.models import Chunk, ChunkMetadata
from rag_local.parsers.common import (
    chunk_flat_lines_window,
    extract_dependency_identifiers,
)
from rag_local.parsers.python_helpers import (
    extract_python_class_schema,
    extract_python_docstring,
    extract_python_event_tags,
    extract_python_signature,
    get_class_methods_py,
    get_python_parser,
    parse_py_imports,
)


def _chunk_python_class(
    raw_node: Any,
    actual_node: Any,
    lines: list[str],
    import_text: str,
    imports_list: list[str],
    local_imports: list[str],
) -> list[Chunk]:
    """Segmenta un nodo de clase Python de forma monolítica o jerárquica."""
    start_line = max(1, min(raw_node.start_point[0] + 1, len(lines)))
    end_line = max(1, min(raw_node.end_point[0] + 1, len(lines)))
    node_text = "".join(lines[start_line - 1 : end_line])

    class_name_node = actual_node.child_by_field_name("name")
    class_name_str = (
        class_name_node.text.decode("utf-8", errors="ignore")
        if class_name_node and class_name_node.text
        else ""
    )

    class_schema = extract_python_class_schema(actual_node)

    if (end_line - start_line + 1) <= MAX_LINES_PER_CHUNK:
        method_names = get_class_methods_py(actual_node)
        method_name_str = ",".join(method_names) if method_names else ""
        deps = extract_dependency_identifiers(node_text, excluded={class_name_str})

        return [
            Chunk(
                text=node_text,
                start_line=start_line,
                end_line=end_line,
                metadata=ChunkMetadata(
                    class_name=class_name_str,
                    method_name=method_name_str,
                    imports=imports_list,
                    dependencies=deps + local_imports,
                    tags=extract_python_event_tags(node_text),
                    title=extract_python_docstring(actual_node)
                    or f"class {class_name_str}",
                    payload_schema=class_schema,
                ),
            )
        ]

    # Para clases grandes, generar chunk inicial + chunks por método
    chunks: list[Chunk] = []
    body_node = actual_node.child_by_field_name("body")
    init_node = None
    method_nodes = []
    if body_node:
        for member in body_node.children:
            if member.type == "function_definition":
                name_node = member.child_by_field_name("name")
                if name_node and name_node.text:
                    name_str = name_node.text.decode("utf-8", errors="ignore")
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

    first_chunk_end_line = max(start_line, min(first_chunk_end_line, end_line))
    first_chunk_text = "".join(lines[start_line - 1 : first_chunk_end_line])

    if not first_chunk_text.strip().startswith(("import", "from")):
        first_chunk_text = f"{import_text}\n{first_chunk_text}"

    first_chunk_deps = extract_dependency_identifiers(
        first_chunk_text, excluded={class_name_str, "__init__"}
    )

    chunks.append(
        Chunk(
            text=first_chunk_text,
            start_line=start_line,
            end_line=first_chunk_end_line,
            metadata=ChunkMetadata(
                class_name=class_name_str,
                method_name="__init__" if init_node else "",
                imports=imports_list,
                dependencies=first_chunk_deps + local_imports,
                tags=extract_python_event_tags(first_chunk_text),
                title=extract_python_docstring(actual_node)
                or f"class {class_name_str}",
                payload_schema=class_schema,
            ),
        )
    )

    for m_node in method_nodes:
        m_start = max(1, min(m_node.start_point[0] + 1, len(lines)))
        m_end = max(1, min(m_node.end_point[0] + 1, len(lines)))
        m_text = "".join(lines[m_start - 1 : m_end])

        m_name_node = m_node.child_by_field_name("name")
        m_name = (
            m_name_node.text.decode("utf-8", errors="ignore")
            if m_name_node and m_name_node.text
            else ""
        )

        hierarchical_text = f"{import_text}\n{class_header_text}\n{m_text}\n"
        m_deps = extract_dependency_identifiers(
            hierarchical_text, excluded={class_name_str, m_name}
        )

        chunks.append(
            Chunk(
                text=hierarchical_text,
                start_line=m_start,
                end_line=m_end,
                metadata=ChunkMetadata(
                    class_name=class_name_str,
                    method_name=m_name,
                    imports=imports_list,
                    dependencies=m_deps + local_imports,
                    tags=extract_python_event_tags(m_text),
                    title=extract_python_docstring(m_node)
                    or extract_python_signature(m_node),
                ),
            )
        )

    return chunks


def _chunk_python_function(
    raw_node: Any,
    actual_node: Any,
    lines: list[str],
    import_text: str,
    imports_list: list[str],
    local_imports: list[str],
) -> Chunk:
    """Segmenta un nodo de función de nivel superior en Python."""
    start_line = max(1, min(raw_node.start_point[0] + 1, len(lines)))
    end_line = max(1, min(raw_node.end_point[0] + 1, len(lines)))
    node_text = "".join(lines[start_line - 1 : end_line])

    fn_name_node = actual_node.child_by_field_name("name")
    fn_name = (
        fn_name_node.text.decode("utf-8", errors="ignore")
        if fn_name_node and fn_name_node.text
        else ""
    )

    hierarchical_text = f"{import_text}\n{node_text}\n"
    fn_deps = extract_dependency_identifiers(hierarchical_text, excluded={fn_name})

    return Chunk(
        text=hierarchical_text,
        start_line=start_line,
        end_line=end_line,
        metadata=ChunkMetadata(
            class_name="",
            method_name=fn_name,
            imports=imports_list,
            dependencies=fn_deps + local_imports,
            type="function",
            tags=extract_python_event_tags(node_text),
            title=extract_python_docstring(actual_node)
            or extract_python_signature(actual_node),
        ),
    )


def chunk_python(lines: list[str]) -> list[Chunk]:
    """Divide un archivo de Python (.py) usando tree-sitter.

    Implementa Hierarchical AST Chunking.
    """
    code = "".join(lines)
    parser = get_python_parser()
    tree = parser.parse(bytes(code, "utf8"))
    root_node = tree.root_node

    import_lines, imports_list, _ = parse_py_imports(lines)
    local_imports = [
        imp for imp in imports_list if "from ." in imp or "import ." in imp
    ]
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

    nodes = [
        child
        for child in root_node.children
        if child.type not in ("import_statement", "import_from_statement")
    ]

    def metadata_factory(text: str) -> ChunkMetadata:
        return ChunkMetadata(
            class_name="",
            method_name="",
            imports=imports_list,
            dependencies=local_imports,
            tags=extract_python_event_tags(text),
        )

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
        return chunk_flat_lines_window(unique_line_tuples, metadata_factory)

    pending_flat_nodes = []
    for node in nodes:
        raw_node = node
        actual_node = node
        if node.type == "decorated_definition":
            for sub in node.children:
                if sub.type in ("class_definition", "function_definition"):
                    actual_node = sub
                    break

        if actual_node.type == "class_definition":
            if pending_flat_nodes:
                chunks.extend(chunk_flat_nodes(pending_flat_nodes))
                pending_flat_nodes = []
            chunks.extend(
                _chunk_python_class(
                    raw_node,
                    actual_node,
                    lines,
                    import_text,
                    imports_list,
                    local_imports,
                )
            )
        elif actual_node.type == "function_definition":
            if pending_flat_nodes:
                chunks.extend(chunk_flat_nodes(pending_flat_nodes))
                pending_flat_nodes = []
            chunks.append(
                _chunk_python_function(
                    raw_node,
                    actual_node,
                    lines,
                    import_text,
                    imports_list,
                    local_imports,
                )
            )
        else:
            pending_flat_nodes.append(node)

    if pending_flat_nodes:
        chunks.extend(chunk_flat_nodes(pending_flat_nodes))

    return chunks


__all__ = [
    "chunk_python",
    "extract_python_docstring",
    "extract_python_event_tags",
    "extract_python_signature",
    "get_python_parser",
    "parse_py_imports",
]
