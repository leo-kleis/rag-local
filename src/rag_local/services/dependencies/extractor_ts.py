import contextlib
from pathlib import Path
from typing import Any

from rag_local.core.logging import logger
from rag_local.parsers.typescript.ast import extract_ts_interface_schema
from rag_local.parsers.typescript.chunker import get_typescript_parser
from rag_local.services.db_schemas import DependencySymbol
from rag_local.services.embeddings import get_embeddings


def _find_all_node_modules(project_path: Path) -> list[Path]:
    """Localiza todos los directorios node_modules del proyecto (soporte monorepos)."""
    candidates: list[Path] = []
    root_nm = project_path / "node_modules"
    if root_nm.is_dir():
        candidates.append(root_nm)

    ignored = (".", "dist", "vendor", "__pycache__")
    for sub in project_path.iterdir():
        if not sub.is_dir() or sub.name.startswith(ignored):
            continue
        sub_nm = sub / "node_modules"
        if sub_nm.is_dir() and sub_nm not in candidates:
            candidates.append(sub_nm)
        for nested in sub.iterdir():
            if not nested.is_dir() or nested.name.startswith((".", "dist")):
                continue
            nested_nm = nested / "node_modules"
            if nested_nm.is_dir() and nested_nm not in candidates:
                candidates.append(nested_nm)
    return candidates


def _collect_dts_files(node_modules_list: list[Path], package_name: str) -> list[Path]:
    """Recolecta archivos .d.ts/.d.mts desde el paquete, @types o .prisma."""
    found_files: list[Path] = []

    for node_modules in node_modules_list:
        # 1. Directorio del paquete
        pkg_dir = node_modules / package_name
        if pkg_dir.is_dir():
            for f in pkg_dir.rglob("*.d.ts"):
                found_files.append(f)
            for f in pkg_dir.rglob("*.d.mts"):
                found_files.append(f)

        # 2. Directorio @types
        types_dir = node_modules / "@types" / package_name
        if types_dir.is_dir():
            for f in types_dir.rglob("*.d.ts"):
                found_files.append(f)
            for f in types_dir.rglob("*.d.mts"):
                found_files.append(f)

        # 3. Soporte especial para Prisma (.prisma/client)
        if "prisma" in package_name.lower():
            prisma_dir = node_modules / ".prisma" / "client"
            if prisma_dir.is_dir():
                for f in prisma_dir.rglob("*.d.ts"):
                    found_files.append(f)

    clean_files: list[Path] = []
    for f in found_files:
        rel = str(f).lower()
        if any(p in rel for p in ("test", "tests", "__tests__", "spec", "benchmarks")):
            continue
        if f not in clean_files:
            clean_files.append(f)
        if len(clean_files) >= 40:
            break

    return clean_files


def _unwrap_ts_node(node: Any) -> Any:
    """Desenvuelve nodos export_statement o ambient_declaration al nodo semántico."""
    curr = node
    while curr.type in ("export_statement", "ambient_declaration"):
        found = None
        for sub in curr.children:
            if sub.type in (
                "interface_declaration",
                "type_alias_declaration",
                "function_signature",
                "function_declaration",
                "class_declaration",
                "enum_declaration",
                "ambient_declaration",
            ):
                found = sub
                break
        if found:
            curr = found
        else:
            break
    return curr


def extract_ts_package_symbols(
    project_path: Path,
    package_name: str,
    package_version: str,
    max_symbols: int = 250,
) -> list[DependencySymbol]:
    """Extrae contratos de tipos, interfaces y firmas de un paquete TypeScript/Node."""
    node_modules_list = _find_all_node_modules(project_path)
    if not node_modules_list:
        logger.warning(
            f"No se encontró ningún node_modules en {project_path} para {package_name}."
        )
        return []

    dts_files = _collect_dts_files(node_modules_list, package_name)
    if not dts_files:
        return []

    parser = get_typescript_parser()
    symbols_data: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()

    for file_path in dts_files:
        content = ""
        with contextlib.suppress(Exception):
            content = file_path.read_text(encoding="utf-8", errors="replace")
        if not content:
            continue

        tree = parser.parse(content.encode("utf-8"))
        root = tree.root_node
        lines = content.splitlines(keepends=True)
        mod_name = (
            f"{package_name}/{file_path.stem}"
            if file_path.stem != "index"
            else package_name
        )

        for child in root.children:
            actual_node = _unwrap_ts_node(child)
            node_type = actual_node.type
            name_node = actual_node.child_by_field_name("name")
            name_str = (
                name_node.text.decode("utf-8", errors="ignore")
                if name_node and name_node.text
                else ""
            )

            if not name_str or name_str.startswith("_") or name_str in seen_symbols:
                continue

            seen_symbols.add(name_str)
            start_l = actual_node.start_point[0]
            end_l = actual_node.end_point[0]
            decl_snippet = "".join(lines[start_l : min(end_l + 1, start_l + 35)])

            # Extraer comentarios JSDoc anteriores al nodo
            docstring = ""
            if start_l > 0:
                prev_line = lines[start_l - 1].strip()
                if (
                    "*/" in prev_line
                    or prev_line.startswith("*")
                    or prev_line.startswith("//")
                ):
                    docstring = prev_line

            sym_type = "type"
            sig = f"{name_str}"

            if node_type == "interface_declaration":
                sym_type = "interface"
                schema = extract_ts_interface_schema(actual_node)
                if schema:
                    sig = f"interface {name_str} {{ {schema} }}"
                else:
                    sig = f"interface {name_str}"
            elif node_type == "type_alias_declaration":
                sym_type = "type_alias"
                sig = f"type {name_str} = ..."
            elif node_type in ("function_signature", "function_declaration"):
                sym_type = "function"
                first_line = lines[start_l].strip().rstrip("{").rstrip(";")
                sig = first_line or f"function {name_str}(...)"
            elif node_type == "class_declaration":
                sym_type = "class"
                sig = f"class {name_str}"
            elif node_type == "enum_declaration":
                sym_type = "enum"
                sig = f"enum {name_str}"

            symbols_data.append(
                {
                    "symbol_name": name_str,
                    "symbol_type": sym_type,
                    "signature": sig,
                    "docstring": docstring,
                    "declaration_text": decl_snippet.strip(),
                    "source_module": mod_name,
                }
            )

            if len(symbols_data) >= max_symbols:
                break
        if len(symbols_data) >= max_symbols:
            break

    if not symbols_data:
        return []

    # Generar embeddings vectoriales
    text_prompts = [
        (
            f"{s['symbol_name']} ({package_name} {s['symbol_type']}): "
            f"{s['signature']}\n{s['docstring']}"
        )
        for s in symbols_data
    ]
    try:
        embeddings_res = get_embeddings(text_prompts)
        vectors: list[list[float]] = embeddings_res or [
            [0.0] * 768 for _ in symbols_data
        ]
    except Exception as e:
        logger.warning(
            f"Error al generar embeddings para TS {package_name}, usando ceros: {e}"
        )
        vectors = [[0.0] * 768 for _ in symbols_data]

    results: list[DependencySymbol] = []
    for i, s in enumerate(symbols_data):
        vec = vectors[i] if i < len(vectors) else [0.0] * 768
        symbol_id = f"npm:{package_name}@{package_version}:{s['symbol_name']}"
        results.append(
            DependencySymbol(
                id=symbol_id,
                vector=vec,
                language="typescript",
                package_manager="pnpm",
                package_name=package_name,
                package_version=package_version,
                source_module=s["source_module"],
                symbol_name=s["symbol_name"],
                symbol_type=s["symbol_type"],
                signature=s["signature"],
                docstring=s["docstring"],
                declaration_text=s["declaration_text"],
            )
        )

    logger.info(
        f"Extraídos {len(results)} símbolos para TS: {package_name}@{package_version}"
    )
    return results
