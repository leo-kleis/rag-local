from pathlib import Path

from rag_local.services.style_audit import audit_layout_risks, format_audit_report


def test_flex_wrap_overflow_risk_detected(tmp_path: Path):
    """Verifica que se detecten contenedores flex de botones/listas sin flex-wrap."""
    css_content = """
    .pagination-buttons {
        display: flex;
        gap: 8px;
    }
    .tag-list {
        display: inline-flex;
        gap: 4px;
    }
    .user-column-card {
        display: flex;
        flex-direction: column;
        min-width: 0;
    }
    .toolbar-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
    }
    .nav-scroll-bar {
        display: flex;
        overflow-x: auto;
    }
    """
    css_file = tmp_path / "styles.css"
    css_file.write_text(css_content, encoding="utf-8")

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    categories = [issue["category"] for issue in report["issues"]]
    selectors = [issue["selector"] for issue in report["issues"]]

    # Debe detectar .pagination-buttons y .tag-list bajo Flex Wrap Overflow Risk
    assert "Flex Wrap Overflow Risk" in categories
    assert any(".pagination-buttons" in s for s in selectors)
    assert any(".tag-list" in s for s in selectors)

    # NO debe alertar sobre flex-direction: column, flex-wrap: wrap o overflow-x: auto
    assert not any(".user-column-card" in s for s in selectors)
    assert not any(".toolbar-actions" in s for s in selectors)
    assert not any(".nav-scroll-bar" in s for s in selectors)


def test_breakpoint_width_overflow_detected(tmp_path: Path):
    """Verifica detección de anchos fijos que saturan breakpoints media queries."""
    css_content = """
    @media (max-width: 576px) {
        .wide-card-modal {
            min-width: 600px;
        }
        .small-card {
            width: 250px;
        }
    }
    @media (max-width: 480px) {
        .grid-fixed-container {
            display: grid;
            grid-template-columns: 260px 260px;
        }
    }
    """
    css_file = tmp_path / "responsive.css"
    css_file.write_text(css_content, encoding="utf-8")

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"

    categories = [issue["category"] for issue in report["issues"]]
    selectors = [issue["selector"] for issue in report["issues"]]

    assert "Breakpoint Width Overflow" in categories
    assert any(".wide-card-modal" in s for s in selectors)
    assert any(".grid-fixed-container" in s for s in selectors)
    assert not any(".small-card" in s for s in selectors)


def test_live_css_cache_invalidation(tmp_path: Path):
    """Verifica que audit_layout_risks refleje cambios en tiempo real."""
    css_file = tmp_path / "chat.css"
    css_file.write_text(
        """
        .chat-msg-body {
            display: block;
        }
        """,
        encoding="utf-8",
    )

    report_1 = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report_1["total_issues"] == 1
    assert report_1["issues"][0]["category"] == "Text Break Risk"

    # Editar el archivo en caliente para corregir el problema
    css_file.write_text(
        """
        .chat-msg-body {
            display: block;
            overflow-wrap: break-word;
        }
        """,
        encoding="utf-8",
    )

    report_2 = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report_2["total_issues"] == 0

    formatted = format_audit_report(report_2)
    assert "0 issues found" in formatted
