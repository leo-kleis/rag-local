from pathlib import Path
from typing import Any

from rag_local.core import config
from rag_local.services.db import get_indexed_metadata
from rag_local.services.style_audit.context import build_audit_context
from rag_local.services.style_audit.evaluators import (
    eval_breakpoint_consistency,
    eval_breakpoint_overflow,
    eval_fixed_width_risk,
    eval_flex_column_scroll_risk,
    eval_flex_wrap_risk,
    eval_flexbox_overflow_risk,
    eval_modal_landscape_overflow,
    eval_stacking_context_trap,
    eval_text_break_risk,
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

            # 1. Flexbox / Grid overflow risk
            issue_flex = eval_flexbox_overflow_risk(
                rule, rel_path, ctx, mitigating_selectors
            )
            if issue_flex:
                issues.append(issue_flex)

            # Omitir pseudo-clases en auditoría subsiguiente de layout y texto
            if is_pseudo_class(clean_sel):
                continue

            # 2. Flex Wrap Overflow Risk
            issue_wrap = eval_flex_wrap_risk(rule, rel_path, ctx, mitigating_selectors)
            if issue_wrap:
                issues.append(issue_wrap)

            # 3. Breakpoint Width Overflow
            issue_bp = eval_breakpoint_overflow(rule, rel_path)
            if issue_bp:
                issues.append(issue_bp)

            # 4. Ruptura de texto en contenedores de texto dinámico largo
            issue_text = eval_text_break_risk(rule, rel_path, ctx)
            if issue_text:
                issues.append(issue_text)

            # 5. Ancho fijo estricto en px sin max-width
            issue_fixed = eval_fixed_width_risk(rule, rel_path)
            if issue_fixed:
                issues.append(issue_fixed)

            # 6. Z-Index elevado
            issue_z = eval_z_index_conflict(rule, rel_path)
            if issue_z:
                issues.append(issue_z)

            # 7. Stacking Context Trap (elementos fijos atrapados en ancestros aislados)
            issue_trap = eval_stacking_context_trap(rule, rel_path, ctx)
            if issue_trap:
                issues.append(issue_trap)

            # 8. Flex Column Scroll Risk (scroll vertical flex sin min-height: 0)
            issue_col = eval_flex_column_scroll_risk(rule, rel_path, ctx)
            if issue_col:
                issues.append(issue_col)

            # 9. Modal Landscape Overflow (modales centrados sin scroll)
            issue_modal = eval_modal_landscape_overflow(rule, rel_path)
            if issue_modal:
                issues.append(issue_modal)

    # Evaluaciones globales / entre archivos
    z_hierarchy_issues = eval_z_index_hierarchy(ctx)
    for zh in z_hierarchy_issues:
        if not file_filters or any(flt in zh["file"].lower() for flt in file_filters):
            issues.append(zh)

    bp_consistency_issues = eval_breakpoint_consistency(ctx)
    for bp in bp_consistency_issues:
        if not file_filters or any(flt in bp["file"].lower() for flt in file_filters):
            issues.append(bp)

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


__all__ = [
    "_CSS_EXTENSIONS",
    "_RESET_SELECTORS",
    "_is_reset_selector",
    "audit_layout_risks",
    "format_audit_report",
    "get_indexed_metadata",
]
