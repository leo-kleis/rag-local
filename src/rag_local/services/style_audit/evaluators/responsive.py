import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.evaluators.common import (
    create_audit_issue,
    parse_css_dimension_px,
)
from rag_local.services.style_audit.models import is_modal_or_overlay

_RE_MAX_WIDTH = re.compile(
    r"max-width\s*:\s*([\d.]+)\s*(px|rem|em)",
    re.IGNORECASE,
)
_RE_MAX_HEIGHT = re.compile(
    r"max-height\s*:\s*([\d.]+)\s*(px|rem|em)",
    re.IGNORECASE,
)


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

    mw_match = _RE_MAX_WIDTH.search(media_query)
    if not mw_match:
        return None

    bp_val = float(mw_match.group(1))
    bp_unit = mw_match.group(2).lower()
    bp_px = bp_val * 16.0 if bp_unit in ("rem", "em") else bp_val

    declared_width_px = 0.0
    for w_prop in (min_w, width):
        parsed = parse_css_dimension_px(w_prop)
        if parsed is not None:
            declared_width_px = max(declared_width_px, parsed)

    grid_cols = props.get("grid-template-columns", "").lower()
    if grid_cols:
        px_cols = re.findall(r"([\d.]+)\s*px", grid_cols)
        if px_cols:
            grid_sum = sum(float(c) for c in px_cols)
            declared_width_px = max(declared_width_px, grid_sum)

        minmax_matches = re.findall(r"minmax\(\s*([\d.]+)\s*px", grid_cols)
        if minmax_matches:
            for mm_px_str in minmax_matches:
                mm_px = float(mm_px_str)
                if mm_px >= 280 and bp_px <= mm_px:
                    declared_width_px = max(declared_width_px, mm_px)

    if declared_width_px >= bp_px and declared_width_px > 0:
        msg_text = (
            f"Rule '{selector}' defines fixed/minimum width of "
            f"~{declared_width_px:.0f}px exceeding breakpoint "
            f"'{media_query.strip()}' ({bp_px:.0f}px)."
        )
        return create_audit_issue(
            severity="WARNING",
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Breakpoint Width Overflow",
            message=msg_text,
            recommendation=(
                "Use relative dimensions (%, fr, vw) or "
                "reduce rigid widths in this breakpoint."
            ),
        )

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
                    f"Rule '{selector}' has rigid fixed width "
                    f"of '{width}' without 'max-width: 100%'."
                )
                return create_audit_issue(
                    severity="WARNING",
                    file=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    selector=selector,
                    category="Fixed Width Responsive Risk",
                    message=msg_text,
                    recommendation="Use 'max-width: 100%;' or relative units.",
                )
        except ValueError:
            pass

    return None


def eval_rigid_height_landscape_risk(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext,
) -> dict[str, Any] | None:
    """Detecta alturas mínimas o fijas rígidas (>=380px) sin adaptación landscape."""
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})
    clean_sel = selector.lower()

    if is_modal_or_overlay(props) or any(
        kw in clean_sel for kw in ("backdrop", "modal", "overlay", "drawer")
    ):
        return None

    if rule.get("media_query"):
        return None

    min_h = props.get("min-height", "").lower().strip()
    height = props.get("height", "").lower().strip()

    fixed_px = 0.0
    for h_prop in (min_h, height):
        if h_prop.endswith("px"):
            try:
                px = float(re.sub(r"[^\d.]", "", h_prop))
                fixed_px = max(fixed_px, px)
            except ValueError:
                pass

    if fixed_px >= 380:
        rule_classes = set(rule.get("classes", []))
        has_landscape_override = False
        for mq_item in ctx.project_media_queries:
            mq_text = mq_item.get("query", "").lower()
            if "max-height" in mq_text:
                m = re.search(r"max-height\s*:\s*([\d.]+)", mq_text)
                if m and float(m.group(1)) <= 500:
                    mq_sel = mq_item.get("selector", "").lower()
                    if selector.lower() in mq_sel or any(
                        c.lower() in mq_sel for c in rule_classes
                    ):
                        has_landscape_override = True
                        break

        if not has_landscape_override:
            msg_text = (
                f"Container '{selector}' defines rigid fixed/min height of "
                f"{fixed_px:.0f}px without reduced height media query adaptation "
                "('@media (max-height: 480px)'). "
                "Will overflow viewport in mobile landscape."
            )
            rec_text = (
                f"Add '@media (max-height: 480px) {{ {selector} "
                "{ min-height: auto; } }}'."
            )
            return create_audit_issue(
                severity="CRITICAL",
                file=rel_path,
                start_line=start_line,
                end_line=end_line,
                selector=selector,
                category="Rigid Height Landscape Risk",
                message=msg_text,
                recommendation=rec_text,
            )
    return None


def eval_2d_breakpoint_collision(ctx: AuditContext) -> list[dict[str, Any]]:
    """Detecta solapamiento ortogonal entre media queries de ancho y alto."""
    issues: list[dict[str, Any]] = []
    width_mqs: list[dict[str, Any]] = []
    height_mqs: list[dict[str, Any]] = []

    for item in ctx.project_media_queries:
        mq = item.get("query", "").lower()
        if "max-width" in mq:
            m_w = _RE_MAX_WIDTH.search(mq)
            if m_w:
                val = float(m_w.group(1)) * (
                    16.0 if m_w.group(2).lower() in ("rem", "em") else 1.0
                )
                if val <= 650:
                    width_mqs.append({**item, "val": val})
        if "max-height" in mq:
            m_h = _RE_MAX_HEIGHT.search(mq)
            if m_h:
                val = float(m_h.group(1)) * (
                    16.0 if m_h.group(2).lower() in ("rem", "em") else 1.0
                )
                if val <= 500:
                    height_mqs.append({**item, "val": val})

    reported_pairs: set[tuple[str, str]] = set()
    for w_item in width_mqs:
        q_w = w_item["query"]
        f_w = w_item["file"]
        l_w = w_item["line"]
        if "min-height" in q_w.lower() or "orientation" in q_w.lower():
            continue

        for h_item in height_mqs:
            q_h = h_item["query"]
            f_h = h_item["file"]
            l_h = h_item["line"]
            if "min-width" in q_h.lower() or "orientation" in q_h.lower():
                continue

            # Si están en archivos distintos, solo colisionan si alguno es layout global
            is_same_file = f_w == f_h
            is_global_w = any(
                kw in f_w.lower() for kw in ("responsive", "global", "layout", "app")
            )
            is_global_h = any(
                kw in f_h.lower() for kw in ("responsive", "global", "layout", "app")
            )
            if not is_same_file and not (is_global_w or is_global_h):
                continue

            pair_key = (q_w.strip(), q_h.strip())
            if pair_key not in reported_pairs:
                reported_pairs.add(pair_key)
                msg_text = (
                    f"Orthogonal media query overlap: '{q_w}' ({f_w}:L{l_w}) "
                    f"and '{q_h}' ({f_h}:L{l_h}) trigger simultaneously on mobile "
                    "devices rotated to landscape without mutual exclusion."
                )
                rec_text = (
                    "Constrain mobile query to portrait orientation: "
                    f"'{q_w.strip()} and (min-height: {h_item['val'] + 1:.0f}px)'."
                )
                issues.append(
                    create_audit_issue(
                        severity="WARNING",
                        file=f_w,
                        start_line=l_w,
                        end_line=l_w,
                        selector="@media",
                        category="2D Breakpoint Collision Risk",
                        message=msg_text,
                        recommendation=rec_text,
                    )
                )
    return issues


def eval_breakpoint_consistency(ctx: AuditContext) -> list[dict[str, Any]]:
    """Detecta discrepancias de media queries a nivel de proyecto."""
    issues: list[dict[str, Any]] = []
    bp_records: list[tuple[float, str, int, str]] = []

    for item in ctx.project_media_queries:
        mq = item.get("query", "")
        m = _RE_MAX_WIDTH.search(mq)
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
                        f"Responsive breakpoint inconsistency: '{q1}' "
                        f"({px1:.0f}px in {f1}:L{l1}) vs '{q2}' "
                        f"({px2:.0f}px in {f2}:L{l2}). The {diff:.0f}px difference "
                        "may cause conflicting hybrid layout states."
                    )
                    issues.append(
                        create_audit_issue(
                            severity="INFO",
                            file=f1,
                            start_line=l1,
                            end_line=l1,
                            selector="@media",
                            category="Breakpoint Inconsistency",
                            message=msg_text,
                            recommendation=(
                                "Standardize media queries to a single official "
                                f"breakpoint (e.g. {max_bp:.0f}px)."
                            ),
                        )
                    )

    return issues
