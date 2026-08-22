from typing import Any

from rag_local.core.models import Chunk, ChunkMetadata
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
                title=extract_ts_jsdoc_and_signature(node_text)
                or f"{decl_type or 'declaration'} {decl_name}".strip(),
                class_parents=extract_jsx_class_parents(node_text),
                payload_schema=ts_schema,
            ),
        )
    ]
