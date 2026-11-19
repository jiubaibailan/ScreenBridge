from __future__ import annotations

from screen_bridge.mcp_server import create_server


def test_mcp_server_registers_realtime_tools(bridge):
    server = create_server(bridge)
    names = set(server._tool_manager._tools)
    expected = {
        "subscribe_task", "wait_for_event", "get_current_state", "act", "verify_action", "stop_task"
    }
    assert expected <= names

