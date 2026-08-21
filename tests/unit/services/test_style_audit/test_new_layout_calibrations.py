import json
from typing import Any

from rag_local.parsers.html import extract_html_class_parents
from rag_local.services.style_audit.context import AuditContext
from rag_local.services.style_audit.evaluators import (
    eval_2d_breakpoint_collision,
    eval_flex_wrap_risk,
    eval_invalid_css_shorthand,
    eval_rigid_height_landscape_risk,
    eval_tooltip_viewport_overflow,
    eval_z_index_conflict,
)


def test_balanced_html_stack_parser():
    html_sample = """
    <div class="app-header">
      <div class="stream-widget">
        <span class="stream-title">Title</span>
      </div>
      <div class="conn-popover">Popover</div>
    </div>
    <div class="tab-bar">
      <button class="tab-btn">Tab</button>
    </div>
    """
    res_str = extract_html_class_parents(html_sample)
    data = json.loads(res_str)

    # tab-btn should ONLY have tab-bar as parent, NOT app-header or conn-popover
    assert "tab-bar" in data["tab-btn"]["parents"]
    assert "app-header" not in data["tab-btn"]["parents"]
    assert "conn-popover" not in data["tab-btn"]["parents"]

    # stream-title should have stream-widget and app-header
    assert "stream-widget" in data["stream-title"]["parents"]
    assert "app-header" in data["stream-title"]["parents"]


def test_html_parser_extracts_inline_rules():
    sample = """
    <div class="history-header">
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="history-header-name">Leo</span>
      </div>
    </div>
    """
    res_str = extract_html_class_parents(sample)
    data = json.loads(res_str)

    assert "__inline_rules__" in data
    inline_rules = data["__inline_rules__"]
    assert len(inline_rules) == 1
    assert inline_rules[0]["properties"]["display"] == "flex"
    assert "history-header" in inline_rules[0]["parents"]


def test_css_variable_resolution():
    ctx = AuditContext()
    ctx.css_variables = {
        "--border": {"value": "rgba(169, 112, 255, 0.18)"},
        "--z-header": {"value": "110"},
        "--z-popover": {"value": "520"},
        "--theme-border": {"value": "1px solid var(--border)"},
    }

    assert ctx.resolve_css_value("var(--z-header)") == "110"
    assert ctx.resolve_css_value("var(--border)") == "rgba(169, 112, 255, 0.18)"
    assert (
        ctx.resolve_css_value("var(--theme-border)")
        == "1px solid rgba(169, 112, 255, 0.18)"
    )


def test_invalid_css_shorthand_detected():
    ctx = AuditContext()
    ctx.css_variables = {
        "--border": {"value": "rgba(169, 112, 255, 0.18)"},
    }

    rule_invalid = {
        "selector": ".form-control",
        "start_line": 52,
        "end_line": 52,
        "properties": {
            "border": "var(--border)",
        },
    }
    issue = eval_invalid_css_shorthand(rule_invalid, "src/modals.css", ctx)
    assert issue is not None
    assert issue["severity"] == "WARNING"
    assert issue["category"] == "Invalid CSS Shorthand"

    rule_valid = {
        "selector": ".form-control",
        "start_line": 52,
        "end_line": 52,
        "properties": {
            "border": "1px solid var(--border)",
        },
    }
    assert eval_invalid_css_shorthand(rule_valid, "src/modals.css", ctx) is None


def test_rigid_height_landscape_risk():
    ctx = AuditContext()
    ctx.project_media_queries = [
        {
            "query": "@media (max-width: 600px)",
            "file": "responsive.css",
            "line": 13,
            "selector": ".panel",
        }
    ]

    rule_rigid = {
        "selector": ".users-table-wrapper",
        "classes": ["users-table-wrapper"],
        "start_line": 205,
        "end_line": 215,
        "properties": {
            "min-height": "520px",
            "overflow-x": "auto",
        },
    }
    issue = eval_rigid_height_landscape_risk(rule_rigid, "src/followers.css", ctx)
    assert issue is not None
    assert issue["severity"] == "CRITICAL"
    assert issue["category"] == "Rigid Height Landscape Risk"

    # Con override de max-height presente
    ctx.project_media_queries.append(
        {
            "query": "@media (max-height: 480px)",
            "file": "responsive.css",
            "line": 135,
            "selector": ".users-table-wrapper",
        }
    )
    assert (
        eval_rigid_height_landscape_risk(rule_rigid, "src/followers.css", ctx) is None
    )


def test_2d_breakpoint_collision_detected():
    ctx = AuditContext()
    ctx.project_media_queries = [
        {
            "query": "@media (max-width: 600px)",
            "file": "responsive.css",
            "line": 13,
            "selector": "#app-root",
        },
        {
            "query": "@media (max-height: 480px)",
            "file": "responsive.css",
            "line": 135,
            "selector": "#app-root",
        },
    ]
    issues = eval_2d_breakpoint_collision(ctx)
    assert len(issues) == 1
    assert issues[0]["severity"] == "WARNING"
    assert issues[0]["category"] == "2D Breakpoint Collision Risk"


def test_tooltip_viewport_overflow():
    rule_tooltip = {
        "selector": ".ui-tooltip",
        "start_line": 11,
        "end_line": 30,
        "properties": {
            "position": "absolute",
            "left": "50%",
            "transform": "translateX(-50%)",
            "white-space": "nowrap",
        },
    }
    issue = eval_tooltip_viewport_overflow(rule_tooltip, "src/tooltip.css")
    assert issue is not None
    assert issue["severity"] == "WARNING"
    assert issue["category"] == "Tooltip Viewport Overflow Risk"


def test_flex_wrap_risk_with_space_between_on_collection():
    ctx = AuditContext()
    rule_model_info = {
        "selector": ".agent-model-info",
        "classes": ["agent-model-info"],
        "start_line": 98,
        "end_line": 108,
        "properties": {
            "display": "flex",
            "justify-content": "space-between",
            "align-items": "center",
        },
    }
    issue = eval_flex_wrap_risk(rule_model_info, "src/agent.css", ctx, set())
    assert issue is not None
    assert issue["category"] == "Flex Wrap Overflow Risk"


def test_z_index_conflict_resolves_var():
    ctx = AuditContext()
    ctx.css_variables = {
        "--z-header": {"value": "110"},
    }
    rule = {
        "selector": ".app-header",
        "start_line": 10,
        "end_line": 20,
        "properties": {
            "position": "sticky",
            "z-index": "var(--z-header)",
        },
    }
    issue = eval_z_index_conflict(rule, "src/header.css", ctx)
    assert issue is not None
    assert "110" in issue["message"]


def test_stacking_trap_excludes_modal_internals_and_overlays():
    from rag_local.services.style_audit.evaluators import eval_stacking_context_trap

    ctx = AuditContext()
    ctx.component_parent_map = {
        "modal-card": {"modal-backdrop"},
        "modal-icon": {"modal-backdrop", "modal-card"},
        "toast-title": {"toast-card", "toast-overlay"},
    }
    ctx.stacking_context_classes = {
        "modal-backdrop": {
            "trigger": "backdrop-filter: blur(8px)",
            "file": "modals.css",
            "line": 4,
        },
        "toast-card": {
            "trigger": "backdrop-filter: blur(10px)",
            "file": "toasts.css",
            "line": 14,
        },
    }

    rule_modal_card = {
        "selector": ".modal-card",
        "classes": ["modal-card"],
        "start_line": 20,
        "end_line": 30,
        "properties": {"position": "relative"},
    }
    assert eval_stacking_context_trap(rule_modal_card, "src/modals.css", ctx) is None

    rule_toast_title = {
        "selector": ".toast-title",
        "classes": ["toast-title"],
        "start_line": 10,
        "end_line": 15,
        "properties": {"font-weight": "bold"},
    }
    assert eval_stacking_context_trap(rule_toast_title, "src/toasts.css", ctx) is None


def test_flex_wrap_excludes_input_toolbars():
    ctx = AuditContext()
    rule_chat_input = {
        "selector": ".chat-input-form",
        "classes": ["chat-input-form"],
        "start_line": 575,
        "end_line": 585,
        "properties": {
            "display": "flex",
            "gap": "8px",
            "align-items": "center",
        },
    }
    assert eval_flex_wrap_risk(rule_chat_input, "src/chat.css", ctx, set()) is None


def test_text_break_excludes_th_and_short_labels():
    from rag_local.services.style_audit.evaluators import eval_text_break_risk

    ctx = AuditContext()
    rule_th = {
        "selector": ".users-table th",
        "classes": ["users-table"],
        "start_line": 254,
        "end_line": 260,
        "properties": {"font-size": "13px"},
    }
    assert eval_text_break_risk(rule_th, "src/followers.css", ctx) is None


def test_stacking_trap_excludes_popover_subparts_and_self_isolated_overlay():
    from rag_local.services.style_audit.evaluators import eval_stacking_context_trap

    ctx = AuditContext()
    ctx.component_parent_map = {
        "conn-popover-dot": {"conn-popover"},
        "conn-popover-status": {"conn-popover"},
    }
    ctx.stacking_context_classes = {
        "conn-popover": {
            "trigger": "isolation: isolate",
            "file": "header.css",
            "line": 40,
        }
    }

    # 1. Elemento interno con posición estática no es capa flotante
    rule_dot = {
        "selector": ".conn-popover-dot",
        "classes": ["conn-popover-dot"],
        "start_line": 50,
        "end_line": 55,
        "properties": {"width": "8px", "height": "8px"},
    }
    assert eval_stacking_context_trap(rule_dot, "src/header.css", ctx) is None

    # 2. Popover con isolation propia no debe atrapar a sus propios hijos
    rule_status = {
        "selector": ".conn-popover-status",
        "classes": ["conn-popover-status"],
        "start_line": 60,
        "end_line": 65,
        "properties": {"position": "relative"},
    }
    assert eval_stacking_context_trap(rule_status, "src/header.css", ctx) is None


def test_flex_wrap_excludes_containers_with_elastic_truncation_child():
    from rag_local.services.style_audit.evaluators import eval_flex_wrap_risk

    ctx = AuditContext()
    ctx.component_parent_map = {
        "user-row-name": {"user-row"},
        "user-row-badge": {"user-row"},
    }
    rule_row = {
        "selector": ".user-row",
        "classes": ["user-row"],
        "start_line": 10,
        "end_line": 20,
        "properties": {"display": "flex", "align-items": "center"},
    }
    rule_name = {
        "selector": ".user-row-name",
        "classes": ["user-row-name"],
        "start_line": 22,
        "end_line": 30,
        "properties": {
            "flex": "1",
            "min-width": "0",
            "overflow": "hidden",
            "text-overflow": "ellipsis",
            "white-space": "nowrap",
        },
    }
    ctx.parsed_css_by_file["src/users.css"] = [rule_row, rule_name]
    ctx.class_dynamic_contexts["user-row"] = [("src/users.css", True, True)]

    # El contenedor con colección/múltiples hijos no emite alerta
    # porque el hijo absorbe el ancho mediante truncamiento elástico
    assert eval_flex_wrap_risk(rule_row, "src/users.css", ctx, set()) is None
