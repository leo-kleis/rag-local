import contextlib
from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.parsers.python_helpers import (
    extract_python_class_schema,
    extract_python_docstring,
    extract_python_signature,
    get_class_methods_py,
    get_python_parser,
)
from rag_local.services.db_schemas import DependencySymbol
from rag_local.services.embeddings import get_embeddings


def _find_site_packages(project_path: Path) -> list[Path]:
    """Localiza los directorios site-packages en el .venv del proyecto."""
    candidates = [
        project_path / ".venv" / "Lib" / "site-packages",
        project_path / ".venv" / "lib" / "site-packages",
    ]
    # También buscar en posibles rutas unix-like dentro de Windows/WSL
    for p in (project_path / ".venv" / "lib").glob("python*/site-packages"):
        candidates.append(p)

    return [p for p in candidates if p.is_dir()]


def _find_package_dir(site_packages_dirs: list[Path], package_name: str) -> Path | None:
    """Busca el directorio o archivo del paquete en site-packages."""
    norm_name = package_name.lower().replace("-", "_")
    hyphen_name = package_name.lower().replace("_", "-")

    for sp in site_packages_dirs:
        for name_variant in (norm_name, hyphen_name, package_name):
            dir_candidate = sp / name_variant
            if dir_candidate.is_dir():
                return dir_candidate
            file_candidate = sp / f"{name_variant}.py"
            if file_candidate.is_file():
                return file_candidate
            pyi_candidate = sp / f"{name_variant}.pyi"
            if pyi_candidate.is_file():
                return pyi_candidate

        # Búsqueda por .dist-info/top_level.txt
        for pattern in (f"{norm_name}*.dist-info", f"{hyphen_name}*.dist-info"):
            for dist_dir in sp.glob(pattern):
                top_level = dist_dir / "top_level.txt"
                if top_level.is_file():
                    with contextlib.suppress(Exception):
                        for line in top_level.read_text().splitlines():
                            top_name = line.strip()
                            if not top_name:
                                continue
                            top_dir = sp / top_name
                            if top_dir.is_dir():
                                return top_dir
                            top_file = sp / f"{top_name}.py"
                            if top_file.is_file():
                                return top_file
    return None


def _extract_all_exports(pkg_path: Path) -> list[str]:
    """Extrae los nombres exportados en __all__ desde __init__.py(i) o paquete."""
    candidate_files: list[Path] = []
    if pkg_path.is_file():
        candidate_files.append(pkg_path)
    else:
        for name in ("__init__.pyi", "__init__.py"):
            init_f = pkg_path / name
            if init_f.is_file():
                candidate_files.append(init_f)

    all_exports: list[str] = []
    for f in candidate_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            import ast

            parsed = ast.parse(content)
            for node in ast.walk(parsed):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "__all__"
                            and isinstance(node.value, (ast.List, ast.Tuple, ast.Set))
                        ):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(
                                    elt.value, str
                                ):
                                    all_exports.append(elt.value)
        except Exception as ex:
            logger.debug(f"Error al extraer __all__ de {f}: {ex}")
            continue
        if all_exports:
            break
    return all_exports


def extract_python_package_symbols(
    project_path: Path,
    package_name: str,
    package_version: str,
    max_symbols: int = 500,
) -> list[DependencySymbol]:
    """Extrae símbolos, firmas y docstrings de un paquete Python instalado."""
    site_packages_dirs = _find_site_packages(project_path)
    if not site_packages_dirs:
        logger.warning(f"No se encontró .venv en {project_path} para {package_name}.")
        return []

    pkg_path = _find_package_dir(site_packages_dirs, package_name)
    if not pkg_path:
        logger.warning(f"No se encontró {package_name} en site-packages.")
        return []

    all_exports = _extract_all_exports(pkg_path)
    all_exports_order = {name: idx for idx, name in enumerate(all_exports)}

    # Recolectar archivos relevantes (.pyi stubs y .py públicos)
    files_to_parse: list[Path] = []
    if pkg_path.is_file():
        files_to_parse.append(pkg_path)
    else:
        # Priorizar __init__.pyi / __init__.py, luego stubs .pyi, luego .py
        init_files = [
            f
            for f in (pkg_path / "__init__.pyi", pkg_path / "__init__.py")
            if f.is_file()
        ]
        pyi_files = [f for f in pkg_path.rglob("*.pyi") if f not in init_files]
        py_files = [f for f in pkg_path.rglob("*.py") if f not in init_files]

        for f in init_files + pyi_files + py_files:
            rel = str(f.relative_to(pkg_path)).lower()
            if any(
                p in rel
                for p in (
                    "test",
                    "tests",
                    "testing",
                    "_vendor",
                    "benchmarks",
                    "examples",
                )
            ):
                continue
            files_to_parse.append(f)
            if len(files_to_parse) >= 120:
                break

    parser = get_python_parser()
    symbols_data: list[dict[str, Any]] = []
    seen_symbol_names: set[str] = set()

    for file_path in files_to_parse:
        content = ""
        with contextlib.suppress(Exception):
            content = file_path.read_text(encoding="utf-8", errors="replace")
        if not content:
            continue

        tree = parser.parse(content.encode("utf-8"))
        root = tree.root_node
        lines = content.splitlines(keepends=True)
        mod_name = (
            f"{package_name}.{file_path.stem}"
            if file_path.stem != "__init__"
            else package_name
        )

        for child in root.children:
            actual_node = child
            if child.type == "decorated_definition":
                for sub in child.children:
                    if sub.type in ("class_definition", "function_definition"):
                        actual_node = sub
                        break

            if actual_node.type == "class_definition":
                name_node = actual_node.child_by_field_name("name")
                if not name_node or not name_node.text:
                    continue
                name_str = name_node.text.decode("utf-8", errors="ignore")
                if name_str.startswith("_") and not name_str.startswith("__"):
                    continue
                if name_str in seen_symbol_names:
                    continue
                seen_symbol_names.add(name_str)

                doc = extract_python_docstring(actual_node)
                schema = extract_python_class_schema(actual_node)
                methods = get_class_methods_py(actual_node)
                start_l = actual_node.start_point[0]
                end_l = actual_node.end_point[0]
                decl_snippet = "".join(lines[start_l : min(end_l + 1, start_l + 40)])

                sig = f"class {name_str}"
                if schema:
                    sig += f"({schema})"

                symbols_data.append(
                    {
                        "symbol_name": name_str,
                        "symbol_type": "class",
                        "signature": sig,
                        "docstring": doc,
                        "declaration_text": decl_snippet.strip(),
                        "source_module": mod_name,
                        "methods": methods,
                    }
                )

            elif actual_node.type == "function_definition":
                name_node = actual_node.child_by_field_name("name")
                if not name_node or not name_node.text:
                    continue
                name_str = name_node.text.decode("utf-8", errors="ignore")
                if name_str.startswith("_"):
                    continue
                if name_str in seen_symbol_names:
                    continue
                seen_symbol_names.add(name_str)

                doc = extract_python_docstring(actual_node)
                sig = extract_python_signature(actual_node)
                start_l = actual_node.start_point[0]
                end_l = actual_node.end_point[0]
                decl_snippet = "".join(lines[start_l : min(end_l + 1, start_l + 25)])

                symbols_data.append(
                    {
                        "symbol_name": name_str,
                        "symbol_type": "function",
                        "signature": sig or f"def {name_str}(...)",
                        "docstring": doc,
                        "declaration_text": decl_snippet.strip(),
                        "source_module": mod_name,
                        "methods": "",
                    }
                )

            if len(symbols_data) >= max_symbols * 2:
                break
        if len(symbols_data) >= max_symbols * 2:
            break

    if all_exports_order:
        symbols_data.sort(
            key=lambda s: (
                0 if s["symbol_name"] in all_exports_order else 1,
                all_exports_order.get(s["symbol_name"], 999999),
            )
        )
    symbols_data = symbols_data[:max_symbols]

    if not symbols_data:
        return []

    # Generar embeddings vectoriales en lotes
    text_prompts = [
        (
            f"{s['symbol_name']} ({package_name} {s['symbol_type']}): "
            f"{s['signature']}\n{s['docstring']}"
        )
        for s in symbols_data
    ]
    try:
        embeddings_res = get_embeddings(text_prompts, task="nl2code_document")
        vectors: list[list[float]] = embeddings_res or [
            [0.0] * config.EMBEDDING_VECTOR_DIM for _ in symbols_data
        ]
    except Exception as e:
        logger.warning(
            f"Error al generar embeddings para {package_name}, usando ceros: {e}"
        )
        vectors = [[0.0] * config.EMBEDDING_VECTOR_DIM for _ in symbols_data]

    results: list[DependencySymbol] = []
    for i, s in enumerate(symbols_data):
        vec = vectors[i] if i < len(vectors) else [0.0] * config.EMBEDDING_VECTOR_DIM
        symbol_id = f"python:{package_name}@{package_version}:{s['symbol_name']}"
        results.append(
            DependencySymbol(
                id=symbol_id,
                vector=vec,
                language="python",
                package_manager="uv",
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
        f"Extraídos {len(results)} símbolos para Python: "
        f"{package_name}@{package_version}"
    )
    return results
