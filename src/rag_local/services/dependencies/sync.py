import contextlib
from pathlib import Path
from typing import Any

from rag_local.core.events import SyncPhase, emit_sync_event
from rag_local.core.logging import logger
from rag_local.services.dependencies.db import compact_deps_db, get_deps_table
from rag_local.services.dependencies.detector import detect_project_dependencies
from rag_local.services.dependencies.extractor_py import extract_python_package_symbols
from rag_local.services.dependencies.extractor_ts import extract_ts_package_symbols


def normalize_dependency_language(lang: str | None) -> str | None:
    """Normaliza alias de lenguaje ('node', 'js', 'ts', 'npm' -> 'typescript')."""
    if not lang:
        return None
    cleaned = lang.strip().lower()
    if cleaned in ("node", "nodejs", "javascript", "js", "typescript", "ts", "npm"):
        return "typescript"
    if cleaned in ("python", "py"):
        return "python"
    return cleaned


def sync_project_dependencies(
    project_path: Path,
    language: str | None = None,
    package_filter: str | None = None,
    force: bool = False,
    console: Any = None,
) -> dict[str, Any]:
    """Sincroniza las dependencias directas del proyecto con la base de datos global.

    Detecta qué dependencias ya están indexadas en la caché global y solo
    extrae e inserta aquellas que no existan (o todas si force=True).
    """
    table = get_deps_table()
    detected = detect_project_dependencies(project_path)
    norm_lang = normalize_dependency_language(language)

    # Filtrar por lenguaje si se solicitó
    if norm_lang:
        detected = {k: v for k, v in detected.items() if k == norm_lang}

    # Filtrar por paquete si se solicitó
    if package_filter:
        norm_pfilter = package_filter.lower().replace("_", "-")
        for lang_key in list(detected.keys()):
            detected[lang_key] = {
                k: v
                for k, v in detected[lang_key].items()
                if k.lower().replace("_", "-") == norm_pfilter
            }
        if not any(detected.values()):
            return {
                "indexed_packages": [],
                "already_cached": [],
                "failed_packages": [
                    f"{language or 'any'}:{package_filter} "
                    "(not declared in pyproject.toml or package.json)"
                ],
                "total_new_symbols": 0,
            }

    # Leer qué paquetes y versiones ya existen en la caché global
    try:
        existing_rows = (
            table.search()
            .select(["language", "package_name", "package_version"])
            .limit(20000)
            .to_list()
        )
        existing_set = {
            (
                r["language"],
                r["package_name"].lower().replace("_", "-"),
                r["package_version"],
            )
            for r in existing_rows
        }
    except Exception as e:
        logger.warning(f"No se pudieron leer registros previos de dependencias: {e}")
        existing_set = set()

    total_items = [
        (lang_key, pkg_name, version)
        for lang_key, packages in detected.items()
        for pkg_name, version in packages.items()
    ]
    total_count = max(len(total_items), 1)

    emit_sync_event(
        phase=SyncPhase.START,
        progress=5,
        message=f"Scanning dependencies ({len(total_items)} detected)...",
    )

    indexed_packages: list[str] = []
    already_cached: list[str] = []
    failed_packages: list[str] = []
    total_new_symbols = 0

    for idx, (lang_key, pkg_name, version) in enumerate(total_items):
        curr_progress = int((idx / total_count) * 90) + 5
        norm_pkg = pkg_name.lower().replace("_", "-")
        cache_key = (lang_key, norm_pkg, version)

        if not force and cache_key in existing_set:
            already_cached.append(f"{lang_key}:{pkg_name}@{version}")
            emit_sync_event(
                phase=SyncPhase.PROGRESS,
                progress=curr_progress,
                message=f"Cached {lang_key}:{pkg_name}@{version}",
            )
            continue

        if console:
            console.print(f"[dim]Indexando {lang_key}: {pkg_name}@{version}...[/dim]")

        emit_sync_event(
            phase=SyncPhase.PROGRESS,
            progress=curr_progress,
            message=f"Extracting {lang_key}:{pkg_name}@{version}...",
        )

        # Si es force, eliminar registros previos de este paquete
        if force:
            with contextlib.suppress(Exception):
                table.delete(
                    f"language = '{lang_key}' AND package_name = '{pkg_name}' "
                    f"AND package_version = '{version}'"
                )

        symbols = []
        if lang_key == "python":
            symbols = extract_python_package_symbols(project_path, pkg_name, version)
        elif lang_key == "typescript":
            symbols = extract_ts_package_symbols(project_path, pkg_name, version)

        if symbols:
            try:
                records = [s.model_dump() for s in symbols]
                table.add(records)
                total_new_symbols += len(symbols)
                indexed_packages.append(f"{lang_key}:{pkg_name}@{version}")
                existing_set.add(cache_key)
            except Exception as e:
                logger.error(
                    f"Error al insertar símbolos en LanceDB para {pkg_name}: {e}"
                )
                failed_packages.append(f"{lang_key}:{pkg_name}@{version}")
        else:
            failed_packages.append(f"{lang_key}:{pkg_name}@{version}")

    if total_new_symbols > 0:
        compact_deps_db()

    emit_sync_event(
        phase=SyncPhase.COMPLETED,
        progress=100,
        message=f"Finished: {len(indexed_packages)} indexed, "
        f"{len(already_cached)} cached.",
    )

    return {
        "indexed_packages": indexed_packages,
        "already_cached": already_cached,
        "failed_packages": failed_packages,
        "total_new_symbols": total_new_symbols,
    }
