from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK, MAX_TOKENS_PER_CHUNK
from rag_local.core.models import Chunk, ChunkMetadata
from rag_local.parsers.common import count_code_tokens
from rag_local.parsers.typescript.ast import (
    extract_event_and_action_tags,
    extract_jsx_class_parents,
    extract_jsx_css_classes,
    extract_ts_interface_schema,
    extract_ts_jsdoc_and_signature,
)
from rag_local.parsers.typescript.switch_chunker import chunk_large_switch_function


def extract_function_var_decl(node: Any) -> tuple[str, str] | None:
    """Extrae (nombre, tipo) si el nodo es una declaración de variable con función."""
    if node.type in ("lexical_declaration", "variable_declaration"):
        for child in node.children:
            if child.type == "variable_declarator":
                val_node = child.child_by_field_name("value")
                if val_node and val_node.type in (
                    "arrow_function",
                    "function_expression",
                    "function",
                ):
                    name_node = child.child_by_field_name("name")
                    if name_node and name_node.text is not None:
                        name_str = name_node.text.decode(
                            "utf-8", errors="ignore"
                        ).strip()
                        return name_str, "function"
    return None


_CONTROL_STATEMENT_TYPES = {
    "if_statement",
    "for_statement",
    "for_in_statement",
    "for_of_statement",
    "while_statement",
    "do_statement",
    "try_statement",
    "switch_statement",
}


def _subdivide_large_ts_function(
    node: Any,
    body_node: Any,
    lines: list[str],
    decl_name: str,
    decl_type: str,
    import_text: str,
    imports_list: list[str],
    local_imports: list[str],
    start_line: int,
    end_line: int,
    ts_title: str,
) -> list[Chunk]:
    """Subdivide el cuerpo de una funcion TS extensa por bloques de control."""
    body_children = [
        c for c in body_node.children if c.type not in ("{", "}", "comment")
    ]
    if not body_children:
        return []

    first_child = body_children[0]
    header_end_line = max(start_line, min(first_child.start_point[0], end_line))
    fn_header_text = "".join(lines[start_line - 1 : header_end_line])
    if not fn_header_text.strip():
        fn_header_text = f"function {decl_name}(...) {{\n"
    elif not fn_header_text.endswith("\n"):
        fn_header_text += "\n"

    chunks: list[Chunk] = []

    def make_subchunk(sub_nodes: list[Any]) -> None:
        if not sub_nodes:
            return
        sub_start = max(1, min(sub_nodes[0].start_point[0] + 1, len(lines)))
        sub_end = max(1, min(sub_nodes[-1].end_point[0] + 1, len(lines)))
        sub_text = "".join(lines[sub_start - 1 : sub_end])

        chunk_code = (
            f"{import_text}\n{fn_header_text}{sub_text}\n}}\n"
            if import_text
            else f"{fn_header_text}{sub_text}\n}}\n"
        )
        sub_tags = set(extract_jsx_css_classes(sub_text))
        sub_tags.update(extract_event_and_action_tags(sub_text))
        sub_tags.discard("")

        chunks.append(
            Chunk(
                text=chunk_code,
                start_line=sub_start,
                end_line=sub_end,
                metadata=ChunkMetadata(
                    class_name=decl_name,
                    method_name=decl_name,
                    imports=imports_list,
                    dependencies=local_imports,
                    type=decl_type or "function",
                    tags=sorted(sub_tags),
                    title=ts_title,
                    class_parents=extract_jsx_class_parents(sub_text),
                ),
            )
        )

    pending_nodes: list[Any] = []
    for child in body_children:
        if child.type in _CONTROL_STATEMENT_TYPES:
            if pending_nodes:
                make_subchunk(pending_nodes)
                pending_nodes = []
            make_subchunk([child])
        else:
            pending_nodes.append(child)
            cur_start = pending_nodes[0].start_point[0] + 1
            cur_end = pending_nodes[-1].end_point[0] + 1
            if (cur_end - cur_start + 1) >= 50:
                make_subchunk(pending_nodes)
                pending_nodes = []

    if pending_nodes:
        make_subchunk(pending_nodes)

    return chunks


def chunk_ts_named_declaration(
    node: Any,
    lines: list[str],
    import_text: str,
    imports_list: list[str],
    local_imports: list[str],
    var_fn_info: tuple[str, str] | None,
) -> list[Chunk]:
    """Segmenta funciones, interfaces, enums y type aliases en TypeScript."""
    name_node = node.child_by_field_name("name")
    decl_name = ""
    if name_node and name_node.text is not None:
        decl_name = name_node.text.decode("utf-8", errors="ignore")
    elif var_fn_info:
        decl_name = var_fn_info[0]

    type_map = {
        "function_declaration": "function",
        "enum_declaration": "enum",
        "interface_declaration": "interface",
        "type_alias_declaration": "type_alias",
    }
    decl_type = type_map.get(node.type, "function" if var_fn_info is not None else "")

    # Intentar segmentación por switch si es un reducer/función grande
    if node.type in (
        "function_declaration",
        "lexical_declaration",
        "variable_declaration",
    ):
        switch_chunks = chunk_large_switch_function(
            lines=lines,
            fn_node=node,
            fn_name=decl_name,
            import_text=import_text,
            imports_list=imports_list,
            local_imports=local_imports,
            decl_type=decl_type or "function",
        )
        if switch_chunks:
            return switch_chunks

    start_line = max(1, min(node.start_point[0] + 1, len(lines)))
    end_line = max(1, min(node.end_point[0] + 1, len(lines)))
    node_text = "".join(lines[start_line - 1 : end_line])

    ts_title = (
        extract_ts_jsdoc_and_signature(node_text)
        or f"{decl_type or 'declaration'} {decl_name}".strip()
    )

    # Subdividir funciones extensas por bloques de control
    node_tokens = count_code_tokens(node_text)
    if decl_type == "function" and (
        (end_line - start_line + 1) > MAX_LINES_PER_CHUNK
        or node_tokens > MAX_TOKENS_PER_CHUNK
    ):
        body_node = None
        if node.type == "function_declaration":
            body_node = node.child_by_field_name("body")
        elif node.type in ("lexical_declaration", "variable_declaration"):
            for child in node.children:
                if child.type == "variable_declarator":
                    val_node = child.child_by_field_name("value")
                    if val_node:
                        body_node = val_node.child_by_field_name("body")
                        break

        if body_node and body_node.type == "statement_block":
            sub_chunks = _subdivide_large_ts_function(
                node=node,
                body_node=body_node,
                lines=lines,
                decl_name=decl_name,
                decl_type=decl_type,
                import_text=import_text,
                imports_list=imports_list,
                local_imports=local_imports,
                start_line=start_line,
                end_line=end_line,
                ts_title=ts_title,
            )
            if sub_chunks:
                return sub_chunks

    hierarchical_text = f"{import_text}\n{node_text}\n"
    tags_set = set(extract_jsx_css_classes(node_text))
    tags_set.update(extract_event_and_action_tags(node_text))
    tags_set.discard("")

    ts_schema = (
        extract_ts_interface_schema(node)
        if node.type in ("interface_declaration", "type_alias_declaration")
        else ""
    )

    return [
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
                tags=sorted(tags_set),
                title=ts_title,
                class_parents=extract_jsx_class_parents(node_text),
                payload_schema=ts_schema,
            ),
        )
    ]
