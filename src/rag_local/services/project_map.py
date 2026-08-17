from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from rag_local.services.db import get_indexed_metadata

# Sufijos conocidos por tipo de símbolo (NestJS / Angular / Python)
_SUFFIX_MAP: dict[str, str] = {
    "Controller": "Controllers",
    "Service": "Services",
    "Guard": "Guards",
    "Module": "Modules",
    "Interceptor": "Interceptors",
    "Filter": "Filters",
    "Pipe": "Pipes",
    "Decorator": "Decorators",
    "Component": "Components",
    "Directive": "Directives",
    "Resolver": "Resolvers",
    "Repository": "Repositories",
    "Factory": "Factories",
    "Strategy": "Strategies",
    "Middleware": "Middlewares",
    "Provider": "Providers",
    "Context": "Contexts",
    "Event": "Events",
    "Action": "Actions",
    "Handler": "Handlers",
    "Emitter": "Emitters",
    "Consumer": "Consumers",
    "Listener": "Listeners",
}


def _classify_class(class_name: str, source: str = "") -> str:
    """Retorna la categoría de una clase según su sufijo o ruta de archivo."""
    for suffix, category in _SUFFIX_MAP.items():
        if class_name.endswith(suffix):
            return category
    norm_src = source.replace("\\", "/").lower()
    if norm_src.endswith("events.py") or "/events/" in norm_src:
        return "Events"
    if norm_src.endswith("actions.py") or "/actions/" in norm_src:
        return "Actions"
    return "Other"


def _short_path(source: str) -> str:
    """Retorna la última parte relevante de la ruta para legibilidad."""
    parts = source.replace("\\", "/").split("/")
    # Tomar las últimas 2 partes (carpeta/archivo.ext)
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


def generate_project_map(lancedb_path: Path) -> str:
    """Lee los metadatos indexados en LanceDB y genera un mapa estructural.

    No realiza búsqueda semántica ni llama a ningún LLM. Solo lee las
    columnas de metadatos de la tabla monorepo_code para minimizar memoria.

    Returns:
        String formateado con el mapa del proyecto agrupado por scope y tipo.
    """
    rows = get_indexed_metadata(["source", "scope", "class_name", "type", "models"])
    if not rows:
        return (
            "NO_INDEX: The codebase has not been indexed yet. "
            "Run ingest_codebase first."
        )

    # Estructuras de acumulación
    # scope → category → list[(class_name, source)]
    scope_classes: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    # scope → set de archivos únicos
    scope_files: dict[str, set[str]] = defaultdict(set)
    # Modelos Prisma (únicos)
    prisma_models: set[str] = set()
    seen_classes: set[tuple[str, str]] = set()  # (scope, class_name) ya registradas

    for row in rows:
        source: str = row.get("source", "")
        scope: str = row.get("scope", "unknown")
        class_name: str = (row.get("class_name") or "").strip()
        chunk_type: str = (row.get("type") or "").strip()
        models_raw: str = (row.get("models") or "").strip()

        if source:
            scope_files[scope].add(source)

        # Clases con nombre
        if class_name and (scope, class_name) not in seen_classes:
            seen_classes.add((scope, class_name))
            category = _classify_class(class_name, source)
            scope_classes[scope][category].append((class_name, source))

        # Modelos Prisma desde campo models (JSON array o string CSV)
        if models_raw:
            try:
                parsed = json.loads(models_raw)
                if isinstance(parsed, list):
                    prisma_models.update(str(m) for m in parsed if m)
            except (json.JSONDecodeError, ValueError):
                # Fallback: puede ser CSV simple
                for m in models_raw.split(","):
                    m = m.strip()
                    if m:
                        prisma_models.add(m)

        # Chunk de tipo model sin class_name (chunks Prisma sin clase explícita)
        if chunk_type == "model" and not class_name:
            pass  # ya cubierto por models_raw

    # Ordenar scopes para output consistente
    scope_order = ["angular", "nestjs", "nextjs-app", "python"]
    all_scopes = scope_order + [s for s in sorted(scope_files) if s not in scope_order]

    total_files = sum(len(files) for files in scope_files.values())
    lines: list[str] = [f"[Project Map — {total_files} files indexed]"]

    # Generar el árbol de directorios a partir de todos los archivos únicos indexados
    all_indexed_paths = sorted(
        {path for files in scope_files.values() for path in files}
    )
    if all_indexed_paths:
        lines.append("\n[Indexed File Tree]")
        tree_dict = _build_tree_dict(all_indexed_paths)
        lines.extend(_format_tree(tree_dict))

    for scope in all_scopes:
        if scope not in scope_files:
            continue

        file_count = len(scope_files[scope])
        lines.append(f"\n[{scope}] {file_count} file{'s' if file_count != 1 else ''}")

        categories = scope_classes.get(scope, {})
        if not categories:
            lines.append("  (no named classes found)")
            continue

        # Orden de categorías para consistencia visual
        category_order = [
            "Events",
            "Actions",
            "Handlers",
            "Emitters",
            "Consumers",
            "Listeners",
            "Components",
            "Directives",
            "Pipes",
            "Controllers",
            "Services",
            "Guards",
            "Resolvers",
            "Modules",
            "Interceptors",
            "Filters",
            "Middlewares",
            "Repositories",
            "Factories",
            "Strategies",
            "Decorators",
            "Other",
        ]
        ordered = category_order + [
            c for c in sorted(categories) if c not in category_order
        ]

        for category in ordered:
            if category not in categories:
                continue
            entries = categories[category]
            # Ordenar por nombre de clase
            entries.sort(key=lambda x: x[0])
            items = ", ".join(f"{cls} ({_short_path(src)})" for cls, src in entries)
            lines.append(f"  {category}: {items}")

    # Sección Prisma
    if prisma_models:
        sorted_models = ", ".join(sorted(prisma_models))
        lines.append("\n[nestjs/prisma]")
        lines.append(f"  Models: {sorted_models}")

    return "\n".join(lines)
