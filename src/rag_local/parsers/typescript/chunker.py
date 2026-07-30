from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK, OVERLAP_LINES
from rag_local.core.models import Chunk, ChunkMetadata
from rag_local.parsers.typescript.ast import (
    extract_jsx_css_classes,
    get_all_class_names,
    get_class_methods,
)
from rag_local.parsers.typescript.imports import (
    get_class_dependencies,
    parse_ts_imports,
)


def chunk_flat_lines(
    line_tuples: list[tuple[int, str]],
    imports_list: list[str],
    local_imports: list[str],
) -> list[Chunk]:
    """Divide líneas TypeScript planas con solapamiento cuando no hay clases."""
    chunks = []
    total_lines = len(line_tuples)
    if total_lines == 0:
        return []

    if total_lines <= MAX_LINES_PER_CHUNK:
        text = "".join(lc for _, lc in line_tuples)
        start_line = line_tuples[0][0]
        end_line = line_tuples[-1][0]
        css_tags = extract_jsx_css_classes(text)

        chunks.append(
            Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                metadata=ChunkMetadata(
                    class_name="",
                    method_name="",
                    imports=imports_list,
                    dependencies=local_imports,
                    tags=css_tags,
                ),
            )
        )
        return chunks

    start = 0
    while start < total_lines:
        end = min(start + MAX_LINES_PER_CHUNK, total_lines)
        chunk_lines = line_tuples[start:end]
        text = "".join(lc for _, lc in chunk_lines)
        start_line = chunk_lines[0][0]
        end_line = chunk_lines[-1][0]
        css_tags = extract_jsx_css_classes(text)

        chunks.append(
            Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                metadata=ChunkMetadata(
                    class_name="",
                    method_name="",
                    imports=imports_list,
                    dependencies=local_imports,
                    tags=css_tags,
                ),
            )
        )

        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES
        if start >= total_lines - OVERLAP_LINES:
            break

    return chunks


_ts_parser: Any = None


def get_typescript_parser() -> Any:
    """Obtiene o inicializa el Parser de TypeScript de forma perezosa."""
    global _ts_parser
    if _ts_parser is None:
        import tree_sitter_typescript
        from tree_sitter import Language, Parser

        _ts_parser = Parser(Language(tree_sitter_typescript.language_typescript()))
    return _ts_parser


_tsx_parser: Any = None


def get_tsx_parser() -> Any:
    """Obtiene o inicializa el Parser de TSX de forma perezosa."""
    global _tsx_parser
    if _tsx_parser is None:
        import tree_sitter_typescript
        from tree_sitter import Language, Parser

        _tsx_parser = Parser(Language(tree_sitter_typescript.language_tsx()))
    return _tsx_parser


def _chunk_ts_tree(lines: list[str], root_node: Any) -> list[Chunk]:
    """Lógica compartida de Hierarchical AST Chunking para TS y TSX."""

    import_lines, imports_list, _ = parse_ts_imports(lines)
    local_imports = [imp for imp in imports_list if imp.startswith(".")]
    import_text = "".join(import_lines) if import_lines else ""

    chunks: list[Chunk] = []

    # Registrar el chunk de imports inicial
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

    # Buscar nodos de primer nivel
    nodes = []
    for child in root_node.children:
        if child.type == "import_statement":
            continue
        inner = child
        if inner.type == "export_statement":
            for sub in inner.children:
                if sub.type in (
                    "class_declaration",
                    "function_declaration",
                    "interface_declaration",
                    "enum_declaration",
                    "type_alias_declaration",
                ):
                    inner = sub
                    break
        nodes.append(inner)

    def chunk_flat_nodes(flat_nodes) -> list[Chunk]:
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
        return chunk_flat_lines(unique_line_tuples, imports_list, local_imports)

    pending_flat_nodes = []
    for node in nodes:
        is_class = node.type == "class_declaration"
        is_named_declaration = node.type in (
            "function_declaration",
            "enum_declaration",
            "interface_declaration",
            "type_alias_declaration",
        )

        if is_class:
            if pending_flat_nodes:
                chunks.extend(chunk_flat_nodes(pending_flat_nodes))
                pending_flat_nodes = []

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            start_line = max(1, min(start_line, len(lines)))
            end_line = max(1, min(end_line, len(lines)))

            node_text = "".join(lines[start_line - 1 : end_line])
            class_names = get_all_class_names(node)
            class_name_str = ",".join(class_names) if class_names else ""

            # Si la clase es pequeña, procesarla como un único fragmento completo
            if (end_line - start_line + 1) <= MAX_LINES_PER_CHUNK:
                method_names = get_class_methods(node)
                method_name_str = ",".join(method_names) if method_names else ""
                chunks.append(
                    Chunk(
                        text=node_text,
                        start_line=start_line,
                        end_line=end_line,
                        metadata=ChunkMetadata(
                            class_name=class_name_str,
                            method_name=method_name_str,
                            imports=imports_list,
                            dependencies=get_class_dependencies(
                                node_text, local_imports
                            ),
                            tags=extract_jsx_css_classes(node_text),
                        ),
                    )
                )
            else:
                # Segmentar clase grande jerárquicamente
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

                first_chunk_end_line = max(
                    start_line, min(first_chunk_end_line, end_line)
                )
                first_chunk_text = "".join(lines[start_line - 1 : first_chunk_end_line])

                if not first_chunk_text.startswith("import"):
                    first_chunk_text = f"{import_text}\n{first_chunk_text}"

                first_chunk_deps = get_class_dependencies(
                    first_chunk_text, local_imports
                )

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
                    m_name = ""
                    if m_name_node and m_name_node.text is not None:
                        m_name = m_name_node.text.decode("utf-8", errors="ignore")

                    hierarchical_text = (
                        f"{import_text}\n{class_header_text}\n{m_text}\n}}\n"
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
                                dependencies=sorted(local_imports),
                                tags=extract_jsx_css_classes(m_text),
                            ),
                        )
                    )
        elif is_named_declaration:
            if pending_flat_nodes:
                chunks.extend(chunk_flat_nodes(pending_flat_nodes))
                pending_flat_nodes = []

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            start_line = max(1, min(start_line, len(lines)))
            end_line = max(1, min(end_line, len(lines)))
            node_text = "".join(lines[start_line - 1 : end_line])

            name_node = node.child_by_field_name("name")
            decl_name = ""
            if name_node and name_node.text is not None:
                decl_name = name_node.text.decode("utf-8", errors="ignore")

            type_map = {
                "function_declaration": "function",
                "enum_declaration": "enum",
                "interface_declaration": "interface",
                "type_alias_declaration": "type_alias",
            }
            decl_type = type_map.get(node.type, "")

            hierarchical_text = f"{import_text}\n{node_text}\n"

            chunks.append(
                Chunk(
                    text=hierarchical_text,
                    start_line=start_line,
                    end_line=end_line,
                    metadata=ChunkMetadata(
                        class_name=decl_name,
                        method_name="",
                        imports=imports_list,
                        dependencies=local_imports,
                        type=decl_type,
                        tags=extract_jsx_css_classes(node_text),
                    ),
                )
            )
        else:
            pending_flat_nodes.append(node)

    if pending_flat_nodes:
        chunks.extend(chunk_flat_nodes(pending_flat_nodes))

    return chunks


def chunk_typescript(lines: list[str]) -> list[Chunk]:
    """Divide un archivo TypeScript usando tree-sitter."""
    code = "".join(lines)
    parser = get_typescript_parser()
    tree = parser.parse(bytes(code, "utf8"))
    return _chunk_ts_tree(lines, tree.root_node)


def chunk_tsx(lines: list[str]) -> list[Chunk]:
    """Divide un archivo TSX/JSX usando tree-sitter con gramática TSX."""
    code = "".join(lines)
    parser = get_tsx_parser()
    tree = parser.parse(bytes(code, "utf8"))
    return _chunk_ts_tree(lines, tree.root_node)
