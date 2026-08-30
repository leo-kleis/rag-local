from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK, MAX_TOKENS_PER_CHUNK
from rag_local.core.models import Chunk, ChunkMetadata
from rag_local.parsers.common import count_code_tokens
from rag_local.parsers.typescript.ast import (
    extract_jsx_class_parents,
    extract_jsx_css_classes,
    extract_ts_jsdoc_and_signature,
    get_all_class_names,
    get_class_methods,
)
from rag_local.parsers.typescript.imports import get_class_dependencies


def chunk_ts_class(
    node: Any,
    lines: list[str],
    import_text: str,
    imports_list: list[str],
    local_imports: list[str],
) -> list[Chunk]:
    """Segmenta una clase TS monolítica o jerárquicamente de forma token-aware."""
    start_line = max(1, min(node.start_point[0] + 1, len(lines)))
    end_line = max(1, min(node.end_point[0] + 1, len(lines)))
    node_text = "".join(lines[start_line - 1 : end_line])

    class_names = get_all_class_names(node)
    class_name_str = ",".join(class_names) if class_names else ""
    node_tokens = count_code_tokens(node_text)

    if (
        end_line - start_line + 1
    ) <= MAX_LINES_PER_CHUNK and node_tokens <= MAX_TOKENS_PER_CHUNK:
        method_names = get_class_methods(node)
        method_name_str = ",".join(method_names) if method_names else ""
        return [
            Chunk(
                text=node_text,
                start_line=start_line,
                end_line=end_line,
                metadata=ChunkMetadata(
                    class_name=class_name_str,
                    method_name=method_name_str,
                    imports=imports_list,
                    dependencies=get_class_dependencies(node_text, local_imports),
                    tags=extract_jsx_css_classes(node_text),
                    title=extract_ts_jsdoc_and_signature(node_text)
                    or f"class {class_name_str}",
                    class_parents=extract_jsx_class_parents(node_text),
                ),
            )
        ]

    chunks: list[Chunk] = []
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
                    name_str = name_node.text.decode("utf-8", errors="ignore")
                    if name_str == "constructor":
                        constructor_node = member
                    else:
                        method_nodes.append(member)

    method_nodes.sort(key=lambda x: x.start_point[0])

    if class_body:
        header_end_line = class_body.start_point[0] + 1
        header_end_line = max(start_line, min(header_end_line, end_line))
        class_header_text = "".join(lines[start_line - 1 : header_end_line])
    else:
        class_header_text = f"class {class_name_str} {{\n"

    if constructor_node:
        first_chunk_end_line = constructor_node.end_point[0] + 1
    elif method_nodes:
        first_chunk_end_line = method_nodes[0].start_point[0]
    else:
        first_chunk_end_line = end_line

    first_chunk_end_line = max(start_line, min(first_chunk_end_line, end_line))
    first_chunk_text = "".join(lines[start_line - 1 : first_chunk_end_line])

    if not first_chunk_text.startswith("import"):
        first_chunk_text = f"{import_text}\n{first_chunk_text}"

    first_chunk_deps = get_class_dependencies(first_chunk_text, local_imports)

    chunks.append(
        Chunk(
            text=first_chunk_text,
            start_line=start_line,
            end_line=first_chunk_end_line,
            metadata=ChunkMetadata(
                class_name=class_name_str,
                method_name="constructor" if constructor_node else "",
                imports=imports_list,
                dependencies=first_chunk_deps,
                tags=extract_jsx_css_classes(first_chunk_text),
                title=extract_ts_jsdoc_and_signature(first_chunk_text)
                or f"class {class_name_str}",
                class_parents=extract_jsx_class_parents(first_chunk_text),
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
            if m_name_node and m_name_node.text is not None
            else ""
        )

        hierarchical_text = f"{import_text}\n{class_header_text}\n{m_text}\n}}\n"

        chunks.append(
            Chunk(
                text=hierarchical_text,
                start_line=m_start,
                end_line=m_end,
                metadata=ChunkMetadata(
                    class_name=class_name_str,
                    method_name=m_name,
                    imports=imports_list,
                    dependencies=sorted(local_imports),
                    tags=extract_jsx_css_classes(m_text),
                    title=extract_ts_jsdoc_and_signature(m_text)
                    or f"{class_name_str}.{m_name}",
                    class_parents=extract_jsx_class_parents(m_text),
                ),
            )
        )

    return chunks
