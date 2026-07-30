from typing import Any

import lancedb

from rag_local.core.logging import logger
from rag_local.services.db import get_chroma_collection

_CSS_EXTENSIONS = (".css", ".scss", ".less", ".sass")
_CONSUME_EXTENSIONS = (".tsx", ".jsx", ".js", ".html", ".vue", ".svelte", ".astro")
# Marcador que el parser usa para prefijos BEM dinámicos
_BEM_MARKER = "[BEM]"


def get_styles_summary(repo_path: str | None = None) -> dict[str, Any]:
    """Genera un resumen estructurado del sistema de estilos del proyecto

    incluyendo archivos CSS, variables de diseño y clases obsoletas (Dead CSS).
    """
    try:
        wrapper = get_chroma_collection()
        table: lancedb.table.Table = wrapper.table
        rows: list[dict[str, Any]] = (
            table.search()
            .select(["source", "tags", "dependencies"])
            .limit(10000)
            .to_list()
        )
    except Exception as e:
        logger.error(f"Error al leer LanceDB en get_styles_summary: {e}")
        return {
            "status": "error",
            "message": f"No se pudo consultar la base de datos de estilos: {e}",
            "css_files": [],
            "variables": [],
            "declared_classes_count": 0,
            "consumed_classes_count": 0,
            "unused_classes": [],
        }

    if not rows:
        return {
            "status": "empty",
            "message": "La base de datos está vacía.",
            "css_files": [],
            "variables": [],
            "declared_classes_count": 0,
            "consumed_classes_count": 0,
            "unused_classes": [],
        }

    files_map: dict[str, dict[str, list[str]]] = {}
    declared_classes_file_map: dict[str, str] = {}
    consumed_classes: set[str] = set()
    bem_prefixes: set[str] = set()  # Prefijos BEM consumidos (sin marcador)

    for row in rows:
        source: str = str(row.get("source", ""))

        # Manejar tags/deps como lista o string CSV
        tags_raw = row.get("tags", "")
        deps_raw = row.get("dependencies", "")
        if isinstance(tags_raw, list):
            tags_list = [t.strip() for t in tags_raw if t and t.strip()]
        else:
            tags_list = [t.strip() for t in str(tags_raw).split(",") if t.strip()]
        if isinstance(deps_raw, list):
            deps_list = [d.strip() for d in deps_raw if d and d.strip()]
        else:
            deps_list = [d.strip() for d in str(deps_raw).split(",") if d.strip()]

        if source.endswith(_CSS_EXTENSIONS):
            if source not in files_map:
                files_map[source] = {"variables": [], "classes": []}

            for t in tags_list:
                if t not in files_map[source]["classes"]:
                    files_map[source]["classes"].append(t)
                if t not in declared_classes_file_map:
                    declared_classes_file_map[t] = source

            for d in deps_list:
                if d.startswith("--") and d not in files_map[source]["variables"]:
                    files_map[source]["variables"].append(d)

        elif source.endswith(_CONSUME_EXTENSIONS):
            for t in tags_list:
                if t.startswith(_BEM_MARKER):
                    # Extraer el prefijo BEM real (ej: "[BEM]user-avatar--")
                    bem_prefixes.add(t[len(_BEM_MARKER) :])
                else:
                    consumed_classes.add(t)

    obsoletos: dict[str, list[str]] = {}
    for c, f in declared_classes_file_map.items():
        # Clase declarada usada si:
        # 1. Está en clases consumidas, O
        # 2. Match con prefijo BEM
        is_used = c in consumed_classes or any(
            c.startswith(prefix) for prefix in bem_prefixes if prefix
        )
        if not is_used:
            if f not in obsoletos:
                obsoletos[f] = []
            obsoletos[f].append(c)

    # Ordenar listas dentro de cada archivo
    for f_info in files_map.values():
        f_info["variables"].sort()
        f_info["classes"].sort()
    for f in obsoletos:
        obsoletos[f].sort()

    return {
        "status": "success",
        "files": files_map,
        "obsoletos": obsoletos,
    }


def format_styles_summary(data: dict[str, Any]) -> str:
    """Formatea la información de estilos en texto plano optimizado para el agente."""
    if data.get("status") != "success":
        return f"NO_DATA: {data.get('message', 'No styles metadata available.')}"

    files = data.get("files", {})
    obsoletos = data.get("obsoletos", {})

    total_vars = sum(len(f["variables"]) for f in files.values())
    total_classes = sum(len(f["classes"]) for f in files.values())
    total_obsoletos = sum(len(clist) for clist in obsoletos.values())

    header = (
        f"[Styles System Map — {len(files)} CSS files, "
        f"{total_vars} variables, {total_classes} classes]"
    )
    lines = [header]

    lines.append("\n[CSS Files & Design Variables]")
    for f_path in sorted(files):
        vars_list = files[f_path]["variables"]
        classes_list = files[f_path]["classes"]
        info_parts = []
        if vars_list:
            info_parts.append(f"vars({', '.join(vars_list)})")
        if classes_list:
            info_parts.append(f"classes({', '.join(classes_list)})")
        info_str = " ".join(info_parts) if info_parts else "(empty)"
        lines.append(f"  {f_path}: {info_str}")

    lines.append(f"\n[Obsolete CSS Classes — {total_obsoletos} unused classes]")
    if not obsoletos:
        lines.append("  (no unused CSS classes found)")
    else:
        for f_path in sorted(obsoletos):
            lines.append(f"  {f_path}: {', '.join(obsoletos[f_path])}")

    return "\n".join(lines)
