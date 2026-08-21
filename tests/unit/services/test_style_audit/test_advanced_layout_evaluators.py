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
    assert "is trapped inside ancestor '.tab-content'" in issue["message"]
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
    assert "lacks 'min-height: 0'" in issue["message"]


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
    assert "lacks 'overflow-y: auto'" in issue["message"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_aspect_ratio_height_overflow_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de aspect-ratio con ancho 100% sin max-height."""
    css_rules = [
        {
            "selector": ".stream-player-wrapper",
            "start_line": 15,
            "end_line": 25,
            "properties": {
                "aspect-ratio": "16 / 9",
                "width": "100%",
                "max-width": "1000px",
            },
            "classes": ["stream-player-wrapper"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/stream.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    aspect_issues = [
        i
        for i in report["issues"]
        if i["category"] == "Aspect Ratio Height Overflow Risk"
    ]
    assert len(aspect_issues) == 1
    assert "without 'max-height: 100%'" in aspect_issues[0]["message"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_2d_breakpoint_cascade_inversion_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de sobreescritura de cascada CSS en media queries 2D."""
    css_rules = [
        {
            "selector": ".filters-bar",
            "start_line": 534,
            "end_line": 540,
            "media_query": "@media (max-height: 480px)",
            "properties": {"padding": "8px 12px"},
            "classes": ["filters-bar"],
        },
        {
            "selector": ".filters-bar",
            "start_line": 550,
            "end_line": 560,
            "media_query": "@media (max-width: 768px)",
            "properties": {"display": "flex", "flex-direction": "column"},
            "classes": ["filters-bar"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/followers.css", css_rules)]
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="ALL")
    assert report["status"] == "success"

    collision_issues = [
        i for i in report["issues"] if i["category"] == "2D Breakpoint Collision Risk"
    ]
    assert len(collision_issues) == 1
    assert collision_issues[0]["severity"] == "CRITICAL"
    assert "Media Query Cascade Inversion" in collision_issues[0]["message"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_state_modifier_does_not_trigger_stacking_trap(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que modificadores de estado como .open no atrapen elementos."""
    css_forms = [
        {
            "selector": ".custom-select-wrap.open .custom-select-arrow",
            "start_line": 170,
            "end_line": 175,
            "properties": {"transform": "rotate(180deg)"},
            "classes": ["custom-select-wrap", "open", "custom-select-arrow"],
        }
    ]
    css_drawer = [
        {
            "selector": ".history-drawer-tab-btn",
            "start_line": 60,
            "end_line": 70,
            "properties": {"display": "flex"},
            "classes": ["history-drawer-tab-btn"],
        }
    ]
    markup = [
        (
            "src/components/UserHistoryDrawer.js",
            {
                "history-drawer-tab-btn": {
                    "parents": ["[COMP]UserHistoryDrawer", "history-drawer", "open"],
                    "has_dynamic_text": False,
                    "is_collection": False,
                }
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[
            ("src/styles/forms.css", css_forms),
            ("src/styles/history.css", css_drawer),
        ],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="CRITICAL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_cross_file_component_scoped_closure_prevents_false_positives(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que clases de un tab no contaminen a subcomponentes de otro tab."""
    css_settings = [
        {
            "selector": ".settings-section",
            "start_line": 12,
            "end_line": 20,
            "properties": {"backdrop-filter": "blur(20px)"},
            "classes": ["settings-section"],
        }
    ]
    css_followers = [
        {
            "selector": ".table-loading-overlay",
            "start_line": 226,
            "end_line": 240,
            "properties": {"position": "absolute", "inset": "0"},
            "classes": ["table-loading-overlay"],
        }
    ]
    markup = [
        (
            "src/components/App.js",
            {
                "[COMP]SettingsTab": {"parents": ["tab-content", "[COMP]App"]},
                "[COMP]FollowersTab": {"parents": ["tab-content", "[COMP]App"]},
            },
        ),
        (
            "src/components/SettingsTab.js",
            {
                "settings-section": {"parents": ["[COMP]SettingsTab"]},
                "[COMP]DangerSection": {
                    "parents": ["settings-section", "[COMP]SettingsTab"]
                },
            },
        ),
        (
            "src/components/FollowersTab.js",
            {
                "section": {"parents": ["[COMP]FollowersTab"]},
                "[COMP]FollowersTable": {"parents": ["section", "[COMP]FollowersTab"]},
            },
        ),
        (
            "src/components/FollowersTable.js",
            {"table-loading-overlay": {"parents": ["[COMP]FollowersTable", "section"]}},
        ),
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[
            ("src/styles/settings.css", css_settings),
            ("src/styles/followers.css", css_followers),
        ],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="CRITICAL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_portal_mounted_element_suppresses_stacking_trap(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que elementos flotantes montados vía portal en document.body no alerten trap."""
    css_header = [
        {
            "selector": ".app-header",
            "start_line": 2,
            "end_line": 10,
            "properties": {"backdrop-filter": "blur(12px)"},
            "classes": ["app-header"],
        }
    ]
    css_popover = [
        {
            "selector": ".conn-popover",
            "start_line": 83,
            "end_line": 95,
            "properties": {"position": "fixed"},
            "classes": ["conn-popover"],
        }
    ]
    markup = [
        (
            "src/components/ConnectionIndicator.js",
            {
                "__portal_classes__": ["conn-popover", "conn-popover-title"],
                "conn-popover": {
                    "parents": ["[COMP]ConnectionIndicator", "app-header"],
                    "has_dynamic_text": False,
                    "is_collection": False,
                },
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[
            ("src/styles/header.css", css_header),
            ("src/styles/connection-indicator.css", css_popover),
        ],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="CRITICAL")
    assert report["status"] == "success"
    assert report["total_issues"] == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flex_wrap_skips_dialog_headers_and_elastic_micro_rows(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que cabeceras de diálogo y filas atómicas con elipsis no generen avisos INFO de wrap."""
    css_chat = [
        {
            "selector": ".irc-user-top",
            "start_line": 318,
            "end_line": 325,
            "properties": {
                "display": "flex",
                "justify-content": "space-between",
                "align-items": "center",
            },
            "classes": ["irc-user-top"],
        },
        {
            "selector": ".irc-name",
            "start_line": 327,
            "end_line": 335,
            "properties": {
                "text-overflow": "ellipsis",
                "white-space": "nowrap",
                "overflow": "hidden",
                "min-width": "0",
                "flex": "1",
            },
            "classes": ["irc-name"],
        },
        {
            "selector": ".irc-user-badges",
            "start_line": 337,
            "end_line": 342,
            "properties": {"flex-shrink": "0"},
            "classes": ["irc-user-badges"],
        },
    ]
    markup = [
        (
            "src/components/IrcUser.js",
            {
                "irc-name": {"parents": ["irc-user-top"]},
                "irc-user-badges": {"parents": ["irc-user-top"]},
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/chat.css", css_chat)],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="INFO")
    assert report["status"] == "success"
    wrap_issues = [
        i for i in report["issues"] if i["category"] == "Flex Wrap Overflow Risk"
    ]
    assert len(wrap_issues) == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_grid_min_content_overflow_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de CSS Grid con tracks 1fr albergando inputs sin minmax(0, 1fr)."""
    css_filters = [
        {
            "selector": ".filters-bar",
            "start_line": 134,
            "end_line": 145,
            "properties": {
                "display": "grid",
                "grid-template-columns": "1fr 1fr 1fr",
                "gap": "16px",
            },
            "classes": ["filters-bar"],
        }
    ]
    markup = [
        (
            "src/components/FollowersFilterBar.js",
            {
                "filter-dates-row": {
                    "parents": ["filters-bar"],
                    "has_dynamic_text": False,
                    "is_collection": False,
                }
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/followers.css", css_filters)],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    grid_issues = [
        i
        for i in report["issues"]
        if i["category"] == "Grid Track Min-Content Overflow"
    ]
    assert len(grid_issues) == 1
    assert "filters-bar" in grid_issues[0]["selector"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_grid_minmax_0_is_safe(mock_get_metadata: MagicMock, tmp_path: Path) -> None:
    """Verifica que CSS Grid con minmax(0, 1fr) esté libre de alerta."""
    css_filters = [
        {
            "selector": ".filters-bar",
            "start_line": 134,
            "end_line": 145,
            "properties": {
                "display": "grid",
                "grid-template-columns": "repeat(3, minmax(0, 1fr))",
                "gap": "16px",
            },
            "classes": ["filters-bar"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/followers.css", css_filters)],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    grid_issues = [
        i
        for i in report["issues"]
        if i["category"] == "Grid Track Min-Content Overflow"
    ]
    assert len(grid_issues) == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flex_fixed_control_shrink_risk_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de switches/toggles en flexbox sin flex-shrink: 0."""
    css_roles = [
        {
            "selector": ".role-toggle-checkbox",
            "start_line": 463,
            "end_line": 478,
            "properties": {"width": "42px", "height": "24px"},
            "classes": ["role-toggle-checkbox"],
        }
    ]
    markup = [
        (
            "src/components/UserRolesModal.js",
            {
                "role-toggle-checkbox": {
                    "parents": ["modal-role-row"],
                    "has_dynamic_text": False,
                    "is_collection": False,
                }
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/followers.css", css_roles)],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    shrink_issues = [
        i for i in report["issues"] if i["category"] == "Flex Fixed Control Shrink Risk"
    ]
    assert len(shrink_issues) == 1
    assert "role-toggle-checkbox" in shrink_issues[0]["selector"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_flex_fixed_control_with_shrink_0_is_safe(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que switches/toggles con flex-shrink: 0 no emitan alerta."""
    css_roles = [
        {
            "selector": ".role-toggle-checkbox",
            "start_line": 463,
            "end_line": 478,
            "properties": {
                "width": "42px",
                "height": "24px",
                "flex-shrink": "0",
            },
            "classes": ["role-toggle-checkbox"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/followers.css", css_roles)],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    shrink_issues = [
        i for i in report["issues"] if i["category"] == "Flex Fixed Control Shrink Risk"
    ]
    assert len(shrink_issues) == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_landscape_exclusion_trap_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de media queries multi-eje sin regla companion en landscape."""
    css_rules = [
        {
            "selector": ".chat-input-area",
            "start_line": 50,
            "end_line": 65,
            "media_query": "@media (max-width: 600px) and (min-height: 481px)",
            "properties": {"padding": "8px"},
            "classes": ["chat-input-area"],
        }
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/chat.css", css_rules)],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    land_issues = [
        i
        for i in report["issues"]
        if i["category"] == "Landscape Viewport Exclusion Trap"
    ]
    assert len(land_issues) == 1
    assert "chat-input-area" in land_issues[0]["selector"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_landscape_exclusion_safe_with_companion(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que exista regla companion @media (max-height: 480px) excluya la alerta."""
    css_rules = [
        {
            "selector": ".chat-input-area",
            "start_line": 50,
            "end_line": 65,
            "media_query": "@media (max-width: 600px) and (min-height: 481px)",
            "properties": {"padding": "8px"},
            "classes": ["chat-input-area"],
        },
        {
            "selector": ".chat-input-area",
            "start_line": 70,
            "end_line": 80,
            "media_query": "@media (max-height: 480px)",
            "properties": {"padding": "6px"},
            "classes": ["chat-input-area"],
        },
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/chat.css", css_rules)],
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    land_issues = [
        i
        for i in report["issues"]
        if i["category"] == "Landscape Viewport Exclusion Trap"
    ]
    assert len(land_issues) == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_absolute_overflow_clipping_trap_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de tooltips absolute dentro de contenedores con overflow sin portal."""
    css_drawer = [
        {
            "selector": ".history-drawer-panel",
            "start_line": 1,
            "end_line": 10,
            "properties": {"overflow-y": "auto", "display": "flex"},
            "classes": ["history-drawer-panel"],
        }
    ]
    css_tooltip = [
        {
            "selector": ".ui-tooltip",
            "start_line": 14,
            "end_line": 40,
            "properties": {
                "position": "absolute",
                "z-index": "var(--z-popover)",
            },
            "classes": ["ui-tooltip"],
        }
    ]
    markup = [
        (
            "src/components/Tooltip.js",
            {
                "ui-tooltip": {
                    "parents": ["history-drawer-panel"],
                    "has_dynamic_text": False,
                    "is_collection": False,
                }
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[
            ("src/styles/history.css", css_drawer),
            ("src/styles/tooltip.css", css_tooltip),
        ],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    clip_issues = [
        i
        for i in report["issues"]
        if i["category"] == "Absolute Overflow Clipping Trap"
    ]
    assert len(clip_issues) == 1
    assert "ui-tooltip" in clip_issues[0]["selector"]


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_absolute_overflow_clipping_portal_is_safe(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica que tooltips montados vía portal en document.body estén libres de alerta."""
    css_drawer = [
        {
            "selector": ".history-drawer-panel",
            "start_line": 1,
            "end_line": 10,
            "properties": {"overflow-y": "auto", "display": "flex"},
            "classes": ["history-drawer-panel"],
        }
    ]
    css_tooltip = [
        {
            "selector": ".ui-tooltip",
            "start_line": 14,
            "end_line": 40,
            "properties": {
                "position": "absolute",
                "z-index": "var(--z-popover)",
            },
            "classes": ["ui-tooltip"],
        }
    ]
    markup = [
        (
            "src/components/Tooltip.js",
            {
                "__portal_classes__": ["ui-tooltip"],
                "ui-tooltip": {
                    "parents": ["history-drawer-panel"],
                    "has_dynamic_text": False,
                    "is_collection": False,
                },
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[
            ("src/styles/history.css", css_drawer),
            ("src/styles/tooltip.css", css_tooltip),
        ],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    clip_issues = [
        i
        for i in report["issues"]
        if i["category"] == "Absolute Overflow Clipping Trap"
    ]
    assert len(clip_issues) == 0


@patch("rag_local.services.style_audit.get_indexed_metadata")
def test_inline_style_responsive_override_detected(
    mock_get_metadata: MagicMock, tmp_path: Path
) -> None:
    """Verifica detección de estilos inline rígidos en paneles con media queries de compresión."""
    css_history = [
        {
            "selector": ".history-drawer-panel",
            "start_line": 395,
            "end_line": 405,
            "media_query": "@media (max-height: 480px)",
            "properties": {"padding": "6px", "gap": "6px"},
            "classes": ["history-drawer-panel"],
        }
    ]
    markup = [
        (
            "src/components/user/UserProfileDrawer.js",
            {
                "__inline_rules__": [
                    {
                        "line": 339,
                        "tag": "div",
                        "classes": ["history-drawer-panel"],
                        "properties": {
                            "padding": "16px",
                            "gap": "16px",
                            "display": "flex",
                            "flex-direction": "column",
                        },
                    }
                ],
                "history-drawer-panel": {
                    "parents": [],
                    "has_dynamic_text": False,
                    "is_collection": False,
                },
            },
        )
    ]
    mock_get_metadata.return_value = make_audit_mock_data(
        css_entries=[("src/styles/history.css", css_history)],
        markup_entries=markup,
    )

    report = audit_layout_risks(repo_path=str(tmp_path), severity_filter="WARNING")
    assert report["status"] == "success"
    inline_issues = [
        i
        for i in report["issues"]
        if i["category"] == "Inline Style Responsive Override"
    ]
    assert len(inline_issues) == 1
    assert "history-drawer-panel" in inline_issues[0]["selector"]
