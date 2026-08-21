import re
from typing import Any

from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.evaluators.common import create_audit_issue


def eval_aspect_ratio_height_risk(
    rule: dict[str, Any],
    rel_path: str,
    ctx: AuditContext | None = None,
) -> dict[str, Any] | None:
    """Detecta elementos con aspect-ratio y ancho expansivo que carecen de max-height.

    En contenedores con límite vertical o ventanas de poca altura, el aspect-ratio
    calcula alturas excesivas que causan desbordamiento o recorte vertical.
    """
    selector = rule.get("selector", "").strip()
    start_line = rule.get("start_line", 1)
    end_line = rule.get("end_line", 1)
    props = rule.get("properties", {})

    aspect_ratio = props.get("aspect-ratio", "").strip()
    if not aspect_ratio or aspect_ratio == "auto":
        return None

    width = props.get("width", "").lower()
    max_w = props.get("max-width", "").lower()
    max_h = props.get("max-height", "").lower()
    flex_prop = props.get("flex", "").lower()

    # Si ya restringe max-height, no hay riesgo de desbordamiento
    if max_h and (max_h in ("100%", "100vh", "100dvh", "100cqh") or "calc(" in max_h):
        return None

    has_large_width = (
        width in ("100%", "100vw", "auto")
        or "100%" in width
        or bool(flex_prop and flex_prop != "none")
    )
    if not has_large_width and max_w.endswith("px"):
        try:
            px = float(re.sub(r"[^\d.]", "", max_w))
            if px >= 400:
                has_large_width = True
        except ValueError:
            pass

    if has_large_width and not max_h:
        msg_text = (
            f"Element '{selector}' defines 'aspect-ratio: {aspect_ratio}' with wide "
            f"dimensions ('width: {width or 'auto'}', 'max-width: {max_w or 'none'}') "
            "without 'max-height: 100%'. In height-constrained viewports or landscape "
            "windows, the calculated height will overflow parent container."
        )
        return create_audit_issue(
            severity="WARNING",
            file=rel_path,
            start_line=start_line,
            end_line=end_line,
            selector=selector,
            category="Aspect Ratio Height Overflow Risk",
            message=msg_text,
            recommendation=(
                "Add 'max-height: 100%; min-height: 0; width: auto;' to ensure "
                "the aspect-ratio respects vertical viewport boundaries."
            ),
        )

    return None
