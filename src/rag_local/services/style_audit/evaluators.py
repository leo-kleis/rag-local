import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.models import (
    COLLECTION_CLASS_KEYWORDS,
    NATIVE_TEXT_TAGS,
    NON_TEXT_ELEMENTS,
    PROSE_TEXT_KEYWORDS,
    extract_terminal_tag,
    is_atomic_or_micro_ui,
    is_break_protected,
    is_modal_or_overlay,
)


def _analyze_rule_dynamics(
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
    is_column = "column" in flex_direction
    if is_column:
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
    ) = _analyze_rule_dynamics(rule_classes, ctx)

    # Excluir elementos atómicos / micro-UI si no tienen texto dinámico confirmado
    if is_atomic_or_micro_ui(clean_sel, rule_classes) and not rule_has_dynamic:
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
        # Contenedores flex puros solo alertan con señal de texto confirmada
        if (
            not is_flex_child
            and not rule_has_dynamic
            and not (has_text_signal and has_markup_data)
            and not has_text_signal
        ):
            return None

        file_mitigated = any(
            m_sel != selector
            and (m_sel in selector or any(part in selector for part in m_sel.split()))
            for m_sel in mitigating_selectors
        )

        parent_mitigation_class = ""
        for cname in rule_classes:
            parents = ctx.component_parent_map.get(cname, set())
            match_parent = next(
                (p for p in parents if p in ctx.project_mitigated_classes),
                None,
            )
            if match_parent:
                parent_mitigation_class = match_parent
                break

        is_mitigated = file_mitigated or bool(parent_mitigation_class)

        if is_mitigated:
            sev = "INFO"
        elif rule_has_dynamic:
            sev = "CRITICAL"
        elif rule_is_mixed_dynamic or (not has_markup_data and has_text_signal):
            sev = "WARNING"
        else:
            sev = "WARNING"

        if parent_mitigation_class:
            mit_suffix = f" [MITIGATED: Protegido por .{parent_mitigation_class}]"
        elif file_mitigated:
            mit_suffix = " (mitigado por contenedor con overflow)"
        elif rule_is_mixed_dynamic:
            mit_suffix = (
                f" (usado en múltiples contextos "
                f"({rule_mixed_files_count} archivos), verificar manualmente)"
            )
        else:
            mit_suffix = ""

        msg_text = (
            f"El contenedor/hijo flex '{selector}' "
            f"(display: {disp or 'flex-item'}) "
            "carece de 'min-width: 0' o 'overflow: hidden'"
            f"{mit_suffix}."
        )
        return {
            "severity": sev,
            "file": rel_path,
            "start_line": start_line,
            "end_line": end_line,
            "selector": selector,
            "category": "Flexbox/Grid Overflow Risk",
            "message": msg_text,
            "recommendation": "Agregar 'min-width: 0;' o 'overflow: hidden;'.",
        }

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
    has_nowrap_handling = "nowrap" in white_space or "ellipsis" in text_overflow

    if (
        has_fixed_size
        or has_overflow_control
        or has_fixed_distribution
        or has_nowrap_handling
    ):
        return None

    rule_classes = rule.get("classes", [])
    (_, _, _, _, rule_is_collection) = _analyze_rule_dynamics(rule_classes, ctx)

    # Excluir componentes atómicos, toolbars, barras de input, steppers
    if is_atomic_or_micro_ui(clean_sel, rule_classes):
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
        rule_is_collection or has_collection_class or has_combinator_signal
    )

    if has_multiple_items_signal:
        file_mitigated = any(
            m_sel != selector
            and (m_sel in selector or any(part in selector for part in m_sel.split()))
            for m_sel in mitigating_selectors
        )
        parent_mitigation_class = ""
        for cname in rule_classes:
            parents = ctx.component_parent_map.get(cname, set())
            match_parent = next(
                (p for p in parents if p in ctx.project_mitigated_classes),
                None,
            )
            if match_parent:
                parent_mitigation_class = match_parent
                break

        is_mitigated = file_mitigated or bool(parent_mitigation_class)
        has_strong_collection_signal = (
            rule_is_collection or "+" in selector or "~" in selector
        )
        if is_mitigated:
            sev = "INFO"
        elif has_strong_collection_signal:
            sev = "WARNING"
        else:
            sev = "INFO"

        mit_suffix = (
            f" [MITIGATED: Protegido por .{parent_mitigation_class}]"
            if parent_mitigation_class
            else (" (mitigado por contenedor con overflow)" if file_mitigated else "")
        )

        msg_text = (
            f"El contenedor flex horizontal con múltiples hijos '{selector}' "
            f"(display: {disp}) carece de 'flex-wrap: wrap' o "
            f"'overflow-x: auto', lo que puede provocar "
            f"desbordamiento de hijos{mit_suffix}."
        )
        return {
            "severity": sev,
            "file": rel_path,
            "start_line": start_line,
            "end_line": end_line,
            "selector": selector,
            "category": "Flex Wrap Overflow Risk",
            "message": msg_text,
            "recommendation": "Agregar 'flex-wrap: wrap;' o 'overflow-x: auto;'.",
        }

    return None


def eval_breakpoint_overflow(
    rule: dict[str, Any],
    rel_path: str,
) -> dict[str, Any] | None:
    """Evalúa reglas CSS en media queries cuyo ancho fijo satura el breakpoint."""
    media_query = rule.get("media_query", "")
    if not media_query or "max-width" not in media_query.lower():
        return None

    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    min_w = props.get("min-width", "").lower()
    width = props.get("width", "").lower()

    mw_match = re.search(
        r"max-width\s*:\s*([\d.]+)\s*(px|rem|em)",
        media_query,
        re.IGNORECASE,
    )
    if not mw_match:
        return None

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
                px_equiv = w_val * 16.0 if w_unit in ("rem", "em") else w_val
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
        return {
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

    return None


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
    ) = _analyze_rule_dynamics(rule_classes, ctx)

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
            f"El contenedor de texto '{selector}' no especifica reglas "
            "de rotura ('overflow-wrap: anywhere' o "
            "'overflow-wrap: break-word')."
        )
        text_break_sev = "WARNING" if has_markup_data else "INFO"
        return {
            "severity": text_break_sev,
            "file": rel_path,
            "start_line": start_line,
            "end_line": end_line,
            "selector": selector,
            "category": "Text Break Risk",
            "message": msg_text,
            "recommendation": (
                "Agregar 'overflow-wrap: anywhere;' o 'overflow-wrap: break-word;'."
            ),
        }

    return None


def eval_fixed_width_risk(
    rule: dict[str, Any],
    rel_path: str,
) -> dict[str, Any] | None:
    """Evalúa anchos fijos estrictos en px (>400px) sin max-width."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    width = props.get("width", "").lower()
    max_w = props.get("max-width", "").lower()

    if width and width.endswith("px") and not max_w:
        try:
            px_val = int(re.sub(r"[^\d]", "", width))
            if px_val > 400:
                msg_text = (
                    f"La regla '{selector}' tiene un ancho fijo estricto "
                    f"de '{width}' sin 'max-width: 100%'."
                )
                return {
                    "severity": "WARNING",
                    "file": rel_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "selector": selector,
                    "category": "Fixed Width Responsive Risk",
                    "message": msg_text,
                    "recommendation": "Usar 'max-width: 100%;' o unidades relativas.",
                }
        except ValueError:
            pass

    return None


def eval_z_index_conflict(
    rule: dict[str, Any],
    rel_path: str,
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
        try:
            z_val = int(re.sub(r"[^\d-]", "", z_index))
            valid_pos = ("relative", "absolute", "fixed", "sticky")
            if z_val >= 100 and isolation != "isolate" and position in valid_pos:
                msg_text = (
                    f"La regla '{selector}' asigna z-index: {z_val} "
                    "sin establecer contexto de apilamiento aislado."
                )
                return {
                    "severity": "INFO",
                    "file": rel_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "selector": selector,
                    "category": "Z-Index Stacking Conflict",
                    "message": msg_text,
                    "recommendation": "Agregar 'isolation: isolate;'.",
                }
        except ValueError:
            pass

    return None


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

    is_fixed_layer = pos in ("fixed", "sticky") or any(
        kw in clean_sel
        for kw in ("drawer", "modal-backdrop", "modal", "toast", "popover")
    )
    if not is_fixed_layer:
        return None

    rule_classes = rule.get("classes", [])
    for c in rule_classes:
        parents = ctx.component_parent_map.get(c, set())
        for p in parents:
            if p in ctx.stacking_context_classes:
                trap_info = ctx.stacking_context_classes[p]
                trigger_desc = trap_info.get("trigger", "aislamiento")
                trap_file = trap_info.get("file", "")
                trap_line = trap_info.get("line", 1)

                msg_text = (
                    f"El elemento fijo/flotante '{selector}' está contenido en "
                    f"el ancestro '.{p}', el cual establece un contexto de apilamiento "
                    f"aislado ({trigger_desc} en {trap_file}:L{trap_line}). "
                    "Esto atrapa al elemento e impide que se superponga correctamente "
                    "a componentes globales como headers y barras de navegación."
                )
                return {
                    "severity": "CRITICAL",
                    "file": rel_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "selector": selector,
                    "category": "Stacking Context Trap",
                    "message": msg_text,
                    "recommendation": (
                        f"Montar '{selector}' fuera del contenedor '.{p}' en la "
                        "raíz de la aplicación o utilizar un Portal DOM."
                    ),
                }

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
                        f"Inversión en jerarquía de z-index: '{v1_name}' "
                        f"({v1_val}, capa {l1_cap}) tiene un valor mayor que "
                        f"'{v2_name}' ({v2_val}, capa {l2_cap} en "
                        f"{v2_file}:L{v2_line}). Los elementos de tipo {l2_cap} "
                        f"deben tener mayor prioridad de apilamiento que {l1_cap}."
                    )
                    issues.append(
                        {
                            "severity": "WARNING",
                            "file": v1_file,
                            "start_line": v1_line,
                            "end_line": v1_line,
                            "selector": ":root",
                            "category": "Z-Index Hierarchy Risk",
                            "message": msg_text,
                            "recommendation": (
                                f"Ajustar escala: {l2_cap} debe tener "
                                f"un z-index superior a {l1_cap}."
                            ),
                        }
                    )

    return issues


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
        f"El contenedor scrollable '{selector}' dentro de un layout Flex vertical "
        "carece de 'min-height: 0'. Por defecto en Flexbox, min-height es 'auto', "
        "lo que impide que el contenedor se encoja para activar el scroll vertical."
    )
    return {
        "severity": "WARNING",
        "file": rel_path,
        "start_line": start_line,
        "end_line": end_line,
        "selector": selector,
        "category": "Flex Column Scroll Risk",
        "message": msg_text,
        "recommendation": ("Agregar 'min-height: 0;' en el contenedor scrollable."),
    }


def eval_breakpoint_consistency(ctx: AuditContext) -> list[dict[str, Any]]:
    """Detecta discrepancias de media queries a nivel de proyecto."""
    issues: list[dict[str, Any]] = []
    bp_records: list[tuple[float, str, int, str]] = []

    for item in ctx.project_media_queries:
        mq = item.get("query", "")
        m = re.search(r"max-width\s*:\s*([\d.]+)\s*(px|rem|em)", mq, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            unit = m.group(2).lower()
            px_equiv = val * 16.0 if unit in ("rem", "em") else val
            bp_records.append((px_equiv, item["file"], item["line"], mq.strip()))

    reported_file_pairs: set[tuple[str, str, float, float]] = set()
    for px1, f1, l1, q1 in bp_records:
        for px2, f2, l2, q2 in bp_records:
            if f1 != f2 and 0 < abs(px1 - px2) <= 30:
                file_pair = (
                    min(f1, f2),
                    max(f1, f2),
                    min(px1, px2),
                    max(px1, px2),
                )
                if file_pair not in reported_file_pairs:
                    reported_file_pairs.add(file_pair)
                    diff = abs(px1 - px2)
                    max_bp = max(px1, px2)
                    msg_text = (
                        f"Inconsistencia en breakpoints responsivos: '{q1}' "
                        f"({px1:.0f}px en {f1}:L{l1}) vs '{q2}' "
                        f"({px2:.0f}px en {f2}:L{l2}). "
                        f"La diferencia de {diff:.0f}px puede generar comportamientos "
                        "híbridos no deseados entre ambas resoluciones."
                    )
                    issues.append(
                        {
                            "severity": "INFO",
                            "file": f1,
                            "start_line": l1,
                            "end_line": l1,
                            "selector": "@media",
                            "category": "Breakpoint Inconsistency",
                            "message": msg_text,
                            "recommendation": (
                                "Estandarizar media queries a un único breakpoint "
                                f"oficial (ej. {max_bp:.0f}px)."
                            ),
                        }
                    )

    return issues


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
            f"El backdrop/overlay modal '{selector}' centra verticalmente el "
            "diálogo pero carece de 'overflow-y: auto'. En pantallas de baja altura "
            "(landscape o <= 480px), el modal desbordará verticalmente haciendo "
            "inaccesibles los botones de acción y la cabecera."
        )
        return {
            "severity": "WARNING",
            "file": rel_path,
            "start_line": start_line,
            "end_line": end_line,
            "selector": selector,
            "category": "Modal Landscape Overflow Risk",
            "message": msg_text,
            "recommendation": (
                "Agregar 'overflow-y: auto;' y 'padding: 16px;' en el backdrop, y "
                "definir 'max-height: calc(100dvh - 32px); overflow-y: auto;' "
                "en el diálogo modal."
            ),
        }

    return None
