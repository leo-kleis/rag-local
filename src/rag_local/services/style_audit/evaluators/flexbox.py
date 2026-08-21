import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.evaluators.common import (
    analyze_rule_dynamics,
    check_file_mitigation,
    check_parent_mitigation,
    create_audit_issue,
    parse_css_dimension_px,
)
from rag_local.services.style_audit.models import (
    COLLECTION_CLASS_KEYWORDS,
    FIXED_CONTROL_KEYWORDS,
    INPUT_TOOLBAR_KEYWORDS,
    INTRINSIC_INPUT_TAGS,
    MENU_ITEM_KEYWORDS,
    NATIVE_TEXT_TAGS,
    extract_terminal_tag,
    is_atomic_or_micro_ui,
    is_break_protected,
    is_modal_or_overlay,
)


def eval_flexbox_overflow_risk(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
    mitigating_selectors: set[str],
) -> dict[str, Any] | None:
    """Evalúa el riesgo de overflow en contenedores o hijos Flexbox/Grid."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    disp = props.get("display", "").lower()
    flex_direction = props.get("flex-direction", "").lower()
    flex_wrap = props.get("flex-wrap", "").lower()
    flex_prop = props.get("flex", "").lower()
    flex_grow = props.get("flex-grow", "").lower()
    flex_shrink = props.get("flex-shrink", "").lower()
    min_w = props.get("min-width", "").lower()
    min_h = props.get("min-height", "").lower()
    overflow = props.get("overflow", "").lower()
    overflow_x = props.get("overflow-x", "").lower()
    overflow_y = props.get("overflow-y", "").lower()
    width = props.get("width", "").lower()
    height = props.get("height", "").lower()
    pos = props.get("position", "").lower()

    # Excluir flex vertical: en el eje vertical no existe riesgo de min-width: auto
    if "column" in flex_direction:
        return None

    # Excluir flex-wrap activo: los hijos se envuelven naturalmente
    if flex_wrap in ("wrap", "wrap-reverse"):
        return None

    # Excluir backdrops, modales y overlays centrados
    if is_modal_or_overlay(props):
        return None

    # Excluir elementos absolutos/fijos centrados o flotantes
    if pos in ("absolute", "fixed") and (
        "50%" in props.get("left", "")
        or "translate" in props.get("transform", "").lower()
    ):
        return None

    has_no_shrink = flex_shrink == "0" or "0 0 " in flex_prop or flex_prop == "none"
    has_fixed_size = (
        width.endswith("px") and height.endswith("px")
    ) or width == "fit-content"
    has_overflow_control = any(
        kw in overflow or kw in overflow_x or kw in overflow_y
        for kw in ("hidden", "auto", "scroll")
    )

    terminal_tag = extract_terminal_tag(clean_sel)
    has_native_text_tag = terminal_tag in NATIVE_TEXT_TAGS
    has_typography_props = bool(
        props.get("font-size")
        or props.get("line-height")
        or props.get("letter-spacing")
    )
    has_text_signal = has_native_text_tag or has_typography_props

    rule_classes = rule.get("classes", [])
    (
        has_markup_data,
        rule_has_dynamic,
        rule_is_mixed_dynamic,
        rule_mixed_files_count,
        _,
    ) = analyze_rule_dynamics(rule_classes, ctx)

    # Excluir micro-UI / badges a menos que especifiquen truncamiento elíptico
    has_ellipsis = "ellipsis" in props.get("text-overflow", "").lower()
    if is_atomic_or_micro_ui(clean_sel, rule_classes) and not has_ellipsis:
        return None

    is_atomic_inline_flex = disp == "inline-flex"
    is_flex_child = bool(flex_prop) or flex_grow in ("1", "2", "3", "4", "5")
    is_flex_container = ("flex" in disp and not is_atomic_inline_flex) or (
        "grid" in disp
    )

    justify_content = props.get("justify-content", "").lower()
    has_fixed_distribution = any(
        kw in justify_content
        for kw in ("space-between", "space-around", "space-evenly")
    )
    if not is_flex_child and has_fixed_distribution:
        return None

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
        # Contenedores flex alertan con señal de texto o reglas inline
        if (
            not is_flex_child
            and not rule_has_dynamic
            and not (has_text_signal and has_markup_data)
            and not has_text_signal
            and not rule.get("is_inline")
        ):
            return None

        # Excluir si algún contenedor padre declara flex-wrap activo
        parent_classes = set()
        for c in rule_classes:
            parent_classes.update(ctx.component_parent_map.get(c, set()))
        if bool(parent_classes & ctx.flex_wrap_classes):
            return None

        file_mitigated = check_file_mitigation(selector, mitigating_selectors)
        parent_mitigation_class = check_parent_mitigation(rule_classes, ctx)
        is_mitigated = file_mitigated or bool(parent_mitigation_class)

        # Supresión de hallazgos mitigados (no emitir avisos ruidosos)
        if is_mitigated:
            return None

        if rule_has_dynamic:
            sev = "CRITICAL"
        elif rule_is_mixed_dynamic or (not has_markup_data and has_text_signal):
            sev = "WARNING"
        else:
            sev = "WARNING"

        if rule_is_mixed_dynamic:
            mit_suffix = (
                f" (used in multiple contexts "
                f"({rule_mixed_files_count} files), verify manually)"
            )
        else:
            mit_suffix = ""

        msg_text = (
            f"Flex container/child '{selector}' "
            f"(display: {disp or 'flex-item'}) "
            "lacks 'min-width: 0' or 'overflow: hidden'"
            f"{mit_suffix}."
        )
        return create_audit_issue(
            severity=sev,
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Flexbox/Grid Overflow Risk",
            message=msg_text,
            recommendation="Add 'min-width: 0;' or 'overflow: hidden;'.",
        )

    return None


def eval_flex_wrap_risk(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
    mitigating_selectors: set[str],
) -> dict[str, Any] | None:
    """Evalúa el riesgo de desbordamiento por falta de flex-wrap en flex horizontal."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    disp = props.get("display", "").lower()
    flex_direction = props.get("flex-direction", "").lower()
    flex_wrap = props.get("flex-wrap", "").lower()
    justify_content = props.get("justify-content", "").lower()
    overflow = props.get("overflow", "").lower()
    overflow_x = props.get("overflow-x", "").lower()
    overflow_y = props.get("overflow-y", "").lower()
    width = props.get("width", "").lower()
    height = props.get("height", "").lower()
    white_space = props.get("white-space", "").lower()
    text_overflow = props.get("text-overflow", "").lower()

    # Solo aplica a flex horizontal sin wrap
    is_horizontal_flex = ("flex" in disp) and ("column" not in flex_direction)
    has_wrap = flex_wrap in ("wrap", "wrap-reverse")
    if not is_horizontal_flex or has_wrap or disp == "inline-flex":
        return None

    # Excluir backdrops, modales y overlays
    if is_modal_or_overlay(props):
        return None

    has_fixed_size = width.endswith("px") and height.endswith("px")
    has_overflow_control = any(
        kw in overflow or kw in overflow_x or kw in overflow_y
        for kw in ("hidden", "auto", "scroll")
    )
    has_fixed_distribution = any(
        kw in justify_content
        for kw in ("space-between", "space-around", "space-evenly")
    )
    has_nowrap_handling = (
        "nowrap" in white_space
        or "ellipsis" in text_overflow
        or is_break_protected(props)
    )

    if has_fixed_size or has_overflow_control or has_nowrap_handling:
        return None

    rule_classes = rule.get("classes", [])
    (_, _, _, _, rule_is_collection) = analyze_rule_dynamics(rule_classes, ctx)

    # Excluir cabeceras de modales, diálogos, paneles y filas compuestas
    header_terms = {
        "header",
        "title",
        "modal-top",
        "dialog-top",
        "top-bar",
        "card-header",
        "panel-header",
        "drawer-header",
        "user-top",
        "user-bottom",
        "action-row",
        "filter-group",
        "filter-row",
        "item-row",
        "meta-row",
    }
    if any(
        term in clean_sel or any(term in c.lower() for c in rule_classes)
        for term in header_terms
    ):
        return None

    # Excluir inline styles con distribución fija (space-between) o min-width
    min_w = props.get("min-width", "").lower()
    is_inline_rule = rule.get("is_inline", False) or selector.startswith("inline style")
    if is_inline_rule and (
        has_fixed_distribution or min_w in ("0", "0px", "0%", "none")
    ):
        return None

    # Excluir filas con distribución fija y min-width: 0
    if has_fixed_distribution and min_w in ("0", "0px", "0%", "none"):
        return None

    # Excluir si el selector conmuta a column/wrap/block en media query responsiva
    file_rules = ctx.parsed_css_by_file.get(rel_path, [])
    has_responsive_column = False
    for r in file_rules:
        r_mq = r.get("media_query", "").lower()
        if r_mq and ("max-width" in r_mq or "max-height" in r_mq):
            r_sel = r.get("selector", "").strip()
            r_classes = r.get("classes", [])
            matches_rule = (
                r_sel == selector
                or (bool(r_classes) and bool(set(r_classes) & set(rule_classes)))
                or any(f".{c}" in r_sel for c in rule_classes)
            )
            if matches_rule:
                r_props = r.get("properties", {})
                r_flex_dir = r_props.get("flex-direction", "").lower()
                r_wrap = r_props.get("flex-wrap", "").lower()
                r_disp = r_props.get("display", "").lower()
                if (
                    "column" in r_flex_dir
                    or r_wrap in ("wrap", "wrap-reverse")
                    or r_disp in ("block", "grid")
                ):
                    has_responsive_column = True
                    break

    if has_responsive_column:
        return None

    # Excluir componentes atómicos, toolbars, barras de input, menú items
    is_input_toolbar = any(
        kw in clean_sel or any(kw in c.lower() for c in rule_classes)
        for kw in INPUT_TOOLBAR_KEYWORDS
    )
    is_menu_item = any(
        kw in clean_sel or any(kw in c.lower() for c in rule_classes)
        for kw in MENU_ITEM_KEYWORDS
    )
    if (
        is_input_toolbar
        or is_menu_item
        or is_atomic_or_micro_ui(clean_sel, rule_classes)
    ):
        return None

    # Detectar señal real de colección de múltiples elementos variables
    has_collection_class = any(
        kw in c.lower() for c in rule_classes for kw in COLLECTION_CLASS_KEYWORDS
    ) or any(kw in clean_sel for kw in COLLECTION_CLASS_KEYWORDS)
    has_combinator_signal = (
        "+" in selector
        or "~" in selector
        or clean_sel.endswith(" > *")
        or clean_sel.endswith(" li")
    )

    has_multiple_items_signal = (
        rule_is_collection
        or has_collection_class
        or has_combinator_signal
        or has_fixed_distribution
    )

    if has_multiple_items_signal:
        # Excluir contenedores donde algún hijo implementa truncamiento elástico
        child_classes = {
            c
            for c, parents in ctx.component_parent_map.items()
            if any(rc in parents for rc in rule_classes)
        }
        all_candidates = list(ctx.parsed_css_by_file.get(rel_path, [])) + list(
            ctx.parsed_inline_rules_by_file.get(rel_path, [])
        )
        has_flexible_child = False
        has_fixed_icons = False
        for r in all_candidates:
            r_sel = r.get("selector", "").strip()
            r_classes = set(r.get("classes", []))
            is_child_rule = (
                bool(child_classes & r_classes)
                or (selector in r_sel and r_sel != selector)
                or any(f".{c}" in r_sel for c in rule_classes if f".{c}" != r_sel)
            )
            if is_child_rule:
                r_props = r.get("properties", {})
                r_text_ov = r_props.get("text-overflow", "").lower()
                r_overflow = r_props.get("overflow", "").lower()
                r_min_w = r_props.get("min-width", "").lower()
                r_flex = r_props.get("flex", "").lower()
                r_flex_grow = r_props.get("flex-grow", "").lower()
                r_flex_shrink = r_props.get("flex-shrink", "").lower()

                if (
                    "ellipsis" in r_text_ov
                    or is_break_protected(r_props)
                    or (
                        "hidden" in r_overflow
                        and (
                            r_min_w in ("0", "0px")
                            or r_flex == "1"
                            or r_flex_grow in ("1", "2")
                        )
                    )
                ):
                    return None

                if (
                    r_min_w in ("0", "0px")
                    or r_flex == "1"
                    or r_flex_grow in ("1", "2")
                ):
                    has_flexible_child = True
                if r_flex_shrink == "0" or "0 0 " in r_flex or r_flex == "none":
                    has_fixed_icons = True

        if has_flexible_child and has_fixed_icons:
            return None

        file_mitigated = check_file_mitigation(selector, mitigating_selectors)
        parent_mitigation_class = check_parent_mitigation(rule_classes, ctx)
        is_mitigated = file_mitigated or bool(parent_mitigation_class)

        # Supresión de hallazgos mitigados (no emitir avisos ruidosos)
        if is_mitigated:
            return None

        has_strong_collection_signal = (
            rule_is_collection or "+" in selector or "~" in selector
        )
        sev = "WARNING" if has_strong_collection_signal else "INFO"

        msg_text = (
            f"Horizontal flex container with multiple items '{selector}' "
            f"(display: {disp}) lacks 'flex-wrap: wrap' or "
            f"'overflow-x: auto', which may cause child overflow."
        )
        return create_audit_issue(
            severity=sev,
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Flex Wrap Overflow Risk",
            message=msg_text,
            recommendation="Add 'flex-wrap: wrap;' or 'overflow-x: auto;'.",
        )

    return None


def eval_flex_column_scroll_risk(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
) -> dict[str, Any] | None:
    """Detecta contenedores flex verticales scrollables que omiten 'min-height: 0'."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    overflow_y = props.get("overflow-y", "").lower()
    overflow = props.get("overflow", "").lower()
    flex_dir = props.get("flex-direction", "").lower()
    min_h = props.get("min-height", "").lower()
    height = props.get("height", "").lower()
    max_h = props.get("max-height", "").lower()
    flex_prop = props.get("flex", "").lower()

    has_scroll = any(kw in overflow_y or kw in overflow for kw in ("auto", "scroll"))
    is_flex_child = bool(flex_prop) or props.get("flex-grow", "") in ("1", "2", "3")
    is_col_flex = "column" in flex_dir

    if not has_scroll or not (is_flex_child or is_col_flex):
        return None

    # Si tiene min-height: 0 explícito, no hay riesgo de overflow
    if min_h in ("0", "0px", "0%", "none"):
        return None

    # Si tiene altura fija explícita en px, la dimensión está acotada
    if (height.endswith("px") and height != "0px") or max_h.endswith("px"):
        return None

    # Si no es un hijo flexible y tiene altura base explícita (100%, vh, dvh)
    has_bounded_height = height in ("100%", "100vh", "100dvh", "100cqh") or max_h in (
        "100%",
        "100vh",
        "100dvh",
    )
    if not is_flex_child and has_bounded_height:
        return None

    msg_text = (
        f"Scrollable container '{selector}' inside vertical Flex layout "
        "lacks 'min-height: 0'. By default in Flexbox, min-height is 'auto', "
        "preventing the container from shrinking to enable vertical scrolling."
    )
    return create_audit_issue(
        severity="WARNING",
        file=rel_path,
        start_line=start_line,
        end_line=end_line,
        selector=selector,
        category="Flex Column Scroll Risk",
        message=msg_text,
        recommendation="Add 'min-height: 0;' to scrollable container.",
    )


def eval_flex_fixed_control_shrink_risk(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
) -> dict[str, Any] | None:
    """Detecta controles fijos dentro de flexbox que omiten 'flex-shrink: 0'."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    flex_shrink = props.get("flex-shrink", "").lower()
    flex_prop = props.get("flex", "").lower()
    if flex_shrink == "0" or "0 0 " in flex_prop or flex_prop in ("none", "0"):
        return None

    width_prop = props.get("width", "").lower()
    height_prop = props.get("height", "").lower()

    w_px = parse_css_dimension_px(width_prop)
    h_px = parse_css_dimension_px(height_prop)

    if w_px is None or h_px is None:
        return None

    # Aplica a controles de dimensiones fijas (switches, toggles, badges <= 80px)
    if not (0 < w_px <= 80 and 0 < h_px <= 80):
        return None

    rule_classes = set(rule.get("classes", []))
    tokens = set(re.findall(r"[a-z0-9]+", clean_sel))
    for c in rule_classes:
        tokens.update(re.findall(r"[a-z0-9]+", c.lower()))

    is_fixed_control = bool(tokens & FIXED_CONTROL_KEYWORDS)
    if not is_fixed_control:
        return None

    # Verificar si el elemento está ubicado dentro de un contenedor flex o es hijo flex
    parent_classes = set()
    for c in rule_classes:
        parent_classes.update(ctx.component_parent_map.get(c, set()))

    is_descendant = any(comb in selector for comb in (" ", ">", "+", "~"))
    has_flex_parent = bool(parent_classes) or is_descendant or bool(flex_prop)

    if has_flex_parent:
        msg_text = (
            f"Fixed-size control '{selector}' ({w_px:.0f}x{h_px:.0f}px) lacks "
            "'flex-shrink: 0'. In Flexbox, items shrink by default "
            "('flex-shrink: 1'), squishing toggles, switches, or icons."
        )
        return create_audit_issue(
            severity="WARNING",
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Flex Fixed Control Shrink Risk",
            message=msg_text,
            recommendation="Add 'flex-shrink: 0;' or 'flex: none;'.",
        )

    return None


def eval_grid_min_content_overflow(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
) -> dict[str, Any] | None:
    """Detecta CSS Grid con tracks '1fr' albergando inputs sin 'minmax(0, 1fr)'."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    disp = props.get("display", "").lower()
    if "grid" not in disp:
        return None

    grid_cols = props.get("grid-template-columns", "").lower()
    if not grid_cols or "fr" not in grid_cols:
        return None

    # Si ya usa minmax(0, 1fr) para todos los tracks fr, es seguro
    if "minmax(0" in grid_cols or "minmax( 0" in grid_cols:
        return None

    # Contar tracks flexibles
    fr_count = len(re.findall(r"(?:\b|\s)1fr(?:\b|\s|$)", grid_cols))
    repeat_match = re.search(r"repeat\(\s*(\d+)\s*,\s*1fr\s*\)", grid_cols)
    if repeat_match:
        fr_count = max(fr_count, int(repeat_match.group(1)))

    if fr_count < 2:
        return None

    rule_classes = set(rule.get("classes", []))
    tokens = set(re.findall(r"[a-z0-9]+", clean_sel))
    for c in rule_classes:
        tokens.update(re.findall(r"[a-z0-9]+", c.lower()))

    # Detectar si es una barra de filtros o aloja inputs/fechas
    is_filter_or_form = bool(tokens & INTRINSIC_INPUT_TAGS)

    # Verificar si los hijos del contenedor en el markup son inputs o fechas
    child_classes = {
        c
        for c, parents in ctx.component_parent_map.items()
        if any(rc in parents for rc in rule_classes)
    }
    has_input_children = is_filter_or_form or any(
        bool(set(re.findall(r"[a-z0-9]+", cc.lower())) & INTRINSIC_INPUT_TAGS)
        for cc in child_classes
    )

    if has_input_children:
        msg_text = (
            f"CSS Grid container '{selector}' specifies '{grid_cols}' with '1fr'. "
            "Because '1fr' has an implicit 'min-width: auto', intrinsic inputs "
            "(dates, text, selects) will expand tracks and cause horizontal blowout."
        )
        return create_audit_issue(
            severity="WARNING",
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Grid Track Min-Content Overflow",
            message=msg_text,
            recommendation=(
                "Use 'minmax(0, 1fr)' for tracks or specify 'min-width: 0;' on inputs."
            ),
        )

    return None
