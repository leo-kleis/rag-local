"""Pruebas para jerarquía DOM, compatibilidad legacy y falsos positivos."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_local.services.style_audit import audit_layout_risks, format_audit_report

from .helpers import make_audit_mock_data


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_class_with_parents_and_no_flex_properties_generates_zero_issues(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que ancestros en markup NO activen Regla 1 sin propiedades flex."""
    css_rules = [
        {
            "selector": ".nav-link",
            "start_line": 1,
            "end_line": 5,
            "properties": {"color": "#333", "padding": "4px 8px"},
            "classes": ["nav-link"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/nav.css", css_rules)],
        markup_entries=[
            (
                "src/components/Navbar.tsx",
                {
                    "nav-link": {
                        "parents": ["navbar-nav", "header-container", "app-root"],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    }
                },
            )
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_dual_format_deserialization_compatibility(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que el deserializador soporte formato legacy y nuevo."""
    css_rules = [
        {
            "selector": ".legacy-item",
            "start_line": 1,
            "end_line": 5,
            "properties": {"display": "flex", "font-size": "14px"},
            "classes": ["legacy-item"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/legacy.css", css_rules)],
        markup_entries=[
            # Formato legacy donde el valor es directamente list[str]
            ("src/components/Legacy.tsx", {"legacy-item": ["legacy-parent"]})
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert len(report["issues"]) == 1


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_mitigated_ancestor_is_suppressed(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que la mitigación de overflow en un ancestro suprima el aviso de riesgo."""
    css_rules_parent = [
        {
            "selector": ".chat-container",
            "start_line": 1,
            "end_line": 10,
            "properties": {"display": "block", "overflow": "hidden"},
            "classes": ["chat-container"],
        }
    ]
    css_rules_child = [
        {
            "selector": ".chat-message",
            "start_line": 15,
            "end_line": 25,
            "properties": {"display": "flex", "word-break": "break-word"},
            "classes": ["chat-message"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[
            ("src/styles/parent.css", css_rules_parent),
            ("src/styles/child.css", css_rules_child),
        ],
        markup_entries=[
            (
                "src/components/Chat.tsx",
                {
                    "chat-message": {
                        "parents": ["chat-container"],
                        "has_dynamic_text": True,
                        "is_collection": False,
                    }
                },
            )
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_parent_flex_wrap_mitigates_child_overflow(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que un contenedor padre con flex-wrap: wrap proteja a sus hijos del desborde."""
    css_action_row = [
        {
            "selector": ".action-row",
            "start_line": 2,
            "end_line": 8,
            "properties": {
                "display": "flex",
                "flex-wrap": "wrap",
                "min-width": "0",
            },
            "classes": ["action-row"],
        },
        {
            "selector": ".model-select-wrap",
            "start_line": 10,
            "end_line": 14,
            "properties": {"flex": "1", "min-width": "150px"},
            "classes": ["model-select-wrap"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/actions.css", css_action_row)],
        markup_entries=[
            (
                "src/components/ActionsTab.js",
                {
                    "model-select-wrap": {
                        "parents": ["action-row"],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    }
                },
            )
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_media_query_column_switch_mitigates_flex_wrap(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que un layout flex con media query a column no reporte flex-wrap risk."""
    css_layout = [
        {
            "selector": ".two-col-grid",
            "start_line": 4,
            "end_line": 10,
            "properties": {
                "display": "flex",
                "gap": "24px",
                "flex-direction": "row",
                "min-width": "0",
            },
            "classes": ["two-col-grid"],
        },
        {
            "selector": ".two-col-grid",
            "start_line": 20,
            "end_line": 25,
            "media_query": "@media (max-width: 600px)",
            "properties": {"flex-direction": "column", "gap": "16px"},
            "classes": ["two-col-grid"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/layout.css", css_layout)],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    wrap_issues = [
        i for i in report["issues"] if i["category"] == "Flex Wrap Overflow Risk"
    ]
    assert len(wrap_issues) == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_bot_tv_false_positives_regression(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica los patrones reales de bot-tv que generaban falsos positivos."""
    css_rules = [
        # 1. Botón compacto inline-flex con gap
        {
            "selector": ".toolbar-btn",
            "start_line": 1,
            "end_line": 5,
            "properties": {"display": "inline-flex", "gap": "4px"},
            "classes": ["toolbar-btn"],
        },
        # 2. Fila con distribución fija space-between
        {
            "selector": ".user-card-header",
            "start_line": 10,
            "end_line": 15,
            "properties": {
                "display": "flex",
                "justify-content": "space-between",
                "min-width": "0",
                "gap": "8px",
            },
            "classes": ["user-card-header"],
        },
        # 3. Control de formulario textarea (wrap nativo)
        {
            "selector": "textarea.chat-input",
            "start_line": 20,
            "end_line": 25,
            "properties": {"font-size": "14px", "line-height": "20px"},
            "classes": ["chat-input"],
        },
        # 4. Control de formulario input
        {
            "selector": "input.search-field",
            "start_line": 30,
            "end_line": 35,
            "properties": {"font-size": "14px"},
            "classes": ["search-field"],
        },
        # 5. Badge estático sin señal de texto dinámico
        {
            "selector": ".tab-badge",
            "start_line": 40,
            "end_line": 45,
            "properties": {
                "font-size": "11px",
                "line-height": "12px",
                "white-space": "nowrap",
            },
            "classes": ["tab-badge"],
        },
        # 6. Elemento con elipsis / nowrap
        {
            "selector": ".channel-name",
            "start_line": 50,
            "end_line": 55,
            "properties": {
                "font-size": "14px",
                "text-overflow": "ellipsis",
                "overflow": "hidden",
            },
            "classes": ["channel-name"],
        },
        # 7. Ícono con dimensiones fijas
        {
            "selector": ".status-dot",
            "start_line": 60,
            "end_line": 65,
            "properties": {
                "display": "flex",
                "width": "10px",
                "height": "10px",
            },
            "classes": ["status-dot"],
        },
        # 8. Elemento con line-clamp
        {
            "selector": ".video-summary",
            "start_line": 70,
            "end_line": 75,
            "properties": {
                "font-size": "13px",
                "line-height": "18px",
                "-webkit-line-clamp": "2",
            },
            "classes": ["video-summary"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/app.css", css_rules)],
        markup_entries=[
            (
                "src/components/App.tsx",
                {
                    "toolbar-btn": {
                        "parents": [],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    },
                    "user-card-header": {
                        "parents": [],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    },
                    "tab-badge": {
                        "parents": [],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    },
                },
            )
        ],
    )

    report = audit_layout_risks(
        repo_path=str(tmp_path), severity_filter="WARNING"
    )  # Solo WARNING y CRITICAL
    assert report["status"] == "success"
    assert report["total_issues"] == 0

    formatted = format_audit_report(report)
    assert "0 issues found" in formatted


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_reset_selector_grouped_excluded(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Selector agrupado 'html, body' con font-size debe excluirse completamente."""
    css_rules = [
        {
            "selector": "html, body",
            "start_line": 1,
            "end_line": 10,
            "properties": {"font-size": "16px", "line-height": "1.5"},
            "classes": [],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/variables.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0
