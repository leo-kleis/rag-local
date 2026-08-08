import json
import re
from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.parsers.css import parse_css_rules
from rag_local.services.db import get_indexed_metadata
from rag_local.services.scanner import scan_files

_CSS_EXTENSIONS = (".css", ".scss", ".less", ".sass")
_RESET_SELECTORS = {
    "*",
    "*::before",
    "*::after",
    "*:before",
    "*:after",
    "html",
    "body",
    ":root",
    ":before",
    ":after",
}


_RE_CLASS_ATTR = re.compile(r'(?:className|class)\s*=\s*["\'`]?([^"\'`>]+)["\'`]?')


def extract_component_parent_map(
    all_files: list[Path], root: Path
) -> dict[str, set[str]]:
    """Construye un mapa de jerarquía de clases CSS desde componentes UI:

    child_class -> set(parent_classes).
    """
    parent_map: dict[str, set[str]] = {}
    comp_exts = (".js", ".jsx", ".tsx", ".html", ".vue", ".svelte")
    comp_files = [f for f in all_files if f.suffix.lower() in comp_exts]

    for cf in comp_files:
        try:
            content = cf.read_text(encoding="utf-8", errors="replace")
            stack: list[set[str]] = []
            for match in _RE_CLASS_ATTR.finditer(content):
                val = match.group(1).strip()
                raw_classes = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_-]*\b", val))
                if not raw_classes:
                    continue
                for c in raw_classes:
                    if c not in parent_map:
                        parent_map[c] = set()
                    for parent_set in stack:
                        parent_map[c].update(parent_set)
                stack.append(raw_classes)
                if len(stack) > 6:
                    stack.pop(0)
        except Exception as ex:
            logger.debug(f"No se pudo extraer jerarquía en {cf}: {ex}")

    return parent_map


def audit_layout_risks(
    repo_path: str | None = None,
    severity_filter: str | None = None,
    file_filter: str | None = None,
) -> dict[str, Any]:
    """Realiza una auditoría de riesgos y antipatrones de layout CSS

    consultando los archivos indexados en LanceDB y clasificados por severidad.
    Sincroniza transparentemente deltas con fast_check_and_refresh() y analiza
    archivos CSS físicos en caliente.
    """
    root = Path(repo_path) if repo_path else config.REPO_ROOT
    if not root.exists():
        return {
            "status": "error",
            "message": f"La ruta especificada no existe: {root}",
            "issues": [],
        }

    # 1. Sincronización transparente de deltas en caliente
    try:
        from rag_local.services.fast_sync import fast_check_and_refresh

        fast_check_and_refresh(root)
    except Exception as ex:
        logger.debug(f"fast_check_and_refresh en audit_layout_risks: {ex}")

    issues: list[dict[str, Any]] = []

    # Procesar filtros múltiples por coma en file_filter
    file_filters: list[str] = []
    if file_filter:
        file_filters = [f.strip().lower() for f in file_filter.split(",") if f.strip()]

    # Consultar metadatos indexados en LanceDB
    rows = get_indexed_metadata(["source", "tags", "dependencies", "css_rules", "type"])
    parsed_css_by_file: dict[str, list[dict[str, Any]]] = {}

    if rows:
        indexed_sources = {str(r.get("source", "")) for r in rows if r.get("source")}
        all_files = [root / src for src in indexed_sources if (root / src).exists()]
        for r in rows:
            src = str(r.get("source", ""))
            if src.endswith(_CSS_EXTENSIONS) and src not in parsed_css_by_file:
                raw_rules = r.get("css_rules", "")
                if raw_rules:
                    try:
                        parsed_css_by_file[src] = json.loads(raw_rules)
                    except Exception as ex:
                        logger.debug(f"Error deserializando css_rules de {src}: {ex}")
    else:
        # Fallback a escaneo si no hay datos indexados
        try:
            all_files = scan_files()
        except Exception as e:
            logger.warning(f"Error al escanear archivos para auditoría estática: {e}")
            all_files = list(root.rglob("*"))

    css_files = [f for f in all_files if f.suffix.lower() in _CSS_EXTENSIONS]

    # Extraer mapa de ancestros DOM entre clases en componentes UI
    component_parent_map = extract_component_parent_map(all_files, root)

    # Identificar todas las clases CSS del proyecto que aplican mitigación de overflow
    project_mitigated_classes: set[str] = set()
    for css_path in css_files:
        try:
            rel_path = str(css_path.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel_path = str(css_path)

        # Re-parsear en vivo si el archivo físico en disco está disponible
        if css_path.exists():
            try:
                content = css_path.read_text(encoding="utf-8", errors="replace")
                parsed = parse_css_rules(content)
                parsed_css_by_file[rel_path] = parsed
            except Exception as ex:
                logger.debug(f"No se pudo parsear en caliente {css_path}: {ex}")
                parsed = parsed_css_by_file.get(rel_path, [])
        else:
            parsed = parsed_css_by_file.get(rel_path, [])

        for r in parsed:
            props = r.get("properties", {})
            overflow = props.get("overflow", "").lower()
            overflow_x = props.get("overflow-x", "").lower()
            overflow_y = props.get("overflow-y", "").lower()
            if any(
                kw in overflow or kw in overflow_x or kw in overflow_y
                for kw in ("hidden", "auto", "scroll")
            ):
                project_mitigated_classes.update(r.get("classes", []))

    for css_path in css_files:
        try:
            rel_path = str(css_path.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel_path = str(css_path)

        if file_filters and not any(flt in rel_path.lower() for flt in file_filters):
            continue

        rules = parsed_css_by_file.get(rel_path, [])
        if not rules:
            continue

        # Selectores con mitigación de overflow (hidden/auto) en este archivo
        mitigating_selectors = {
            r.get("selector", "").strip()
            for r in rules
            if any(
                kw in r.get("properties", {}).get("overflow", "").lower()
                or kw in r.get("properties", {}).get("overflow-x", "").lower()
                for kw in ("hidden", "auto", "scroll")
            )
        }

        for rule in rules:
            selector = rule.get("selector", "").strip()
            start_line = rule.get("start_line", 1)
            end_line = rule.get("end_line", 1)
            props = rule.get("properties", {})
            media_query = rule.get("media_query", "")

            clean_sel = selector.lower()

            # Excluir selectores universales y de reset CSS
            if clean_sel in _RESET_SELECTORS or clean_sel.startswith("*"):
                continue

            disp = props.get("display", "").lower()
            flex_prop = props.get("flex", "").lower()
            flex_grow = props.get("flex-grow", "").lower()
            flex_shrink = props.get("flex-shrink", "").lower()
            flex_direction = props.get("flex-direction", "").lower()
            flex_wrap = props.get("flex-wrap", "").lower()
            min_w = props.get("min-width", "").lower()
            min_h = props.get("min-height", "").lower()
            overflow = props.get("overflow", "").lower()
            overflow_x = props.get("overflow-x", "").lower()
            overflow_y = props.get("overflow-y", "").lower()
            word_break = props.get("word-break", "").lower()
            overflow_wrap = props.get("overflow-wrap", "").lower()
            z_index = props.get("z-index", "")
            isolation = props.get("isolation", "").lower()
            position = props.get("position", "").lower()
            width = props.get("width", "").lower()
            height = props.get("height", "").lower()
            max_w = props.get("max-width", "").lower()

            # Excluir de CRITICAL si flex-shrink: 0 o dimensiones fijas
            has_no_shrink = (
                flex_shrink == "0" or "0 0 " in flex_prop or flex_prop == "none"
            )
            has_fixed_size = width.endswith("px") and height.endswith("px")
            has_overflow_control = any(
                kw in overflow or kw in overflow_x or kw in overflow_y
                for kw in ("hidden", "auto", "scroll")
            )

            # 1. CRITICAL: Flexbox / Grid child overflow risk (sin min-width: 0)
            is_flex_container = "flex" in disp or "grid" in disp
            is_flex_child = (
                bool(flex_prop)
                or flex_grow in ("1", "2", "3")
                or "flex" in selector.lower()
            )

            if (
                (is_flex_container or is_flex_child)
                and not has_no_shrink
                and not has_fixed_size
                and not (
                    min_w in ("0", "0px", "0%", "none")
                    or min_h in ("0", "0px", "0%", "none")
                    or has_overflow_control
                )
            ):
                flex_keywords = (
                    "msg",
                    "chat",
                    "text",
                    "content",
                    "body",
                    "item",
                    "row",
                    "col",
                    "card",
                    "feed",
                    "wrapper",
                    "container",
                )
                if any(k in selector.lower() for k in flex_keywords):
                    rule_classes = rule.get("classes", [])
                    # Comprobar mitigación en CSS o jerarquía UI
                    file_mitigated = any(
                        m_sel != selector
                        and (
                            m_sel in selector
                            or any(part in selector for part in m_sel.split())
                        )
                        for m_sel in mitigating_selectors
                    )

                    parent_mitigation_class = ""
                    for cname in rule_classes:
                        parents = component_parent_map.get(cname, set())
                        match_parent = next(
                            (p for p in parents if p in project_mitigated_classes),
                            None,
                        )
                        if match_parent:
                            parent_mitigation_class = match_parent
                            break

                    is_mitigated = file_mitigated or bool(parent_mitigation_class)
                    sev = "INFO" if is_mitigated else "CRITICAL"

                    if parent_mitigation_class:
                        mit_suffix = (
                            f" [MITIGATED: Protegido por .{parent_mitigation_class}]"
                        )
                    elif file_mitigated:
                        mit_suffix = " (mitigado por contenedor con overflow)"
                    else:
                        mit_suffix = ""

                    msg_text = (
                        f"El contenedor/hijo flex '{selector}' "
                        f"(display: {disp or 'flex-item'}) "
                        "carece de 'min-width: 0' o 'overflow: hidden'"
                        f"{mit_suffix}."
                    )
                    issues.append(
                        {
                            "severity": sev,
                            "file": rel_path,
                            "start_line": start_line,
                            "end_line": end_line,
                            "selector": selector,
                            "category": "Flexbox/Grid Overflow Risk",
                            "message": msg_text,
                            "recommendation": (
                                "Agregar 'min-width: 0;' o 'overflow: hidden;'."
                            ),
                        }
                    )

            # Omitir pseudo-clases en auditoría de layout y texto
            if re.search(
                r":(hover|active|focus|disabled|visited|first-child|last-child|nth-child|placeholder)",
                clean_sel,
            ):
                continue

            # 2. WARNING / INFO: Flex Wrap Overflow Risk
            is_horizontal_flex = ("flex" in disp or "inline-flex" in disp) and (
                "column" not in flex_direction
            )
            collection_keywords = (
                "button",
                "btn",
                "tag",
                "badge",
                "chip",
                "tab",
                "page",
                "pagination",
                "action",
                "item",
                "list",
                "toolbar",
                "menu",
                "group",
                "pill",
                "link",
                "nav",
                "control",
                "controls",
                "card",
                "cards",
                "row",
                "options",
            )
            has_collection_hint = any(k in clean_sel for k in collection_keywords)
            has_wrap = flex_wrap in ("wrap", "wrap-reverse")

            if (
                is_horizontal_flex
                and has_collection_hint
                and not has_wrap
                and not has_overflow_control
                and not has_fixed_size
            ):
                rule_classes = rule.get("classes", [])
                file_mitigated = any(
                    m_sel != selector
                    and (
                        m_sel in selector
                        or any(part in selector for part in m_sel.split())
                    )
                    for m_sel in mitigating_selectors
                )
                parent_mitigation_class = ""
                for cname in rule_classes:
                    parents = component_parent_map.get(cname, set())
                    match_parent = next(
                        (p for p in parents if p in project_mitigated_classes),
                        None,
                    )
                    if match_parent:
                        parent_mitigation_class = match_parent
                        break

                is_mitigated = file_mitigated or bool(parent_mitigation_class)
                sev = "INFO" if is_mitigated else "WARNING"
                mit_suffix = (
                    f" [MITIGATED: Protegido por .{parent_mitigation_class}]"
                    if parent_mitigation_class
                    else (
                        " (mitigado por contenedor con overflow)"
                        if file_mitigated
                        else ""
                    )
                )

                msg_text = (
                    f"El contenedor flex horizontal '{selector}' (display: {disp}) "
                    "carece de 'flex-wrap: wrap' o 'overflow-x: auto', lo que "
                    f"puede provocar desbordamiento de hijos{mit_suffix}."
                )
                issues.append(
                    {
                        "severity": sev,
                        "file": rel_path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "selector": selector,
                        "category": "Flex Wrap Overflow Risk",
                        "message": msg_text,
                        "recommendation": (
                            "Agregar 'flex-wrap: wrap;' o 'overflow-x: auto;'."
                        ),
                    }
                )

            # 3. WARNING: Breakpoint Width Overflow
            if media_query and "max-width" in media_query.lower():
                mw_match = re.search(
                    r"max-width\s*:\s*([\d.]+)\s*(px|rem|em)",
                    media_query,
                    re.IGNORECASE,
                )
                if mw_match:
                    bp_val = float(mw_match.group(1))
                    bp_unit = mw_match.group(2).lower()
                    bp_px = bp_val * 16.0 if bp_unit in ("rem", "em") else bp_val

                    declared_width_px = 0.0
                    for w_prop in (min_w, width):
                        if w_prop:
                            w_match = re.search(
                                r"^([\d.]+)\s*(px|rem|em)$",
                                w_prop.strip(),
                                re.IGNORECASE,
                            )
                            if w_match:
                                w_val = float(w_match.group(1))
                                w_unit = w_match.group(2).lower()
                                px_equiv = (
                                    w_val * 16.0 if w_unit in ("rem", "em") else w_val
                                )
                                declared_width_px = max(declared_width_px, px_equiv)

                    grid_cols = props.get("grid-template-columns", "").lower()
                    if grid_cols:
                        px_cols = re.findall(r"([\d.]+)\s*px", grid_cols)
                        if px_cols:
                            grid_sum = sum(float(c) for c in px_cols)
                            declared_width_px = max(declared_width_px, grid_sum)

                    if declared_width_px >= bp_px and declared_width_px > 0:
                        msg_text = (
                            f"La regla '{selector}' define un ancho mínimo/fijo de "
                            f"~{declared_width_px:.0f}px que satura o excede el límite "
                            f"del breakpoint '{media_query.strip()}' ({bp_px:.0f}px)."
                        )
                        issues.append(
                            {
                                "severity": "WARNING",
                                "file": rel_path,
                                "start_line": start_line,
                                "end_line": end_line,
                                "selector": selector,
                                "category": "Breakpoint Width Overflow",
                                "message": msg_text,
                                "recommendation": (
                                    "Usar dimensiones relativas (%, fr, vw) o "
                                    "reducir anchos rígidos en este breakpoint."
                                ),
                            }
                        )

            # 4. WARNING: Ruptura de texto en contenedores de texto dinámico largo
            dynamic_text_keywords = (
                "msg-body",
                "sys-text",
                "convo-a",
                "convo-q",
                "toast-text",
                "agent-convo",
                "comment-body",
                "user-bio",
                "post-body",
                "description",
                "summary",
                "article",
                "p",
                "textarea",
            )
            compact_ui_keywords = (
                "btn",
                "button",
                "tab",
                "badge",
                "select",
                "header",
                "footer",
                "nav",
                "bar",
                "icon",
                "avatar",
                "trigger",
                "arrow",
                "toggle",
                "checkbox",
                "radio",
                "input",
                "slider",
            )

            is_compact_ui = any(k in clean_sel for k in compact_ui_keywords)
            is_dynamic_text = any(
                clean_sel == tag
                or clean_sel.endswith(f" {tag}")
                or f".{tag}" in clean_sel
                or f"#{tag}" in clean_sel
                or tag in clean_sel
                for tag in dynamic_text_keywords
            )

            if is_dynamic_text and not is_compact_ui:
                has_break = (
                    word_break in ("break-word", "break-all")
                    or overflow_wrap in ("break-word", "anywhere")
                    or "ellipsis" in props.get("text-overflow", "").lower()
                )
                if not has_break:
                    msg_text = (
                        f"El contenedor de texto '{selector}' no especifica reglas "
                        "de rotura ('overflow-wrap: break-word' o 'word-break')."
                    )
                    issues.append(
                        {
                            "severity": "WARNING",
                            "file": rel_path,
                            "start_line": start_line,
                            "end_line": end_line,
                            "selector": selector,
                            "category": "Text Break Risk",
                            "message": msg_text,
                            "recommendation": ("Agregar 'overflow-wrap: break-word;'."),
                        }
                    )

            # 5. WARNING: Ancho fijo estricto en px sin max-width
            if width and width.endswith("px") and not max_w:
                try:
                    px_val = int(re.sub(r"[^\d]", "", width))
                    if px_val > 400:
                        msg_text = (
                            f"La regla '{selector}' tiene un ancho fijo estricto "
                            f"de '{width}' sin 'max-width: 100%'."
                        )
                        issues.append(
                            {
                                "severity": "WARNING",
                                "file": rel_path,
                                "start_line": start_line,
                                "end_line": end_line,
                                "selector": selector,
                                "category": "Fixed Width Responsive Risk",
                                "message": msg_text,
                                "recommendation": (
                                    "Usar 'max-width: 100%;' o unidades relativas."
                                ),
                            }
                        )
                except ValueError:
                    pass

            # 6. INFO: Z-Index elevado
            if z_index:
                try:
                    z_val = int(re.sub(r"[^\d-]", "", z_index))
                    valid_pos = ("relative", "absolute", "fixed", "sticky")
                    if (
                        z_val >= 100
                        and isolation != "isolate"
                        and position not in valid_pos
                    ):
                        msg_text = (
                            f"La regla '{selector}' asigna z-index: {z_val} "
                            "sin establecer contexto de apilamiento aislado."
                        )
                        issues.append(
                            {
                                "severity": "INFO",
                                "file": rel_path,
                                "start_line": start_line,
                                "end_line": end_line,
                                "selector": selector,
                                "category": "Z-Index Stacking Conflict",
                                "message": msg_text,
                                "recommendation": ("Agregar 'isolation: isolate;'."),
                            }
                        )
                except ValueError:
                    pass

    # Filtrar por nivel de severidad si fue especificado
    if severity_filter and severity_filter.upper() != "ALL":
        target_sev = severity_filter.upper()
        issues = [i for i in issues if i["severity"] == target_sev]

    # Ordenar por severidad (CRITICAL primero, luego WARNING, luego INFO)
    severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    issues.sort(
        key=lambda x: (
            severity_order.get(x["severity"], 3),
            x["file"],
            x["start_line"],
        )
    )

    return {
        "status": "success",
        "total_issues": len(issues),
        "issues": issues,
        "severity_filter": severity_filter or "ALL",
    }


def format_audit_report(data: dict[str, Any]) -> str:
    """Formatea el reporte de auditoría de layout en texto claro para el agente."""
    if data.get("status") != "success":
        return f"ERROR: {data.get('message', 'No audit results available.')}"

    issues = data.get("issues", [])
    total = data.get("total_issues", 0)
    sev_filter = data.get("severity_filter", "ALL")

    if not issues:
        return (
            f"[CSS Layout Audit — 0 issues found (Severity Filter: {sev_filter})]\n"
            "  No CSS layout risk anti-patterns detected."
        )

    critical_count = sum(1 for i in issues if i["severity"] == "CRITICAL")
    warning_count = sum(1 for i in issues if i["severity"] == "WARNING")
    info_count = sum(1 for i in issues if i["severity"] == "INFO")

    summary_hdr = (
        f"[CSS Layout Audit — {total} issues found "
        f"({critical_count} CRITICAL, {warning_count} WARNING, {info_count} INFO)]"
    )
    lines = [summary_hdr]

    for issue in issues:
        sev_tag = f"[{issue['severity']}]"
        loc = f"{issue['file']}:L{issue['start_line']}-{issue['end_line']}"
        lines.append(f"\n{sev_tag} {loc} | {issue['category']}")
        lines.append(f"  Selector: {issue['selector']}")
        lines.append(f"  Issue: {issue['message']}")
        lines.append(f"  Fix: {issue['recommendation']}")

    return "\n".join(lines)
