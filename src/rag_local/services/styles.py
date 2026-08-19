import json
from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.parsers.css import parse_css_rules
from rag_local.services.db import get_indexed_metadata

_CSS_EXTENSIONS = (".css", ".scss", ".less", ".sass")
_CONSUME_EXTENSIONS = (".tsx", ".jsx", ".js", ".html", ".vue", ".svelte", ".astro")
_BEM_MARKER = "[BEM]"
_VENDOR_ICON_PREFIXES = ("fa-", "fas-", "far-", "fab-", "bi-", "icon-", "material-")
_GENERIC_STATE_CLASSES = {
    "active",
    "open",
    "disabled",
    "selected",
    "loading",
    "ok",
    "err",
    "block",
    "warn",
    "open-up",
    "align-right",
}


def _is_css_class_tag(tag: str) -> bool:
    """Valida que un tag sea una clase CSS y no una acción o evento de arquitectura."""
    if not tag or not isinstance(tag, str):
        return False
    t = tag.strip().lower()
    if t.startswith(
        (
            "action:",
            ".action:",
            "event:",
            ".event:",
            "route:",
            ".route:",
            "model:",
            "service:",
            "controller:",
            "api:",
        )
    ):
        return False
    return not (":" in t and not t.startswith(_BEM_MARKER.lower()))


def _is_vendor_icon(class_name: str) -> bool:
    """Verifica si una clase pertenece a librerías de iconos externas."""
    c = class_name.lower()
    return any(c.startswith(p) for p in _VENDOR_ICON_PREFIXES) or c in (
        "fa",
        "fas",
        "far",
        "fab",
        "fa-solid",
        "fa-regular",
        "fa-brand",
    )


def get_styles_summary(
    repo_path: str | None = None,
    component_filter: str | None = None,
    class_filter: str | None = None,
    property_filter: str | None = None,
) -> dict[str, Any]:
    """Genera un resumen estructurado del sistema de estilos del proyecto

    incluyendo trazabilidad componente ↔ clases CSS, inspección de reglas por propiedad
    y detección de clases obsoletas.
    """
    rows = get_indexed_metadata(["source", "tags", "dependencies", "css_rules"])

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
    consumed_by_file: dict[str, set[str]] = {}
    consumed_classes: set[str] = set()
    bem_prefixes: set[str] = set()

    for row in rows:
        source: str = str(row.get("source", ""))
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
                if not _is_css_class_tag(t):
                    continue
                if t not in files_map[source]["classes"]:
                    files_map[source]["classes"].append(t)
                if t not in declared_classes_file_map:
                    declared_classes_file_map[t] = source

            for d in deps_list:
                if d.startswith("--") and d not in files_map[source]["variables"]:
                    files_map[source]["variables"].append(d)

        elif source.endswith(_CONSUME_EXTENSIONS):
            if source not in consumed_by_file:
                consumed_by_file[source] = set()

            for t in tags_list:
                if t.startswith(_BEM_MARKER):
                    bem_prefixes.add(t[len(_BEM_MARKER) :])
                elif _is_css_class_tag(t):
                    consumed_classes.add(t)
                    consumed_by_file[source].add(t)

    # Identificar clases obsoletas (ignorando prefijos de iconos externos)
    obsoletos: dict[str, list[str]] = {}
    for c, f in declared_classes_file_map.items():
        if _is_vendor_icon(c):
            continue
        is_used = c in consumed_classes or any(
            c.startswith(prefix) for prefix in bem_prefixes if prefix
        )
        if not is_used:
            if f not in obsoletos:
                obsoletos[f] = []
            obsoletos[f].append(c)

    for f_info in files_map.values():
        f_info["variables"].sort()
        f_info["classes"].sort()
    for f in obsoletos:
        obsoletos[f].sort()

    # Análisis detallado de reglas CSS usando css_rules indexados en LanceDB
    root = Path(repo_path) if repo_path else config.REPO_ROOT
    parsed_rules_by_file: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        source: str = str(row.get("source", ""))
        if source.endswith(_CSS_EXTENSIONS) and source not in parsed_rules_by_file:
            raw_rules = row.get("css_rules", "")
            if raw_rules:
                try:
                    parsed_rules_by_file[source] = json.loads(raw_rules)
                except Exception as ex:
                    logger.debug(f"Error al deserializar css_rules de {source}: {ex}")

            if source not in parsed_rules_by_file:
                abs_css_path = root / source
                if abs_css_path.exists() and abs_css_path.is_file():
                    try:
                        content = abs_css_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        parsed_rules_by_file[source] = parse_css_rules(content)
                    except Exception as ex:
                        logger.warning(f"No se pudo parsear {source}: {ex}")

    # Filtrar trazabilidad Componente ↔ CSS
    component_trace: dict[str, Any] = {}
    for comp_file, cset in consumed_by_file.items():
        if component_filter and component_filter.lower() not in comp_file.lower():
            continue

        # Clases base (no genéricas) del componente
        comp_non_generic_classes = {
            c
            for c in cset
            if c.lower() not in _GENERIC_STATE_CLASSES and not _is_vendor_icon(c)
        }

        matched_classes: dict[str, list[dict[str, Any]]] = {}
        for cname in sorted(cset):
            if class_filter and class_filter.lower() not in cname.lower():
                continue

            # Ignorar iconos de librerías de terceros
            if _is_vendor_icon(cname):
                continue

            defs: list[dict[str, Any]] = []
            is_generic = cname.lower() in _GENERIC_STATE_CLASSES

            for css_file, rules in parsed_rules_by_file.items():
                for r in rules:
                    rule_classes = r.get("classes", [])
                    if cname in rule_classes:
                        # Coincidencia contextual para clases genéricas (.active)
                        if (
                            is_generic
                            and comp_non_generic_classes
                            and not any(
                                base_c in rule_classes
                                for base_c in comp_non_generic_classes
                            )
                        ):
                            continue

                        if property_filter:
                            p_match = any(
                                property_filter.lower() in p_key.lower()
                                or property_filter.lower() in p_val.lower()
                                for p_key, p_val in r.get("properties", {}).items()
                            )
                            if not p_match:
                                continue

                        defs.append(
                            {
                                "css_file": css_file,
                                "selector": r.get("selector"),
                                "start_line": r.get("start_line"),
                                "end_line": r.get("end_line"),
                                "properties": r.get("properties", {}),
                                "media_query": r.get("media_query", ""),
                            }
                        )

            if defs or not property_filter:
                matched_classes[cname] = defs

        if matched_classes:
            component_trace[comp_file] = matched_classes

    # Filtrar consultas por propiedad directa
    property_matches: list[dict[str, Any]] = []
    if property_filter:
        p_query = property_filter.lower()
        for css_file, rules in parsed_rules_by_file.items():
            for r in rules:
                matching_props = {
                    k: v
                    for k, v in r.get("properties", {}).items()
                    if p_query in k.lower() or p_query in v.lower()
                }
                if matching_props:
                    property_matches.append(
                        {
                            "css_file": css_file,
                            "selector": r.get("selector"),
                            "start_line": r.get("start_line"),
                            "end_line": r.get("end_line"),
                            "matching_properties": matching_props,
                            "media_query": r.get("media_query", ""),
                        }
                    )

    return {
        "status": "success",
        "files": files_map,
        "obsoletos": obsoletos,
        "component_trace": component_trace,
        "property_matches": property_matches,
        "filters": {
            "component": component_filter,
            "class": class_filter,
            "property": property_filter,
        },
    }


def format_styles_summary(data: dict[str, Any]) -> str:
    """Formatea la información de estilos en texto plano optimizado para el agente."""
    if data.get("status") != "success":
        return f"NO_DATA: {data.get('message', 'No styles metadata available.')}"

    files = data.get("files", {})
    obsoletos = data.get("obsoletos", {})
    comp_trace = data.get("component_trace", {})
    prop_matches = data.get("property_matches", [])
    filters = data.get("filters", {})

    total_vars = sum(len(f["variables"]) for f in files.values())
    total_classes = sum(len(f["classes"]) for f in files.values())
    total_obsoletos = sum(len(clist) for clist in obsoletos.values())

    header = (
        f"[Styles System Map — {len(files)} CSS files, "
        f"{total_vars} variables, {total_classes} classes]"
    )
    lines = [header]

    active_filters = [f"{k}={v}" for k, v in filters.items() if v]
    if active_filters:
        lines.append(f"\n[Active Filters: {', '.join(active_filters)}]")

    if comp_trace:
        lines.append("\n[Component ↔ CSS Traceability]")
        for comp_file, classes_map in sorted(comp_trace.items()):
            lines.append(f"  Component: {comp_file}")
            for cname, defs in sorted(classes_map.items()):
                if not defs:
                    lines.append(f"    - .{cname} (no CSS rule definition found)")
                else:
                    for d in defs:
                        props_str = ", ".join(
                            f"{k}: {v}" for k, v in d.get("properties", {}).items()
                        )
                        media_str = (
                            f" ({d['media_query']})" if d.get("media_query") else ""
                        )
                        lines.append(
                            f"    - .{cname} -> {d['css_file']}:"
                            f"L{d['start_line']}-{d['end_line']}{media_str} "
                            f"| selector: '{d['selector']}' | props({props_str})"
                        )

    if prop_matches:
        lines.append(f"\n[Property Query Results ({len(prop_matches)} rules matched)]")
        for pm in prop_matches[:50]:
            props_str = ", ".join(
                f"{k}: {v}" for k, v in pm.get("matching_properties", {}).items()
            )
            lines.append(
                f"  {pm['css_file']}:L{pm['start_line']}-{pm['end_line']} "
                f"| selector: '{pm['selector']}' | {props_str}"
            )

    if not filters.get("component") and not filters.get("property"):
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
