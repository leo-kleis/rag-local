"""Pruebas para detección de riesgos de desbordamiento en Flexbox y Grid."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_local.services.style_audit import audit_layout_risks

from .helpers import make_audit_mock_data


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flexbox_overflow_critical_when_dynamic_text(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que contenedor flex con texto dinámico dispare CRITICAL."""
    css_rules = [
        {
            "selector": ".chat-bubble",
            "start_line": 10,
            "end_line": 15,
            "properties": {"display": "flex"},
            "classes": ["chat-bubble"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/chat.css", css_rules)],
        markup_entries=[
            (
                "src/components/Chat.tsx",
                {
                    "chat-bubble": {
                        "parents": ["chat-feed"],
                        "has_dynamic_text": True,
                        "is_collection": False,
                    }
                },
            )
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 1

    issue = report["issues"][0]
    assert issue["severity"] == "CRITICAL"
    assert issue["category"] == "Flexbox/Grid Overflow Risk"
    assert ".chat-bubble" in issue["selector"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flexbox_overflow_mixed_context_degraded(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que el contexto mixto degrade la severidad a WARNING."""
    css_rules = [
        {
            "selector": ".message-snippet",
            "start_line": 5,
            "end_line": 10,
            "properties": {"display": "flex", "font-size": "14px"},
            "classes": ["message-snippet"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/card.css", css_rules)],
        markup_entries=[
            (
                "src/components/Feed.tsx",
                {
                    "message-snippet": {
                        "parents": [],
                        "has_dynamic_text": True,
                        "is_collection": False,
                    }
                },
            ),
            (
                "src/components/Settings.tsx",
                {
                    "message-snippet": {
                        "parents": [],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    }
                },
            ),
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    flex_issues = [
        i for i in report["issues"] if i["category"] == "Flexbox/Grid Overflow Risk"
    ]
    assert len(flex_issues) == 1
    issue = flex_issues[0]
    assert issue["severity"] == "WARNING"
    assert "used in multiple contexts (2 files)" in issue["message"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flexbox_overflow_confirmed_static_with_typography_stays_warning(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que flex-item estático con tipografía no escale a CRITICAL."""
    css_rules = [
        {
            "selector": ".content-box",
            "start_line": 1,
            "end_line": 6,
            "properties": {
                "display": "flex",
                "flex": "1",
                "font-size": "12px",
                "line-height": "14px",
            },
            "classes": ["content-box"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/content.css", css_rules)],
        markup_entries=[
            (
                "src/components/Content.tsx",
                {
                    "content-box": {
                        "parents": ["content-card"],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    }
                },
            )
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 1

    issue = report["issues"][0]
    assert issue["severity"] == "WARNING"
    assert issue["category"] == "Flexbox/Grid Overflow Risk"


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flexbox_overflow_mixed_context_with_typography_stays_warning(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que contexto mixto degrade a WARNING aun con tipografía."""
    css_rules = [
        {
            "selector": ".message-snippet",
            "start_line": 5,
            "end_line": 10,
            "properties": {
                "display": "flex",
                "font-size": "14px",
                "line-height": "18px",
            },
            "classes": ["message-snippet"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/card.css", css_rules)],
        markup_entries=[
            (
                "src/components/Feed.tsx",
                {
                    "message-snippet": {
                        "parents": [],
                        "has_dynamic_text": True,
                        "is_collection": False,
                    }
                },
            ),
            (
                "src/components/Settings.tsx",
                {
                    "message-snippet": {
                        "parents": [],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    }
                },
            ),
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    flex_issues = [
        i for i in report["issues"] if i["category"] == "Flexbox/Grid Overflow Risk"
    ]
    assert len(flex_issues) == 1
    issue = flex_issues[0]
    assert issue["severity"] == "WARNING"
    assert "used in multiple contexts (2 files)" in issue["message"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flexbox_overflow_no_markup_data_fallback(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica fallback por defecto ante ausencia total de markup."""
    css_rules = [
        {
            "selector": ".sidebar-content",
            "start_line": 1,
            "end_line": 5,
            "properties": {"display": "flex", "flex": "1", "font-size": "14px"},
            "classes": ["sidebar-content"],
        },
        {
            "selector": "p.sidebar-desc",
            "start_line": 10,
            "end_line": 15,
            "properties": {"display": "flex", "font-size": "14px"},
            "classes": ["sidebar-desc"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/sidebar.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 2

    flex_issues = [
        i for i in report["issues"] if i["category"] == "Flexbox/Grid Overflow Risk"
    ]
    assert len(flex_issues) == 2
    by_selector = {i["selector"]: i for i in flex_issues}
    assert by_selector[".sidebar-content"]["severity"] == "WARNING"
    assert by_selector["p.sidebar-desc"]["severity"] == "WARNING"


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_column_flex_and_overlays_excluded(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que flex-direction: column y overlays modales no emitan falsos positivos."""
    css_rules = [
        {
            "selector": ".agent-tab",
            "start_line": 1,
            "end_line": 5,
            "properties": {"display": "flex", "flex-direction": "column"},
            "classes": ["agent-tab"],
        },
        {
            "selector": ".modal-backdrop",
            "start_line": 10,
            "end_line": 15,
            "properties": {
                "position": "fixed",
                "inset": "0",
                "display": "flex",
                "align-items": "center",
                "justify-content": "center",
                "overflow-y": "auto",
            },
            "classes": ["modal-backdrop"],
        },
        {
            "selector": ".user-sub-names",
            "start_line": 20,
            "end_line": 25,
            "properties": {"display": "flex", "flex-wrap": "wrap"},
            "classes": ["user-sub-names"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/layout.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0
