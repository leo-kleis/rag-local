import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.evaluators.common import create_audit_issue
from rag_local.services.style_audit.models import (
    ATOMIC_UI_KEYWORDS,
    OVERLAY_CONTAINER_KEYWORDS,
    STATE_MODIFIERS,
    is_modal_or_overlay,
)


def eval_z_index_conflict(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext | None = None,
) -> dict[str, Any] | None:
    """Evalúa valores elevados de z-index (>=100) sin aislamiento de contexto."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    z_index = props.get("z-index", "")
    isolation = props.get("isolation", "").lower()
    position = props.get("position", "").lower()

    if z_index:
        resolved_z = ctx.resolve_css_value(z_index) if ctx else z_index
        try:
            z_val = int(re.sub(r"[^\d-]", "", resolved_z))
            valid_pos = ("relative", "absolute", "fixed", "sticky")
            if z_val >= 100 and isolation != "isolate" and position in valid_pos:
                msg_text = (
                    f"Rule '{selector}' sets z-index: {z_val} "
                    "without establishing isolated stacking context."
                )
                return create_audit_issue(
                    severity="INFO",
                    file=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    selector=selector,
                    category="Z-Index Stacking Conflict",
                    message=msg_text,
                    recommendation="Add 'isolation: isolate;'.",
                )
        except ValueError:
            pass

    return None


def eval_z_index_hierarchy(ctx: AuditContext) -> list[dict[str, Any]]:
    """Evalúa la escala global de variables z-index y detecta inversiones de capas."""
    issues: list[dict[str, Any]] = []
    layer_types = {
        "toast": 6,
        "modal": 5,
        "dialog": 5,
        "popover": 4,
        "drawer": 3,
        "sheet": 3,
        "header": 2,
        "tab-bar": 2,
        "sticky": 2,
    }

    parsed_vars: list[tuple[str, int, int, str, str, int]] = []
    for var_name, info in ctx.css_variables.items():
        clean_name = var_name.lower()
        val_str = info.get("value", "")
        try:
            val_num = int(re.sub(r"[^\d-]", "", val_str))
        except ValueError:
            continue

        for layer_name, rank in layer_types.items():
            if layer_name in clean_name:
                parsed_vars.append(
                    (var_name, val_num, rank, layer_name, info["file"], info["line"])
                )
                break

    reported_pairs: set[tuple[str, str]] = set()
    for v1_name, v1_val, v1_rank, v1_layer, v1_file, v1_line in parsed_vars:
        for v2_name, v2_val, v2_rank, v2_layer, v2_file, v2_line in parsed_vars:
            if v1_rank < v2_rank and v1_val > v2_val:
                pair_key = (v1_name, v2_name)
                if pair_key not in reported_pairs:
                    reported_pairs.add(pair_key)
                    l1_cap = v1_layer.capitalize()
                    l2_cap = v2_layer.capitalize()
                    msg_text = (
                        f"Z-index hierarchy inversion: '{v1_name}' "
                        f"({v1_val}, layer {l1_cap}) has higher value than "
                        f"'{v2_name}' ({v2_val}, layer {l2_cap} in "
                        f"{v2_file}:L{v2_line}). Elements of type {l2_cap} "
                        f"must have higher stacking priority than {l1_cap}."
                    )
                    issues.append(
                        create_audit_issue(
                            severity="WARNING",
                            file=v1_file,
                            start_line=v1_line,
                            end_line=v1_line,
                            selector=":root",
                            category="Z-Index Hierarchy Risk",
                            message=msg_text,
                            recommendation=(
                                f"Adjust scale: {l2_cap} must have "
                                f"higher z-index than {l1_cap}."
                            ),
                        )
                    )

    return issues


def eval_stacking_context_trap(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
) -> dict[str, Any] | None:
    """Detecta modales/drawers atrapados en contextos de apilamiento aislados."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    pos = props.get("position", "").lower()
    clean_sel = selector.lower()
    rule_classes = rule.get("classes", [])

    # 0. Excluir elementos montados vía DOM portal en document.body o root
    for c in rule_classes:
        if c in ctx.portal_mounted_classes:
            return None
    if any(pc in clean_sel for pc in ctx.portal_mounted_classes):
        return None

    # 1. Omitir pseudoelementos (::before, ::after)
    if (
        "::before" in clean_sel
        or "::after" in clean_sel
        or ":before" in clean_sel
        or ":after" in clean_sel
    ):
        return None

    # 2. Solo evaluar elementos flotantes / fijos
    is_explicit_fixed = pos in ("fixed", "sticky")
    if not is_explicit_fixed:
        return None

    # 3. Omitir selectores descendientes (ej. .stream-info .ui-tooltip-wrapper)
    # Solo se evalúa la regla principal de la clase raíz
    if (
        " " in selector or ">" in selector or "+" in selector or "~" in selector
    ) and not any(
        clean_sel.endswith(k)
        for k in (".ui-tooltip-wrapper", ".ui-tooltip", ".conn-popover")
    ):
        return None

    tokens = set(re.findall(r"[a-z0-9]+", clean_sel))
    for c in rule_classes:
        tokens.update(re.findall(r"[a-z0-9]+", c.lower()))

    # Excluir partes atómicas de micro-UI interna
    if bool(tokens & ATOMIC_UI_KEYWORDS):
        return None

    for c in rule_classes:
        parents = ctx.component_parent_map.get(c, set())
        for p in parents:
            if p.startswith("[COMP]") or p.lower() in STATE_MODIFIERS:
                continue

            # Si el ancestro es el propio contenedor (ej. history-drawer)
            if c.startswith(p) or p.startswith(c.split("-")[0]):
                continue

            # Si el ancestro es backdrop u overlay del componente, no es trampa
            if any(kw in p.lower() for kw in OVERLAY_CONTAINER_KEYWORDS):
                continue

            if p in ctx.stacking_context_classes:
                trap_info = ctx.stacking_context_classes[p]
                trap_file = trap_info.get("file", "")
                trap_sel = trap_info.get("selector", "")
                # No reportar si el trap es su propio contenedor
                if trap_file == rel_path and (
                    trap_sel in selector or selector in trap_sel
                ):
                    continue

                trigger_desc = trap_info.get("trigger", "isolation")
                trap_line = trap_info.get("line", 1)

                msg_text = (
                    f"Fixed/floating element '{selector}' is trapped inside "
                    f"ancestor '.{p}', which establishes an isolated stacking context "
                    f"({trigger_desc} in {trap_file}:L{trap_line}). "
                    "This prevents proper layering above global UI components "
                    "like headers and navigation bars."
                )
                return create_audit_issue(
                    severity="CRITICAL",
                    file=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    selector=selector,
                    category="Stacking Context Trap",
                    message=msg_text,
                    recommendation=(
                        f"Mount '{selector}' outside container '.{p}' at "
                        "application root or use a DOM portal."
                    ),
                )

    return None


def eval_tooltip_viewport_overflow(
    rule: dict[str, Any],
    rel_path: str,
) -> dict[str, Any] | None:
    """Detecta tooltips centrados con white-space: nowrap que desbordan el viewport."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    is_tooltip = any(kw in clean_sel for kw in ("tooltip", "popover", "hint", "flyout"))
    left = props.get("left", "").lower()
    transform = props.get("transform", "").lower()
    white_space = props.get("white-space", "").lower()
    max_w = props.get("max-width", "").lower()

    is_centered_floating = is_tooltip or (
        left == "50%" and "translatex(-50%)" in transform
    )
    if is_centered_floating and "nowrap" in white_space and not max_w:
        msg_text = (
            f"Floating element '{selector}' uses horizontal centering "
            "('left: 50%; transform: translateX(-50%); white-space: nowrap;') "
            "without restricting 'max-width'. Near viewport boundaries or on narrow "
            "screens, it will overflow off-screen."
        )
        return create_audit_issue(
            severity="WARNING",
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Tooltip Viewport Overflow Risk",
            message=msg_text,
            recommendation=(
                "Add 'max-width: min(280px, calc(100vw - 24px)); "
                "white-space: normal; word-break: break-word;'."
            ),
        )
    return None


def eval_modal_landscape_overflow(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext | None = None,
) -> dict[str, Any] | None:
    """Detecta modales centrados verticalmente que carecen de scroll en landscape."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    is_backdrop = is_modal_or_overlay(props) or any(
        kw in clean_sel
        for kw in ("backdrop", "modal-backdrop", "modal-overlay", "dialog-overlay")
    )
    if not is_backdrop:
        is_modal_card = any(
            kw in clean_sel
            for kw in (
                "modal-card",
                "modal-dialog",
                "dialog-card",
                "modal-box",
                "confirm-card",
            )
        )
        if is_modal_card and not rule.get("media_query"):
            padding = props.get("padding", "").lower()
            px_vals = [
                float(m)
                for m in re.findall(r"([\d.]+)\s*px", padding)
                if float(m) >= 24.0
            ]
            if px_vals:
                has_landscape = False
                if ctx:
                    for mq_item in ctx.project_media_queries:
                        mq_q = mq_item.get("query", "").lower()
                        if "max-height" in mq_q:
                            mq_sel = mq_item.get("selector", "").lower()
                            if selector.lower() in mq_sel or any(
                                c.lower() in mq_sel for c in rule.get("classes", [])
                            ):
                                has_landscape = True
                                break
                if not has_landscape:
                    msg_text = (
                        f"Modal card '{selector}' specifies high padding ('{padding}') "
                        "without compact adaptation for landscape viewports "
                        "('@media (max-height: 480px)'). In short viewports, "
                        "large padding forces unnecessary vertical scrolling."
                    )
                    return create_audit_issue(
                        severity="INFO",
                        file=rel_path,
                        start_line=start_line,
                        end_line=end_line,
                        selector=selector,
                        category="Modal Landscape Density",
                        message=msg_text,
                        recommendation=(
                            f"Add '@media (max-height: 480px) {{ {selector} "
                            "{ padding: 14px 18px; } }}'."
                        ),
                    )
        return None

    # Excluir micro-UI interna del modal (botones, iconos, badges, spinners, labels)
    if any(
        kw in clean_sel
        for kw in (
            "loading",
            "spinner",
            "loader",
            "badge",
            "btn",
            "icon",
            "arrow",
            "close",
            "label",
            "title",
            "alert",
        )
    ):
        return None

    disp = props.get("display", "").lower()
    align = props.get("align-items", "").lower()
    justify = props.get("justify-content", "").lower()
    overflow_y = props.get("overflow-y", "").lower()
    overflow = props.get("overflow", "").lower()

    is_centered_flex = "flex" in disp and (align == "center" or justify == "center")
    has_vertical_scroll = any(
        kw in overflow_y or kw in overflow for kw in ("auto", "scroll")
    )

    if is_centered_flex and not has_vertical_scroll:
        msg_text = (
            f"Modal backdrop/overlay '{selector}' vertically centers dialog "
            "but lacks 'overflow-y: auto'. On short screens (landscape or <= 480px), "
            "the modal will overflow vertically making action buttons and "
            "header inaccessible."
        )
        return create_audit_issue(
            severity="WARNING",
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Modal Landscape Overflow Risk",
            message=msg_text,
            recommendation=(
                "Add 'overflow-y: auto;' and 'padding: 16px;' to backdrop, and "
                "define 'max-height: calc(100dvh - 32px); overflow-y: auto;' "
                "on modal dialog."
            ),
        )

    return None


def eval_absolute_overflow_clipping_trap(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
) -> dict[str, Any] | None:
    """Detecta elementos absolute con z-index alto dentro de ancestros con overflow."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    position = props.get("position", "").lower()
    if position != "absolute":
        return None

    z_index = props.get("z-index", "")
    if not z_index:
        return None

    resolved_z = ctx.resolve_css_value(z_index)
    try:
        z_val = int(re.sub(r"[^\d-]", "", resolved_z))
    except ValueError:
        z_val = 0

    # Popovers/tooltips con z-index significativo (>= 50 o variable de tooltip/popover)
    is_elevated_layer = z_val >= 50 or any(
        k in z_index.lower() for k in ("popover", "tooltip", "dropdown", "modal")
    )
    if not is_elevated_layer:
        return None

    rule_classes = set(rule.get("classes", []))
    # Si la clase está montada mediante portal DOM en document.body, no hay recorte
    if bool(rule_classes & ctx.portal_mounted_classes):
        return None

    # Excluir pseudoelementos o elementos de backdrop
    if is_modal_or_overlay(props) or any(
        kw in clean_sel for kw in ("backdrop", "overlay", "::before", "::after")
    ):
        return None

    # Verificar si el elemento tiene tokens de popover/tooltip/dropdown
    tokens = set(re.findall(r"[a-z0-9]+", clean_sel))
    for c in rule_classes:
        tokens.update(re.findall(r"[a-z0-9]+", c.lower()))

    is_floating_widget = bool(
        tokens
        & {
            "tooltip",
            "popover",
            "dropdown",
            "menu",
            "flyout",
            "hovercard",
            "preview",
        }
    )
    if not is_floating_widget:
        return None

    # Buscar si algún ancestro en el mapa de componentes tiene mitigación de overflow
    parent_classes = set()
    for c in rule_classes:
        parent_classes.update(ctx.component_parent_map.get(c, set()))

    clipping_parents = parent_classes & ctx.project_mitigated_classes
    if clipping_parents:
        parent_name = next(iter(clipping_parents))
        msg_text = (
            f"Absolute floating widget '{selector}' (z-index: {z_index}) is placed "
            f"inside ancestor '.{parent_name}' with 'overflow: hidden/auto/scroll'. "
            "Without a DOM portal to document.body, the floating element will be "
            "clipped by the parent container."
        )
        return create_audit_issue(
            severity="WARNING",
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Absolute Overflow Clipping Trap",
            message=msg_text,
            recommendation=(
                "Mount the floating element via a DOM portal attached to "
                "'document.body' or use CSS Anchor Positioning."
            ),
        )

    return None
