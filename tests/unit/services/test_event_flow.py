from pathlib import Path
from unittest.mock import patch

from rag_local.services.event_flow import _normalize_event_name, trace_event_flow


def test_normalize_event_name():
    assert _normalize_event_name("UserNicknameUpdatedEvent") == "user_nickname_updated"
    assert _normalize_event_name("event:user_nickname_updated") == "user_nickname_updated"
    assert _normalize_event_name("action:user_nickname_updated") == "user_nickname_updated"
    assert _normalize_event_name("ChatMessageEvent") == "chat_message"


@patch("rag_local.services.event_flow.get_indexed_metadata")
def test_trace_event_flow_complete_chain(mock_get_metadata):
    mock_get_metadata.return_value = [
        {
            "source": "src/bot/events.py",
            "class_name": "UserNicknameUpdatedEvent",
            "method_name": "",
            "tags": "[]",
            "type": "class",
            "text": "class UserNicknameUpdatedEvent:\n    pass",
            "start_line": 88,
            "end_line": 100,
        },
        {
            "source": "src/bot/actions/users.py",
            "class_name": "",
            "method_name": "action_set_nickname",
            "tags": '["action:user_nickname_updated"]',
            "type": "function",
            "text": "await bot.event_bus.emit(UserNicknameUpdatedEvent(user_id, nickname))",
            "start_line": 180,
            "end_line": 200,
        },
        {
            "source": "src/bot/web/ws_handler.py",
            "class_name": "",
            "method_name": "broadcast_event",
            "tags": '["event:user_nickname_updated"]',
            "type": "function",
            "text": 'UserNicknameUpdatedEvent: "user_nickname_updated"',
            "start_line": 58,
            "end_line": 70,
        },
        {
            "source": "src/bot/web/static/app.js",
            "class_name": "appReducer",
            "method_name": "appReducer:user_nickname_updated",
            "tags": '["action:user_nickname_updated"]',
            "type": "reducer_case",
            "text": "case 'user_nickname_updated':\n    return state;",
            "start_line": 286,
            "end_line": 340,
        },
        {
            "source": "src/bot/web/static/components/event-config.js",
            "class_name": "",
            "method_name": "getEventDetails",
            "tags": "[]",
            "type": "function",
            "text": "user_nickname_updated: { label: 'Nickname', icon: 'edit' }",
            "start_line": 160,
            "end_line": 190,
        },
    ]

    report = trace_event_flow(Path("/fake/repo"), target_event="")

    assert "UserNicknameUpdatedEvent" in report
    assert "Definition:  src/bot/events.py:88" in report
    assert "Emitter:     src/bot/actions/users.py:180" in report
    assert "events.py" not in report.split("Emitter:")[1].split("WebSocket:")[0]
    assert "ws_handler.py:58" in report
    assert "app.js:286" in report
    assert "event-config.js:160" in report


@patch("rag_local.services.event_flow.get_indexed_metadata")
def test_trace_event_flow_limit(mock_get_metadata):
    rows = []
    for i in range(20):
        rows.append(
            {
                "source": "src/bot/events.py",
                "class_name": f"EventNumber{i}Event",
                "method_name": "",
                "tags": "[]",
                "type": "class",
                "text": f"class EventNumber{i}Event: pass",
                "start_line": i * 10,
                "end_line": i * 10 + 5,
            }
        )
    mock_get_metadata.return_value = rows

    report = trace_event_flow(Path("/fake/repo"), target_event="", limit=5)
    assert "showing top 5" in report
    assert "15 more events omitted" in report
