import re
from typing import Any

from rag_local.core.config import MAX_LINES_PER_CHUNK
from rag_local.core.models import Chunk, ChunkMetadata
from rag_local.parsers.typescript.ast import (
    extract_event_and_action_tags,
    extract_jsx_class_parents,
    extract_jsx_css_classes,
)


def find_switch_statement(node: Any) -> Any | None:
    """Busca recursivamente el primer nodo switch_statement dentro de una función."""
    if node.type == "switch_statement":
        return node

    for child in node.children:
        # Evitar entrar en clases anidadas
        if child.type in ("class_declaration", "class_body"):
            continue
        res = find_switch_statement(child)
        if res is not None:
            return res
    return None


def extract_switch_cases(switch_node: Any, lines: list[str]) -> list[dict[str, Any]]:
    """Extrae todos los bloques switch_case y switch_default de un switch_statement."""
    cases: list[dict[str, Any]] = []

    # En tree-sitter, el body del switch suele ser switch_body
    body_node = None
    for child in switch_node.children:
        if child.type == "switch_body":
            body_node = child
            break

    target_nodes = body_node.children if body_node else switch_node.children

    for child in target_nodes:
        if child.type in ("switch_case", "switch_default"):
            is_default = child.type == "switch_default"
            case_val = "default"

            if not is_default:
                val_node = child.child_by_field_name("value")
                if val_node and val_node.text is not None:
                    raw_val = val_node.text.decode("utf-8", errors="ignore").strip()
                    # Limpiar comillas si es un string literal
                    if (raw_val.startswith("'") and raw_val.endswith("'")) or (
                        raw_val.startswith('"') and raw_val.endswith('"')
                    ):
                        case_val = raw_val[1:-1]
                    else:
                        case_val = raw_val
                else:
                    # Fallback buscando nodo después de la palabra clave 'case'
                    for sub in child.children:
                        if sub.type not in ("case", ":") and sub.text:
                            sub_text = sub.text.decode("utf-8", errors="ignore").strip()
                            is_quoted = (
                                sub_text.startswith("'") and sub_text.endswith("'")
                            ) or (sub_text.startswith('"') and sub_text.endswith('"'))
                            case_val = sub_text[1:-1] if is_quoted else sub_text
                            break

            c_start = child.start_point[0] + 1
            c_end = child.end_point[0] + 1
            c_start = max(1, min(c_start, len(lines)))
            c_end = max(1, min(c_end, len(lines)))
            c_text = "".join(lines[c_start - 1 : c_end])

            cases.append(
                {
                    "case_val": case_val,
                    "is_default": is_default,
                    "start_line": c_start,
                    "end_line": c_end,
                    "text": c_text,
                }
            )

    return cases


def find_enclosing_or_inner_function_name(node: Any) -> str:
    """Busca el nombre de la función contenedora o interna más relevante."""
    if node.type in (
        "function_declaration",
        "method_definition",
        "function_expression",
    ):
        name_node = node.child_by_field_name("name")
        if name_node and name_node.text:
            return name_node.text.decode("utf-8", errors="ignore").strip()

    for child in node.children:
        if child.type in ("class_declaration", "class_body"):
            continue
        res = find_enclosing_or_inner_function_name(child)
        if res:
            return res
    return ""


def chunk_large_switch_function(
    lines: list[str],
    fn_node: Any,
    fn_name: str,
    import_text: str,
    imports_list: list[str],
    local_imports: list[str],
    decl_type: str = "function",
) -> list[Chunk] | None:
    """Segmenta jerárquicamente funciones o reducers grandes que contienen un switch."""
    fn_start = fn_node.start_point[0] + 1
    fn_end = fn_node.end_point[0] + 1
    fn_start = max(1, min(fn_start, len(lines)))
    fn_end = max(1, min(fn_end, len(lines)))

    total_lines = fn_end - fn_start + 1
    # Si la función completa cabe dentro del límite de chunk, no subdividir
    if total_lines <= MAX_LINES_PER_CHUNK:
        return None

    switch_node = find_switch_statement(fn_node)
    if switch_node is None:
        return None

    cases = extract_switch_cases(switch_node, lines)
    if not cases:
        return None

    if not fn_name:
        fn_name = find_enclosing_or_inner_function_name(fn_node) or "switchHandler"

    # Extraer cabecera de la función desde su inicio hasta antes del primer case
    first_case_start = cases[0]["start_line"]
    header_end = max(fn_start, min(first_case_start - 1, fn_end))
    fn_header = "".join(lines[fn_start - 1 : header_end]).rstrip()

    if not fn_header:
        fn_header = f"function {fn_name}(state, action) {{\n  switch (action.type) {{"

    chunks: list[Chunk] = []
    chunk_type = "reducer_case" if "reducer" in fn_name.lower() else "switch_case"

    for case in cases:
        case_val = case["case_val"]
        c_start = case["start_line"]
        c_end = case["end_line"]
        c_text = case["text"].rstrip()

        hierarchical_text = f"{import_text}\n{fn_header}\n{c_text}\n  }}\n}}\n"

        tags_set: set[str] = set()
        if case_val and case_val != "default":
            # Normalizar tag de acción
            clean_action = re.sub(r"^[A-Za-z0-9_]+\.", "", case_val)
            tags_set.add(f"action:{clean_action}")

        tags_set.update(extract_jsx_css_classes(c_text))
        tags_set.update(extract_event_and_action_tags(c_text))
        tags_set.discard("")

        method_name = f"{fn_name}:{case_val}" if fn_name else f"case:{case_val}"

        chunks.append(
            Chunk(
                text=hierarchical_text,
                start_line=c_start,
                end_line=c_end,
                metadata=ChunkMetadata(
                    class_name=fn_name,
                    method_name=method_name,
                    imports=imports_list,
                    dependencies=sorted(local_imports),
                    tags=sorted(tags_set),
                    type=chunk_type,
                    class_parents=extract_jsx_class_parents(c_text),
                ),
            )
        )

    return chunks
