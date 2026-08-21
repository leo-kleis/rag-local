"""Pruebas para detección de riesgos de Flex Wrap."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from rag_local.services.style_audit import audit_layout_risks

from .helpers import make_audit_mock_data


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_rule_2_asymmetric_collection_signal(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que is_collection: True active señal de múltiples hijos."""
    css_rules = [
        {
            "selector": ".item-container",
            "start_line": 1,
            "end_line": 8,
            "properties": {"display": "flex"},
            "classes": ["item-container"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/list.css", css_rules)],
        markup_entries=[
            (
                "src/components/List.tsx",
                {
                    "item-container": {
                        "parents": [],
                        "has_dynamic_text": False,
                        "is_collection": True,
                    }
                },
            )
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    categories = [i["category"] for i in report["issues"]]
    assert "Flex Wrap Overflow Risk" in categories


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flex_wrap_gap_only_on_micro_ui_is_clean(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Flex horizontal con gap en micro-UI / tabs / toolbars no emite advertencias innecesarias."""
    css_rules = [
        {
            "selector": ".tab-bar",
            "start_line": 1,
            "end_line": 8,
            "properties": {"display": "flex", "gap": "8px"},
            "classes": ["tab-bar"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/tabs.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flex_wrap_collection_signal_stays_warning(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Flex con is_collection confirmado por markup mantiene severidad WARNING."""
    css_rules = [
        {
            "selector": ".item-list",
            "start_line": 1,
            "end_line": 8,
            "properties": {"display": "flex", "gap": "8px"},
            "classes": ["item-list"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/list.css", css_rules)],
        markup_entries=[
            (
                "src/components/List.tsx",
                {
                    "item-list": {
                        "parents": [],
                        "has_dynamic_text": False,
                        "is_collection": True,
                        "own_tags": ["div"],
                    }
                },
            )
        ],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    wrap_issues = [
        i for i in report["issues"] if i["category"] == "Flex Wrap Overflow Risk"
    ]
    assert len(wrap_issues) == 1
    assert wrap_issues[0]["severity"] == "WARNING"
