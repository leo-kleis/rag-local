import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.evaluators.common import create_audit_issue
from rag_local.services.style_audit.models import (
    ATOMIC_UI_KEYWORDS,
    INTERNAL_OVERLAY_PARTS,
    OVERLAY_CONTAINER_KEYWORDS,
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

    tokens = set(re.findall(r"[a-z0-9]+", clean_sel))
    for c in rule_classes:
        tokens.update(re.findall(r"[a-z0-9]+", c.lower()))

    # Excluir partes estructurales internas de modales/toasts/drawers/micro-UI
    if bool(tokens & (INTERNAL_OVERLAY_PARTS | ATOMIC_UI_KEYWORDS)):
        return None

    is_explicit_fixed = pos in ("fixed", "sticky")
    is_floating_root = any(
        re.search(rf"(?:^|[ ._-]){kw}(?:$|[ ._-])", clean_sel)
        for kw in (
            "popover",
            "dropdown-menu",
            "dropdown-list",
            "tooltip",
            "flyout",
            "modal-backdrop",
            "drawer",
        )
    )
    if not (is_explicit_fixed or is_floating_root):
        return None

    for c in rule_classes:
        parents = ctx.component_parent_map.get(c, set())
        for p in parents:
            # Si el ancestro es backdrop u overlay del componente, no es trampa
            if any(kw in p.lower() for kw in OVERLAY_CONTAINER_KEYWORDS):
                continue

            if p in ctx.stacking_context_classes:
                trap_info = ctx.stacking_context_classes[p]
                trigger_desc = trap_info.get("trigger", "isolation")
                trap_file = trap_info.get("file", "")
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
