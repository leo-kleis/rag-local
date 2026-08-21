"""Pruebas para evaluadores avanzados de layout CSS."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_local.services.style_audit import audit_layout_risks

from .helpers import make_audit_mock_data


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_stacking_context_trap_detected_when_ancestor_has_isolation(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que un elemento fixed dentro de un ancestro con isolation emita CRITICAL."""
    css_responsive = [
        {
            "selector": ".tab-content",
            "start_line": 31,
            "end_line": 35,
            "properties": {"isolation": "isolate"},
            "classes": ["tab-content"],
        }
    ]
    css_drawer = [
        {
            "selector": ".user-profile-drawer",
            "start_line": 10,
            "end_line": 20,
            "properties": {
                "position": "fixed",
                "top": "0",
                "right": "0",
                "z-index": "530",
            },
            "classes": ["user-profile-drawer"],
        }
    ]
    markup = [
        (
            "src/components/ChatTab.js",
            {
                "user-profile-drawer": {
                    "parents": ["tab-content"],
                    "has_dynamic_text": True,
                    "is_collection": False,
                }
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[
            ("src/styles/responsive.css", css_responsive),
            ("src/styles/history.css", css_drawer),
        ],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    trap_issues = [
        i for i in report["issues"] if i["category"] == "Stacking Context Trap"
    ]
    assert len(trap_issues) == 1
    issue = trap_issues[0]
    assert issue["severity"] == "CRITICAL"
    assert "está contenido en el ancestro '.tab-content'" in issue["message"]
    assert "isolation: isolate" in issue["message"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_z_index_hierarchy_inversion_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de inversión donde un Drawer supera a un Modal."""
    css_variables = [
        {
            "selector": ":root",
            "start_line": 1,
            "end_line": 10,
            "properties": {
                "--z-history-drawer": "530",
                "--z-modal": "510",
                "--z-header": "110",
            },
            "classes": [],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/variables.css", css_variables)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    z_issues = [
        i for i in report["issues"] if i["category"] == "Z-Index Hierarchy Risk"
    ]
    assert len(z_issues) == 1
    issue = z_issues[0]
    assert issue["severity"] == "WARNING"
    assert "--z-history-drawer" in issue["message"]
    assert "--z-modal" in issue["message"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flex_column_scroll_missing_min_height_zero(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica alerta en contenedor scrollable dentro de flex vertical sin min-height: 0."""
    css_rules = [
        {
            "selector": ".chat-feed",
            "start_line": 1,
            "end_line": 8,
            "properties": {
                "display": "flex",
                "flex-direction": "column",
                "flex": "1",
                "overflow-y": "auto",
            },
            "classes": ["chat-feed"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/chat.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    col_issues = [
        i for i in report["issues"] if i["category"] == "Flex Column Scroll Risk"
    ]
    assert len(col_issues) == 1
    issue = col_issues[0]
    assert issue["severity"] == "WARNING"
    assert "carece de 'min-height: 0'" in issue["message"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flex_column_scroll_mitigated_by_explicit_bounded_height(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que contenedores raíz con height: 100% no emitan falso positivo."""
    css_rules = [
        {
            "selector": ".panel",
            "start_line": 1,
            "end_line": 8,
            "properties": {
                "display": "flex",
                "flex-direction": "column",
                "height": "100%",
                "overflow-y": "auto",
            },
            "classes": ["panel"],
        },
        {
            "selector": ".settings-tab",
            "start_line": 10,
            "end_line": 18,
            "properties": {
                "display": "flex",
                "flex-direction": "column",
                "height": "100%",
                "overflow-y": "auto",
            },
            "classes": ["settings-tab"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/views.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    col_issues = [
        i for i in report["issues"] if i["category"] == "Flex Column Scroll Risk"
    ]
    assert len(col_issues) == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_breakpoint_inconsistency_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de divergencias menores en breakpoints responsivos."""
    css_followers = [
        {
            "selector": ".table-responsive",
            "start_line": 50,
            "end_line": 60,
            "media_query": "@media (max-width: 576px)",
            "properties": {"display": "block"},
            "classes": ["table-responsive"],
        }
    ]
    css_responsive = [
        {
            "selector": ".app-layout",
            "start_line": 10,
            "end_line": 20,
            "media_query": "@media (max-width: 600px)",
            "properties": {"display": "flex"},
            "classes": ["app-layout"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[
            ("src/styles/followers.css", css_followers),
            ("src/styles/responsive.css", css_responsive),
        ]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    bp_issues = [
        i for i in report["issues"] if i["category"] == "Breakpoint Inconsistency"
    ]
    assert len(bp_issues) >= 1
    assert any("576px" in i["message"] and "600px" in i["message"] for i in bp_issues)


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_modal_landscape_overflow_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica alerta en backdrop modal centrado sin scroll vertical."""
    css_rules = [
        {
            "selector": ".modal-backdrop",
            "start_line": 1,
            "end_line": 10,
            "properties": {
                "position": "fixed",
                "inset": "0",
                "display": "flex",
                "align-items": "center",
                "justify-content": "center",
            },
            "classes": ["modal-backdrop"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/modals.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    modal_issues = [
        i for i in report["issues"] if i["category"] == "Modal Landscape Overflow Risk"
    ]
    assert len(modal_issues) == 1
    issue = modal_issues[0]
    assert issue["severity"] == "WARNING"
    assert "carece de 'overflow-y: auto'" in issue["message"]
