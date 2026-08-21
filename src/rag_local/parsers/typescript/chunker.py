from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK
from rag_local.core.models import Chunk, ChunkMetadata
from rag_local.parsers.common import chunk_flat_lines_window
from rag_local.parsers.typescript.ast import (
    extract_event_and_action_tags,
    extract_jsx_class_parents,
    extract_jsx_css_classes,
)
from rag_local.parsers.typescript.class_chunker import chunk_ts_class
from rag_local.parsers.typescript.decl_chunker import (
    chunk_ts_named_declaration,
    extract_function_var_decl,
)
from rag_local.parsers.typescript.imports import parse_ts_imports
from rag_local.parsers.typescript.switch_chunker import chunk_large_switch_function

# Re-exports para retrocompatibilidad
_extract_function_var_decl = extract_function_var_decl
_chunk_ts_class = chunk_ts_class
_chunk_ts_named_declaration = chunk_ts_named_declaration

_ts_parser: Any = None
_tsx_parser: Any = None


def chunk_flat_lines(
    line_tuples: list[tuple[int, str]],
    imports_list: list[str],
    local_imports: list[str],
) -> list[Chunk]:
    """Divide líneas TypeScript planas con solapamiento cuando no hay clases."""

    def metadata_factory(text: str) -> ChunkMetadata:
        all_tags = sorted(
            set(extract_jsx_css_classes(text) + extract_event_and_action_tags(text))
        )
        return ChunkMetadata(
            class_name="",
            method_name="",
            imports=imports_list,
            dependencies=local_imports,
            tags=all_tags,
            class_parents=extract_jsx_class_parents(text),
        )

    return chunk_flat_lines_window(line_tuples, metadata_factory)


def get_typescript_parser() -> Any:
    """Obtiene o inicializa el Parser de TypeScript de forma perezosa."""
    global _ts_parser
    if _ts_parser is None:
        import tree_sitter_typescript
        from tree_sitter import Language, Parser

        _ts_parser = Parser(Language(tree_sitter_typescript.language_typescript()))
    return _ts_parser


def get_tsx_parser() -> Any:
    """Obtiene o inicializa el Parser de TSX de forma perezosa."""
    global _tsx_parser
    if _tsx_parser is None:
        import tree_sitter_typescript
        from tree_sitter import Language, Parser

        _tsx_parser = Parser(Language(tree_sitter_typescript.language_tsx()))
    return _tsx_parser


def _collect_flat_chunks(
    flat_nodes: list[Any],
    lines: list[str],
    imports_list: list[str],
    local_imports: list[str],
) -> list[Chunk]:
    if not flat_nodes:
        return []
    line_tuples: list[tuple[int, str]] = []
    for fn in flat_nodes:
        for lnum in range(fn.start_point[0] + 1, fn.end_point[0] + 2):
            if 1 <= lnum <= len(lines):
                line_tuples.append((lnum, lines[lnum - 1]))
    seen: set[int] = set()
    unique_tuples = [t for t in line_tuples if not (t[0] in seen or seen.add(t[0]))]
    return chunk_flat_lines(unique_tuples, imports_list, local_imports)


def _chunk_ts_tree(lines: list[str], root_node: Any) -> list[Chunk]:
    """Lógica compartida de Hierarchical AST Chunking para TS y TSX."""
    import_lines, imports_list, _ = parse_ts_imports(lines)
    local_imports = [imp for imp in imports_list if imp.startswith(".")]
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

    pending_flat_nodes: list[Any] = []

    def flush_flat_nodes() -> None:
        nonlocal pending_flat_nodes
        if pending_flat_nodes:
            chunks.extend(
                _collect_flat_chunks(
                    pending_flat_nodes, lines, imports_list, local_imports
                )
            )
            pending_flat_nodes = []

    for child in root_node.children:
        if child.type == "import_statement":
            continue
        node = child
        if node.type == "export_statement":
            for sub in node.children:
                if sub.type in (
                    "class_declaration",
                    "function_declaration",
                    "interface_declaration",
                    "enum_declaration",
                    "type_alias_declaration",
                    "lexical_declaration",
                    "variable_declaration",
                ):
                    node = sub
                    break

        var_fn = extract_function_var_decl(node)
        is_named = (
            node.type
            in (
                "function_declaration",
                "enum_declaration",
                "interface_declaration",
                "type_alias_declaration",
            )
            or var_fn is not None
        )

        if node.type == "class_declaration":
            flush_flat_nodes()
            chunks.extend(
                chunk_ts_class(node, lines, import_text, imports_list, local_imports)
            )
        elif is_named:
            flush_flat_nodes()
            chunks.extend(
                chunk_ts_named_declaration(
                    node, lines, import_text, imports_list, local_imports, var_fn
                )
            )
        else:
            if (node.end_point[0] - node.start_point[0] + 1) > MAX_LINES_PER_CHUNK:
                sw_chunks = chunk_large_switch_function(
                    lines=lines,
                    fn_node=node,
                    fn_name="",
                    import_text=import_text,
                    imports_list=imports_list,
                    local_imports=local_imports,
                    decl_type="switch_case",
                )
                if sw_chunks:
                    flush_flat_nodes()
                    chunks.extend(sw_chunks)
                    continue
            pending_flat_nodes.append(node)

    flush_flat_nodes()
    return chunks


def chunk_typescript(lines: list[str]) -> list[Chunk]:
    """Divide un archivo TypeScript usando tree-sitter."""
    code = "".join(lines)
    tree = get_typescript_parser().parse(bytes(code, "utf8"))
    return _chunk_ts_tree(lines, tree.root_node)


def chunk_tsx(lines: list[str]) -> list[Chunk]:
    """Divide un archivo TSX/JSX usando tree-sitter con gramática TSX."""
    code = "".join(lines)
    tree = get_tsx_parser().parse(bytes(code, "utf8"))
    return _chunk_ts_tree(lines, tree.root_node)
