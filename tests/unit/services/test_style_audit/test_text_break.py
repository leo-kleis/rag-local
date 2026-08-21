"""Pruebas para detección de riesgos de rotura de texto (Text Break Risk)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_local.services.style_audit import audit_layout_risks

from .helpers import make_audit_mock_data


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_rule_3_and_condition_for_text_break(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica condición AND entre tipografía y texto dinámico."""
    css_rules = [
        {
            "selector": ".user-bio-text",
            "start_line": 20,
            "end_line": 25,
            "properties": {"font-size": "12px", "line-height": "14px"},
            "classes": ["user-bio-text"],
        }
    ]

    # Caso A: Texto estático -> No reporta Text Break Risk
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/user.css", css_rules)],
        markup_entries=[
            (
                "src/components/User.tsx",
                {
                    "user-bio-text": {
                        "parents": ["user-card"],
                        "has_dynamic_text": False,
                        "is_collection": False,
                    }
                },
            )
        ],
    )

    report_a = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report_a["total_issues"] == 0

    # Caso B: Texto dinámico confirmado -> Reporta Text Break Risk
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/user.css", css_rules)],
        markup_entries=[
            (
                "src/components/User.tsx",
                {
                    "user-bio-text": {
                        "parents": ["user-card"],
                        "has_dynamic_text": True,
                        "is_collection": False,
                    }
                },
            )
        ],
    )

    report_b = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    text_issues_b = [
        i for i in report_b["issues"] if i["category"] == "Text Break Risk"
    ]
    assert len(text_issues_b) == 1
    assert text_issues_b[0]["severity"] == "WARNING"


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_native_text_tag_with_confirmed_static_markup_generates_zero_issues(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que tag nativo estático en markup no genere Text Break Risk."""
    css_rules = [
        {
            "selector": "p.legal-disclaimer",
            "start_line": 1,
            "end_line": 5,
            "properties": {"font-size": "11px", "line-height": "14px"},
            "classes": ["legal-disclaimer"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/legal.css", css_rules)],
        markup_entries=[
            (
                "src/components/Footer.tsx",
                {
                    "legal-disclaimer": {
                        "parents": ["footer-container"],
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
def test_text_break_risk_no_markup_degrades_to_info(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Clase de prosa con font-size sin markup emite INFO en Text Break Risk."""
    css_rules = [
        {
            "selector": ".article-desc",
            "start_line": 1,
            "end_line": 5,
            "properties": {"font-size": "16px"},
            "classes": ["article-desc"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/articles.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    text_issues = [i for i in report["issues"] if i["category"] == "Text Break Risk"]
    assert len(text_issues) == 1
    assert text_issues[0]["severity"] == "INFO"

    # Filtrado por WARNING no debe incluirlo
    report_w = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    text_warn = [i for i in report_w["issues"] if i["category"] == "Text Break Risk"]
    assert len(text_warn) == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_micro_ui_elements_excluded_from_text_break(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que iconos, botones, badges, timestamps y contadores se excluyan de Text Break Risk."""
    css_rules = [
        {
            "selector": ".tab-icon",
            "start_line": 1,
            "end_line": 5,
            "properties": {"font-size": "16px"},
            "classes": ["tab-icon"],
        },
        {
            "selector": ".chat-time",
            "start_line": 10,
            "end_line": 15,
            "properties": {"font-size": "11px"},
            "classes": ["chat-time"],
        },
        {
            "selector": ".stat-value",
            "start_line": 20,
            "end_line": 25,
            "properties": {"font-size": "24px"},
            "classes": ["stat-value"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/ui.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0
