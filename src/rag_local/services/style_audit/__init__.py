from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.services.db import get_indexed_metadata
from rag_local.services.style_audit.context import build_audit_context
from rag_local.services.style_audit.evaluators import (
    eval_2d_breakpoint_collision,
    eval_absolute_overflow_clipping_trap,
    eval_aspect_ratio_height_risk,
    eval_breakpoint_consistency,
    eval_breakpoint_overflow,
    eval_fixed_width_risk,
    eval_flex_column_scroll_risk,
    eval_flex_fixed_control_shrink_risk,
    eval_flex_wrap_risk,
    eval_flexbox_overflow_risk,
    eval_grid_min_content_overflow,
    eval_inline_style_responsive_override,
    eval_invalid_css_shorthand,
    eval_landscape_exclusion_trap,
    eval_modal_landscape_overflow,
    eval_rigid_height_landscape_risk,
    eval_stacking_context_trap,
    eval_text_break_risk,
    eval_tooltip_viewport_overflow,
    eval_z_index_conflict,
    eval_z_index_hierarchy,
)
from rag_local.services.style_audit.formatter import format_audit_report
from rag_local.services.style_audit.models import (
    CSS_EXTENSIONS,
    RESET_SELECTORS,
    is_pseudo_class,
    is_reset_selector,
)

# Compatibilidad con código legado o tests
_CSS_EXTENSIONS = CSS_EXTENSIONS
_RESET_SELECTORS = RESET_SELECTORS
_is_reset_selector = is_reset_selector


def audit_layout_risks(
    repo_path: str | None = None,
    severity_filter: str | None = None,
    file_filter: str | None = None,
) -> dict[str, Any]:
    """Realiza una auditoría de riesgos y antipatrones de layout CSS

    consultando las reglas estructuradas y metadatos indexados en LanceDB.
    """
    root = Path(repo_path) if repo_path else config.REPO_ROOT
    if not root.exists():
        return {
            "status": "error",
            "message": f"La ruta especificada no existe: {root}",
            "issues": [],
        }

    # Procesar filtros múltiples por coma en file_filter
    file_filters: list[str] = []
    if file_filter:
        file_filters = [f.strip().lower() for f in file_filter.split(",") if f.strip()]

    # Consultar metadatos indexados en LanceDB usando la referencia del módulo
    rows = get_indexed_metadata(
        ["source", "tags", "dependencies", "css_rules", "class_parents", "type"],
        limit=50000,
    )
    if not rows:
        return {
            "status": "empty",
            "message": (
                "La base de datos está vacía o no existe. "
                "Ejecuta ingest_codebase primero."
            ),
            "issues": [],
        }

    ctx = build_audit_context(rows)
    issues: list[dict[str, Any]] = []
    reported_stacking_traps: set[tuple[str, str]] = set()

    # 1. Auditar reglas de archivos CSS
    for rel_path, rules in ctx.parsed_css_by_file.items():
        if file_filters and not any(flt in rel_path.lower() for flt in file_filters):
            continue

        if not rules:
            continue

        # Selectores con mitigación de overflow (hidden/auto/scroll) en este archivo
        mitigating_selectors = {
            r.get("selector", "").strip()
            for r in rules
            if any(
                kw in r.get("properties", {}).get("overflow", "").lower()
                or kw in r.get("properties", {}).get("overflow-x", "").lower()
                or kw in r.get("properties", {}).get("overflow-y", "").lower()
                for kw in ("hidden", "auto", "scroll")
            )
        }

        for rule in rules:
            selector = rule.get("selector", "").strip()
            clean_sel = selector.lower()

            # Excluir resets CSS incluyendo selectores agrupados (ej. 'html, body')
            if is_reset_selector(clean_sel):
                continue

            # Flexbox / Grid overflow risk
            issue_flex = eval_flexbox_overflow_risk(
                rule, rel_path, ctx, mitigating_selectors
            )
            if issue_flex:
                issues.append(issue_flex)

            # Omitir pseudo-clases en auditoría subsiguiente de layout y texto
            if is_pseudo_class(clean_sel):
                continue

            # Flex Wrap Overflow Risk
            issue_wrap = eval_flex_wrap_risk(rule, rel_path, ctx, mitigating_selectors)
            if issue_wrap:
                issues.append(issue_wrap)

            # Breakpoint Width Overflow
            issue_bp = eval_breakpoint_overflow(rule, rel_path)
            if issue_bp:
                issues.append(issue_bp)

            # Ruptura de texto en contenedores de texto dinámico largo
            issue_text = eval_text_break_risk(rule, rel_path, ctx)
            if issue_text:
                issues.append(issue_text)

            # Ancho fijo estricto en px sin max-width
            issue_fixed = eval_fixed_width_risk(rule, rel_path)
            if issue_fixed:
                issues.append(issue_fixed)

            # Z-Index elevado
            issue_z = eval_z_index_conflict(rule, rel_path, ctx)
            if issue_z:
                issues.append(issue_z)

            # Stacking Context Trap (deduplicado por archivo y clase raíz)
            issue_trap = eval_stacking_context_trap(rule, rel_path, ctx)
            if issue_trap:
                rule_cls = rule.get("classes", [])
                primary_cls = rule_cls[0] if rule_cls else rule.get("selector", "")
                trap_key = (rel_path, primary_cls)
                if trap_key not in reported_stacking_traps:
                    reported_stacking_traps.add(trap_key)
                    issues.append(issue_trap)

            # Flex Column Scroll Risk
            issue_col = eval_flex_column_scroll_risk(rule, rel_path, ctx)
            if issue_col:
                issues.append(issue_col)

            # Modal Landscape Overflow
            issue_modal = eval_modal_landscape_overflow(rule, rel_path, ctx)
            if issue_modal:
                issues.append(issue_modal)

            # Aspect Ratio Height Overflow Risk
            issue_aspect = eval_aspect_ratio_height_risk(rule, rel_path, ctx)
            if issue_aspect:
                issues.append(issue_aspect)

            # Altura rígida sin adaptación landscape
            issue_rigid = eval_rigid_height_landscape_risk(rule, rel_path, ctx)
            if issue_rigid:
                issues.append(issue_rigid)

            # Declaraciones shorthand inválidas
            issue_short = eval_invalid_css_shorthand(rule, rel_path, ctx)
            if issue_short:
                issues.append(issue_short)

            # Desbordamiento de tooltips centrados
            issue_tooltip = eval_tooltip_viewport_overflow(rule, rel_path)
            if issue_tooltip:
                issues.append(issue_tooltip)

            # Grid Track Min-Content Overflow
            issue_grid = eval_grid_min_content_overflow(rule, rel_path, ctx)
            if issue_grid:
                issues.append(issue_grid)

            # Controles fijos en Flex sin flex-shrink: 0
            issue_shrink = eval_flex_fixed_control_shrink_risk(rule, rel_path, ctx)
            if issue_shrink:
                issues.append(issue_shrink)

            # Trampa de exclusión en landscape
            issue_land = eval_landscape_exclusion_trap(rule, rel_path, ctx)
            if issue_land:
                issues.append(issue_land)

            # Recorte de elementos flotantes absolute en ancestros con overflow
            issue_clip = eval_absolute_overflow_clipping_trap(rule, rel_path, ctx)
            if issue_clip:
                issues.append(issue_clip)

    # 2. Auditar reglas de estilo inline en componentes JS / HTML
    for rel_path, inline_rules in ctx.parsed_inline_rules_by_file.items():
        if file_filters and not any(flt in rel_path.lower() for flt in file_filters):
            continue

        for irule in inline_rules:
            line = irule.get("line", 1)
            props = irule.get("properties", {})
            classes = irule.get("classes", [])
            tag = irule.get("tag", "element")
            sel_desc = (
                f"inline style in <{tag}> (line {line})"
                if not classes
                else f"inline style on .{classes[0]} (line {line})"
            )
            pseudo_rule = {
                "selector": sel_desc,
                "classes": classes,
                "start_line": line,
                "end_line": line,
                "properties": props,
                "media_query": "",
                "is_inline": True,
            }

            i_flex = eval_flexbox_overflow_risk(pseudo_rule, rel_path, ctx, set())
            if i_flex:
                issues.append(i_flex)

            i_wrap = eval_flex_wrap_risk(pseudo_rule, rel_path, ctx, set())
            if i_wrap:
                issues.append(i_wrap)

            i_col = eval_flex_column_scroll_risk(pseudo_rule, rel_path, ctx)
            if i_col:
                issues.append(i_col)

            i_aspect = eval_aspect_ratio_height_risk(pseudo_rule, rel_path, ctx)
            if i_aspect:
                issues.append(i_aspect)

            i_text = eval_text_break_risk(pseudo_rule, rel_path, ctx)
            if i_text:
                issues.append(i_text)

            i_short = eval_invalid_css_shorthand(pseudo_rule, rel_path, ctx)
            if i_short:
                issues.append(i_short)

            i_fixed = eval_fixed_width_risk(pseudo_rule, rel_path)
            if i_fixed:
                issues.append(i_fixed)

            i_shrink = eval_flex_fixed_control_shrink_risk(pseudo_rule, rel_path, ctx)
            if i_shrink:
                issues.append(i_shrink)

            i_inline_resp = eval_inline_style_responsive_override(
                pseudo_rule, rel_path, ctx
            )
            if i_inline_resp:
                issues.append(i_inline_resp)

    # 3. Evaluaciones globales / entre archivos
    z_hierarchy_issues = eval_z_index_hierarchy(ctx)
    for zh in z_hierarchy_issues:
        if not file_filters or any(flt in zh["file"].lower() for flt in file_filters):
            issues.append(zh)

    bp_consistency_issues = eval_breakpoint_consistency(ctx)
    for bp in bp_consistency_issues:
        if not file_filters or any(flt in bp["file"].lower() for flt in file_filters):
            issues.append(bp)

    collision_2d_issues = eval_2d_breakpoint_collision(ctx)
    for c2d in collision_2d_issues:
        if not file_filters or any(flt in c2d["file"].lower() for flt in file_filters):
            issues.append(c2d)

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
        "css_files_count": len(ctx.parsed_css_by_file),
        "severity_filter": severity_filter or "ALL",
    }


__all__ = [
    "_CSS_EXTENSIONS",
    "_RESET_SELECTORS",
    "_is_reset_selector",
    "audit_layout_risks",
    "format_audit_report",
    "get_indexed_metadata",
]
