import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag_local.mcp.tools.event_flow import trace_event_flow
from rag_local.services.subprocess import SubprocessResult


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


def test_trace_event_flow_setup_error(mock_ctx):
    with patch(
        "rag_local.mcp.tools.event_flow.setup_project_context",
        side_effect=ValueError("Setup error"),
    ):
        result = asyncio.run(
            trace_event_flow(mock_ctx, project_path="/app/repo")
        )
        assert "Error de configuración: Setup error" in result


def test_trace_event_flow_no_index(mock_ctx):
    with (
        patch("rag_local.mcp.tools.event_flow.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
    ):
        mock_db.exists.return_value = False
        result = asyncio.run(
            trace_event_flow(mock_ctx, project_path="/app/repo")
        )
        assert result.startswith("NO_INDEX:")


def test_trace_event_flow_success(mock_ctx):
    sub_res = SubprocessResult(
        returncode=0,
        stdout=b"[Event-Flow Map]\nEvent: UserNicknameUpdatedEvent",
        stderr=b"",
    )

    with (
        patch("rag_local.mcp.tools.event_flow.setup_project_context"),
        patch("rag_local.core.config.LANCEDB_PATH") as mock_db,
        patch("rag_local.core.config.REPO_ROOT") as mock_repo,
        patch(
            "rag_local.mcp.tools.event_flow.run_cli_subprocess",
            new_callable=AsyncMock,
            return_value=sub_res,
        ) as mock_sub,
    ):
        mock_db.exists.return_value = True
        mock_db.iterdir.return_value = [MagicMock()]
        mock_repo.resolve.return_value = "/app/repo"

        result = asyncio.run(
            trace_event_flow(
                mock_ctx,
                project_path="/app/repo",
                event_name="user_nickname_updated",
            )
        )
        assert "Event: UserNicknameUpdatedEvent" in result
        mock_sub.assert_called_once()
        cmd = mock_sub.call_args.kwargs["cmd"]
        assert "--limit" in cmd
        assert "--event" in cmd
        assert "user_nickname_updated" in cmd
