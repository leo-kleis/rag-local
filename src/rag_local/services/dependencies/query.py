import contextlib
from typing import Any

from rag_local.core.logging import logger
from rag_local.services.db_wrapper import sanitize_sql_value
from rag_local.services.dependencies.db import get_deps_table
from rag_local.services.embeddings import get_embeddings


def query_dependency_symbols(
    package_name: str,
    symbol_name: str | None = None,
    query_text: str | None = None,
    language: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Consulta contratos, firmas y tipos de dependencias en la base de datos global.

    Soporta búsqueda exacta por símbolo, listado de paquetes y búsqueda híbrida
    vectorial + FTS para consultas conceptuales.
    """
    table = get_deps_table()
    sanitized_pkg = sanitize_sql_value(package_name.lower().replace("_", "-"))

    where_clauses = [f"package_name = '{sanitized_pkg}'"]
    from rag_local.services.dependencies.sync import (
        normalize_dependency_language,
    )

    norm_lang = normalize_dependency_language(language)
    if norm_lang:
        where_clauses.append(f"language = '{sanitize_sql_value(norm_lang)}'")

    # 1. Búsqueda exacta por símbolo
    if symbol_name and symbol_name.strip():
        sanitized_sym = sanitize_sql_value(symbol_name.strip())
        where_clauses.append(f"symbol_name = '{sanitized_sym}'")
        where_str = " AND ".join(where_clauses)
        try:
            results = table.search().where(where_str).limit(limit).to_list()
        except Exception as e:
            logger.warning(f"Error en búsqueda exacta de dependencia: {e}")
            results = []

        if not results:
            # Reintentar búsqueda case-insensitive o por LIKE si no hubo match exacto
            fallback_where = (
                f"package_name = '{sanitized_pkg}' AND id LIKE '%:{sanitized_sym}%'"
            )
            with contextlib.suppress(Exception):
                results = table.search().where(fallback_where).limit(limit).to_list()

    # 2. Búsqueda híbrida / semántica si se provee query_text
    elif query_text and query_text.strip():
        where_str = " AND ".join(where_clauses)
        try:
            embeddings_res = get_embeddings([query_text])
            query_vector = embeddings_res[0] if embeddings_res else [0.0] * 768
            try:
                # Intentar búsqueda híbrida (Vector + FTS)
                query_builder = (
                    table.search(query_type="hybrid")
                    .vector(query_vector)
                    .text(query_text)
                    .where(where_str)
                    .limit(limit)
                )
                results = query_builder.to_list()
            except Exception:
                # Fallback a búsqueda puramente vectorial
                query_builder = table.search(query_vector).where(where_str).limit(limit)
                results = query_builder.to_list()
        except Exception as e:
            logger.error(f"Error en búsqueda semántica de dependencia: {e}")
            results = []

    # 3. Listado general de símbolos del paquete si no se especificó símbolo ni query
    else:
        where_str = " AND ".join(where_clauses)
        try:
            results = table.search().where(where_str).limit(limit * 4).to_list()
        except Exception as e:
            logger.warning(f"Error al listar símbolos de dependencia: {e}")
            results = []

    return {
        "package_name": package_name,
        "language": language or (results[0].get("language") if results else "unknown"),
        "total_results": len(results),
        "symbols": results,
    }


def format_dependency_result(data: dict[str, Any]) -> str:
    """Formatea el resultado de la consulta de dependencias para el agente."""
    symbols = data.get("symbols", [])
    pkg_name = data.get("package_name", "")

    if not symbols:
        return (
            f"NO_DEPENDENCY_FOUND: No contracts or types found for "
            f"package '{pkg_name}' in LanceDB global cache. "
            f"Run 'ingest_dependencies' to index it."
        )

    lines = [f"[Dependency Contracts: {pkg_name} — {len(symbols)} symbol(s) found]\n"]

    for sym in symbols:
        sym_id = sym.get("id", "")
        sym_name = sym.get("symbol_name", "")
        sym_type = sym.get("symbol_type", "type")
        module = sym.get("source_module", "")
        signature = sym.get("signature", "")
        docstring = sym.get("docstring", "")
        decl_text = sym.get("declaration_text", "")

        lines.append(f"Symbol: {sym_name} ({sym_type}) | ID: {sym_id}")
        if module:
            lines.append(f"  ├── Module:    {module}")
        if signature:
            lines.append(f"  ├── Signature: {signature}")
        if docstring:
            clean_doc = " ".join(docstring.split()[:40])
            lines.append(f"  ├── Docstring: {clean_doc}")
        if decl_text:
            lines.append("  └── Declaration:")
            for line in decl_text.splitlines()[:15]:
                lines.append(f"        {line}")
        lines.append("")

    return "\n".join(lines).rstrip()
