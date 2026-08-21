import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.evaluators.common import (
    analyze_rule_dynamics,
    create_audit_issue,
)
from rag_local.services.style_audit.models import (
    NATIVE_TEXT_TAGS,
    NON_TEXT_ELEMENTS,
    PROSE_TEXT_KEYWORDS,
    extract_terminal_tag,
    is_atomic_or_micro_ui,
    is_break_protected,
    is_modal_or_overlay,
)

_CONTAINER_OR_ITEM_TOKENS = {
    "list",
    "table",
    "grid",
    "feed",
    "wrap",
    "wrapper",
    "container",
    "group",
    "box",
    "area",
    "panel",
    "bar",
    "field",
    "dropdown",
    "item",
    "option",
    "tab",
    "trigger",
}

_EXCLUDED_TERMINAL_TAGS = {
    "th",
    "td",
    "input",
    "textarea",
    "select",
    "button",
    "option",
    "label",
}


def eval_text_break_risk(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
) -> dict[str, Any] | None:
    """Evalúa el riesgo de ruptura o desborde de texto dinámico largo."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    # Excluir reglas con break protection (word-break, wrap, ellipsis, nowrap)
    if is_break_protected(props):
        return None

    disp = props.get("display", "").lower()
    if "flex" in disp or "grid" in disp:
        return None

    # Excluir backdrops, modales y overlays
    if is_modal_or_overlay(props):
        return None

    # Excluir reglas en media queries que no definan texto sustancial (solo overrides)
    media_query = rule.get("media_query", "")
    if media_query and not any(
        k in props for k in ("font-family", "word-break", "overflow-wrap")
    ):
        return None

    # Excluir contenedores de scroll explícito (viewports)
    overflow = props.get("overflow", "").lower()
    overflow_x = props.get("overflow-x", "").lower()
    overflow_y = props.get("overflow-y", "").lower()
    if any(
        kw in overflow or kw in overflow_x or kw in overflow_y
        for kw in ("auto", "scroll")
    ):
        return None

    rule_classes = rule.get("classes", [])
    terminal_part = re.split(r"[\s>+~]", clean_sel)[-1].strip()
    terminal_tag = extract_terminal_tag(clean_sel)

    # Excluir pseudo-elementos
    if any(
        terminal_part.endswith(f"::{pseudo}")
        for pseudo in ("before", "after", "backdrop", "marker")
    ):
        return None

    # Excluir etiquetas no textuales o de tabla/formulario
    if terminal_tag in _EXCLUDED_TERMINAL_TAGS:
        return None

    # Excluir contenedores estructurales de lista/tabla/grilla o micro-items
    terminal_tokens = re.findall(r"[a-z0-9]+", terminal_part)
    if terminal_tokens and terminal_tokens[-1] in _CONTAINER_OR_ITEM_TOKENS:
        return None

    # Excluir componentes atómicos y micro-UI (iconos, badges, botones, timestamps)
    if is_atomic_or_micro_ui(clean_sel, rule_classes):
        return None

    has_native_text_tag = terminal_tag in NATIVE_TEXT_TAGS
    font_size = props.get("font-size", "").lower()
    line_height = props.get("line-height", "").lower()
    letter_spacing = props.get("letter-spacing", "").lower()
    has_typography_props = bool(font_size or line_height or letter_spacing)
    has_text_signal = has_native_text_tag or has_typography_props

    (
        has_markup_data,
        rule_has_dynamic,
        rule_is_mixed_dynamic,
        _,
        _,
    ) = analyze_rule_dynamics(rule_classes, ctx)

    rule_own_tags: set[str] = set()
    for cname in rule_classes:
        rule_own_tags.update(ctx.class_own_tags.get(cname, set()))

    effective_non_text_tags = (
        rule_own_tags if (has_markup_data and rule_own_tags) else {terminal_tag}
    )
    is_non_text_subject = bool(effective_non_text_tags & NON_TEXT_ELEMENTS) or bool(
        effective_non_text_tags & {"textarea", "input", "select", "button", "th", "td"}
    )
    if is_non_text_subject:
        return None

    # Detectar si el selector apunta a prosa/texto largo explícito
    is_prose_keyword = any(
        kw in clean_sel or any(kw in c.lower() for c in rule_classes)
        for kw in PROSE_TEXT_KEYWORDS
    )

    # Excluir encabezados y textos descriptivos estándar de modales/diálogos
    is_heading = terminal_tag in ("h1", "h2", "h3", "h4", "h5", "h6")
    is_dialog_context = any(
        kw in clean_sel or any(kw in c.lower() for c in rule_classes)
        for kw in ("modal", "dialog", "alert", "prompt", "toast")
    )
    if is_dialog_context and (is_heading or not is_prose_keyword):
        return None

    if has_markup_data:
        is_text_target = (has_text_signal or is_prose_keyword) and (
            rule_has_dynamic or rule_is_mixed_dynamic
        )
    else:
        # En ausencia de markup, alertar solo en semántica de prosa o tags largos
        is_prose_tag = terminal_tag in ("blockquote", "article", "p")
        is_text_target = (is_prose_keyword or is_prose_tag) and has_text_signal

    is_pure_utility = not any(
        k in props
        for k in (
            "font-size",
            "line-height",
            "display",
            "width",
            "max-width",
            "padding",
        )
    )

    if is_text_target and not is_pure_utility:
        msg_text = (
            f"Text container '{selector}' does not specify wrap rules "
            "('overflow-wrap: anywhere' or 'overflow-wrap: break-word')."
        )
        text_break_sev = "WARNING" if has_markup_data else "INFO"
        return create_audit_issue(
            severity=text_break_sev,
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Text Break Risk",
            message=msg_text,
            recommendation=(
                "Add 'overflow-wrap: anywhere;' or 'overflow-wrap: break-word;'."
            ),
        )

    return None
