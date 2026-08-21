import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.evaluators.common import create_audit_issue

_SHORTHAND_PROPS = (
    "border",
    "border-top",
    "border-bottom",
    "border-left",
    "border-right",
    "outline",
)

_COLOR_PATTERNS = (
    re.compile(r"^#(?:[0-9a-fA-F]{3,8})$"),
    re.compile(r"^rgba?\(", re.IGNORECASE),
    re.compile(r"^hsla?\(", re.IGNORECASE),
    re.compile(
        r"^(?:transparent|currentColor|red|blue|green|white|black|purple|gray)$",
        re.IGNORECASE,
    ),
)

_STYLE_KEYWORDS = {
    "solid",
    "dashed",
    "dotted",
    "double",
    "groove",
    "ridge",
    "inset",
    "outset",
    "none",
    "hidden",
}

_RE_WIDTH_PATTERN = re.compile(
    r"\b\d+(?:px|rem|em|%)|\b(?:thin|medium|thick)\b", re.IGNORECASE
)


def eval_invalid_css_shorthand(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
) -> dict[str, Any] | None:
    """Detecta declaraciones shorthand inválidas (ej. 'border: var(--color)')."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})

    for prop_name in _SHORTHAND_PROPS:
        val = props.get(prop_name, "").strip()
        if not val:
            continue
        resolved_val = ctx.resolve_css_value(val).strip()
        is_color_only = any(pat.search(resolved_val) for pat in _COLOR_PATTERNS)
        has_style = bool(set(resolved_val.lower().split()) & _STYLE_KEYWORDS)
        has_width = bool(_RE_WIDTH_PATTERN.search(resolved_val))

        if is_color_only and not has_style and not has_width:
            msg_text = (
                f"Declaration '{prop_name}: {val}' in '{selector}' "
                f"resolves to '{resolved_val}', which is a color only. Shorthand "
                f"properties of type '{prop_name}' require style and width "
                f"(e.g. '1px solid {val}'), and will be ignored by browsers."
            )
            return create_audit_issue(
                severity="WARNING",
                file=rel_path,
                start_line=start_line,
                end_line=end_line,
                selector=selector,
                category="Invalid CSS Shorthand",
                message=msg_text,
                recommendation=f"Change to '{prop_name}: 1px solid {val};'.",
            )
    return None
