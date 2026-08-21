import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext

_RE_DIMENSION = re.compile(r"^([\d.]+)\s*(px|rem|em)$", re.IGNORECASE)


def analyze_rule_dynamics(
    rule_classes: list[str],
    ctx: AuditContext,
) -> tuple[bool, bool, bool, int, bool]:
    """Calcula las señales de contexto dinámico y colección para una regla."""
    has_markup_data = any(ctx.class_dynamic_contexts.get(c) for c in rule_classes)
    rule_has_dynamic = False
    rule_is_mixed_dynamic = False
    rule_mixed_files_count = 0
    rule_is_collection = False

    for cname in rule_classes:
        contexts = ctx.class_dynamic_contexts.get(cname, [])
        if contexts:
            dyn_flags = [has_dyn for (_, has_dyn, _) in contexts]
            col_flags = [is_col for (_, _, is_col) in contexts]

            if any(col_flags):
                rule_is_collection = True

            if all(dyn_flags) and any(dyn_flags):
                rule_has_dynamic = True
            elif any(dyn_flags):
                rule_is_mixed_dynamic = True
                rule_mixed_files_count = len({f for (f, _, _) in contexts})

    return (
        has_markup_data,
        rule_has_dynamic,
        rule_is_mixed_dynamic,
        rule_mixed_files_count,
        rule_is_collection,
    )


# Alias para retrocompatibilidad
_analyze_rule_dynamics = analyze_rule_dynamics


def parse_css_dimension_px(val: str, base_font_size: float = 16.0) -> float | None:
    """Convierte un valor de dimensión CSS (px, rem, em) a píxeles numéricos."""
    if not val:
        return None
    m = _RE_DIMENSION.search(val.strip())
    if not m:
        return None
    try:
        num = float(m.group(1))
        unit = m.group(2).lower()
        return num * base_font_size if unit in ("rem", "em") else num
    except ValueError:
        return None


def check_parent_mitigation(rule_classes: list[str], ctx: AuditContext) -> str:
    """Retorna la clase ancestro que proporciona mitigación de overflow."""
    for cname in rule_classes:
        parents = ctx.component_parent_map.get(cname, set())
        for p in parents:
            if p in ctx.project_mitigated_classes:
                return p
    return ""


def check_file_mitigation(selector: str, mitigating_selectors: set[str]) -> bool:
    """Verifica si el selector está mitigado en el mismo archivo."""
    return any(
        m_sel != selector
        and (m_sel in selector or any(part in selector for part in m_sel.split()))
        for m_sel in mitigating_selectors
    )


def create_audit_issue(
    *,
    severity: str,
    file: str,
    start_line: int,
    end_line: int,
    selector: str,
    category: str,
    message: str,
    recommendation: str,
) -> dict[str, Any]:
    """Crea un diccionario tipado y estandarizado con el reporte de un issue."""
    return {
        "severity": severity,
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "selector": selector,
        "category": category,
        "message": message,
        "recommendation": recommendation,
    }
