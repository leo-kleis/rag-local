import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.mcp.tools.style_audit import audit_layout_risks
from rag_local.services.subprocess import SubprocessResult


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


def test_audit_layout_risks_setup_error(mock_ctx):
    with patch(
        "rag_local.mcp.tools.style_audit.setup_project_context",
        side_effect=ValueError("Setup error"),
    ):
        result = asyncio.run(
            audit_layout_risks(mock_ctx, project_path="/app/repo")
        )
        assert "Error de configuración: Setup error" in result


def test_audit_layout_risks_success(mock_ctx):
    sub_res = SubprocessResult(
        returncode=0,
        stdout=b"[CSS Layout Audit - 0 issues found]",
        stderr=b"",
    )

    with (
        patch("rag_local.mcp.tools.style_audit.setup_project_context"),
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch("rag_local.core.config.RAG_ROOT") as mock_rag,
        patch(
            "rag_local.mcp.tools.style_audit.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_repo.resolve.return_value = "/app/repo"
        mock_rag.__str__.return_value = "/app/rag"

        result = asyncio.run(
            audit_layout_risks(mock_ctx, project_path="/app/repo", severity="CRITICAL")
        )

        assert mock_sub.called
        cmd_used = mock_sub.call_args.args[0]
        assert "rag_local.cli.style_audit" in cmd_used
        assert "--severity" in cmd_used
        assert "CRITICAL" in cmd_used
        assert "[CSS Layout Audit - 0 issues found]" in result
