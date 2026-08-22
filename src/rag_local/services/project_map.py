import fnmatch
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from rag_local.services.db import get_indexed_metadata


def _short_path(source: str) -> str:
    """Retorna la última parte relevante de la ruta para legibilidad."""
    parts = source.replace("\\", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 1 else source


def _build_tree_dict(paths: list[str]) -> dict[str, Any]:
    """Construye un diccionario anidado (trie) a partir de una lista de rutas."""
    tree: dict[str, Any] = {}
    for path in paths:
        parts = path.replace("\\", "/").split("/")
        current = tree
        for part in parts:
            if not part:
                continue
            if part not in current:
                current[part] = {}
            current = current[part]
    return tree


def _format_tree(tree: dict[str, Any], prefix: str = "") -> list[str]:
    """Genera recursivamente las líneas visuales del árbol de directorios."""
    lines = []
    items = sorted(tree.items())
    for i, (name, children) in enumerate(items):
        is_last = i == len(items) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{name}")
        if children:
            new_prefix = prefix + ("    " if is_last else "│   ")
            lines.extend(_format_tree(children, new_prefix))
    return lines


def generate_project_map(
    lancedb_path: Path,
    compact: bool = True,
    scope_filter: str | None = None,
    path_filter: str | None = None,
) -> str:
    """Lee los metadatos indexados en LanceDB y genera un mapa estructural universal.

    Extrae clases, funciones, modelos de datos, interfaces y eventos para
    Python, TypeScript, JavaScript y Prisma sin asumir convenciones rígidas.

    Args:
        lancedb_path: Ruta a la base de datos LanceDB.
        compact: Si es True, omite el volcado masivo del árbol de archivos.
        scope_filter: Filtro opcional por scope específico.
        path_filter: Filtro opcional por ruta o directorio (ej. 'src/bot_tv/web').

    Returns:
        String formateado con el mapa del proyecto condensado y de fácil lectura.
    """
    rows = get_indexed_metadata(
        [
            "source",
            "scope",
            "class_name",
            "method_name",
            "type",
            "models",
            "tags",
            "title",
        ]
    )
    if not rows:
        return (
            "NO_INDEX: The codebase has not been indexed yet. "
            "Run ingest_codebase first."
        )

    # Estructura por archivo: source -> símbolos y scope
    file_map: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "classes": set(),
            "functions": set(),
            "models": set(),
            "interfaces": set(),
            "events": set(),
            "scope": "unknown",
        }
    )
    scope_files: dict[str, set[str]] = defaultdict(set)
    prisma_models: set[str] = set()

    for row in rows:
        source: str = row.get("source", "")
        scope: str = row.get("scope", "unknown")
        if not source:
            continue

        if scope_filter and scope.lower() != scope_filter.lower():
            continue

        norm_source = source.replace("\\", "/")
        if path_filter:
            norm_pf = path_filter.replace("\\", "/").strip().rstrip("/")
            if "*" in norm_pf or "?" in norm_pf:
                if not fnmatch.fnmatch(norm_source, norm_pf) and not fnmatch.fnmatch(
                    norm_source, f"*{norm_pf}*"
                ):
                    continue
            elif norm_pf not in norm_source:
                continue

        scope_files[scope].add(source)
        data = file_map[source]
        data["scope"] = scope

        class_name: str = (row.get("class_name") or "").strip()
        method_name: str = (row.get("method_name") or "").strip()
        chunk_type: str = (row.get("type") or "").strip().lower()
        models_raw: str = (row.get("models") or "").strip()
        tags_raw: str = (row.get("tags") or "").strip()

        # Modelos desde metadatos
        if models_raw:
            try:
                parsed = json.loads(models_raw)
                if isinstance(parsed, list):
                    for m in parsed:
                        if m:
                            data["models"].add(str(m))
                            if "prisma" in source.lower():
                                prisma_models.add(str(m))
            except (json.JSONDecodeError, ValueError):
                for m in models_raw.split(","):
                    m = m.strip()
                    if m:
                        data["models"].add(m)
                        if "prisma" in source.lower():
                            prisma_models.add(m)

        # Tags de eventos y acciones
        if tags_raw:
            for tag in tags_raw.split(","):
                tag = tag.strip()
                if tag.startswith("event:"):
                    data["events"].add(tag.removeprefix("event:"))
                elif tag.startswith("action:"):
                    data["events"].add(tag.removeprefix("action:"))

        # Clasificación de símbolos por tipo AST
        if chunk_type in ("function", "func"):
            fn_name = method_name or class_name
            if fn_name:
                data["functions"].add(fn_name)
        elif chunk_type in ("interface", "type_alias"):
            if class_name:
                data["interfaces"].add(class_name)
        elif chunk_type == "model":
            if class_name:
                data["models"].add(class_name)
        elif class_name:
            if any(
                class_name.endswith(suffix)
                for suffix in ("Model", "Schema", "Payload", "DTO", "Dto", "Entity")
            ):
                data["models"].add(class_name)
            else:
                data["classes"].add(class_name)

        if (
            method_name
            and chunk_type not in ("function", "func")
            and method_name not in ("__init__", "constructor")
        ):
            for m in method_name.split(","):
                m = m.strip()
                if m and len(m) > 1:
                    data["functions"].add(m)

    total_files = sum(len(files) for files in scope_files.values())
    if total_files == 0:
        filter_parts = []
        if scope_filter:
            filter_parts.append(f"scope='{scope_filter}'")
        if path_filter:
            filter_parts.append(f"path='{path_filter}'")
        filter_desc = f" matching {', '.join(filter_parts)}" if filter_parts else ""
        return f"No indexed files found{filter_desc} in LanceDB."

    total_symbols = sum(
        len(d["classes"])
        + len(d["functions"])
        + len(d["models"])
        + len(d["interfaces"])
        + len(d["events"])
        for d in file_map.values()
    )

    filter_info = []
    if scope_filter:
        filter_info.append(f"scope={scope_filter}")
    if path_filter:
        filter_info.append(f"path={path_filter}")
    filter_header = f" (filtered by {', '.join(filter_info)})" if filter_info else ""

    lines: list[str] = [
        f"[Project Map — {total_files} files, "
        f"{total_symbols} symbols indexed{filter_header}]"
    ]

    # Mostrar árbol completo solo si compact es False
    if not compact:
        all_indexed_paths = sorted(
            {path for files in scope_files.values() for path in files}
        )
        if all_indexed_paths:
            lines.append("\n[Indexed File Tree]")
            tree_dict = _build_tree_dict(all_indexed_paths)
            lines.extend(_format_tree(tree_dict))

    # Orden canónico de scopes
    scope_order = ["angular", "nestjs", "nextjs-app", "python"]
    all_scopes = [s for s in scope_order if s in scope_files] + [
        s for s in sorted(scope_files) if s not in scope_order
    ]

    for scope in all_scopes:
        files = sorted(scope_files[scope])
        lines.append(f"\n[{scope}] {len(files)} file{'s' if len(files) != 1 else ''}")

        # Agrupar archivos por directorio de primer nivel dentro del scope
        dir_groups: dict[str, list[str]] = defaultdict(list)
        for f in files:
            norm_f = f.replace("\\", "/")
            parts = norm_f.split("/")
            group_key = parts[0] if len(parts) > 1 else "."
            dir_groups[group_key].append(norm_f)

        for group_key in sorted(dir_groups):
            group_files = dir_groups[group_key]
            for f in group_files:
                d = file_map.get(f)
                if not d:
                    continue

                symbols_parts: list[str] = []
                if d["classes"]:
                    sorted_classes = sorted(d["classes"])
                    symbols_parts.append(f"Classes: {', '.join(sorted_classes)}")
                if d["models"]:
                    sorted_models = sorted(d["models"])
                    symbols_parts.append(f"Models: {', '.join(sorted_models)}")
                if d["interfaces"]:
                    sorted_interfaces = sorted(d["interfaces"])
                    symbols_parts.append(f"Interfaces: {', '.join(sorted_interfaces)}")
                if d["functions"]:
                    # Limitar funciones por archivo para mantener concisión
                    sorted_fns = sorted(d["functions"])
                    if len(sorted_fns) > 5:
                        extra = len(sorted_fns) - 5
                        fn_str = f"{', '.join(sorted_fns[:5])} (+{extra} more)"
                    else:
                        fn_str = ", ".join(sorted_fns)
                    symbols_parts.append(f"Functions: {fn_str}")
                if d["events"]:
                    sorted_evts = sorted(d["events"])
                    symbols_parts.append(f"Events: {', '.join(sorted_evts)}")

                if symbols_parts:
                    summary = " | ".join(symbols_parts)
                    lines.append(f"  {_short_path(f)}: {summary}")
                else:
                    lines.append(f"  {_short_path(f)}")

    # Sección de modelos Prisma consolidada si existe
    if prisma_models:
        sorted_models = ", ".join(sorted(prisma_models))
        lines.append("\n[database/prisma]")
        lines.append(f"  Models: {sorted_models}")

    return "\n".join(lines)
