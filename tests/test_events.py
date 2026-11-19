from __future__ import annotations

import threading

from screen_bridge.service import ScreenService


def test_event_queue_delivers_changed_state_and_stops(bridge: ScreenService):
    first = bridge.collect_once()
    bridge.subscribe_task("fixture")
    event = bridge.wait_for_event(timeout_ms=0)
    assert event["event"] == "timeout"

    bridge.desktop.image.putpixel((10, 10), (0, 0, 0))
    bridge.collect_once()
    event = bridge.wait_for_event(timeout_ms=100)
    assert event["event"] == "state_changed"
    assert event["snapshot_id"] != first["snapshot_id"]
    assert event["components"]

    result = {}

    def wait():
        result.update(bridge.wait_for_event(after_sequence=event["sequence"], timeout_ms=1000))

    thread = threading.Thread(target=wait)
    thread.start()
    bridge.stop_task()
    thread.join(timeout=2)
    assert result["event"] == "task_stopped"


def test_current_state_exposes_generic_components(bridge: ScreenService):
    bridge.collect_once()
    bridge.subscribe_task("fixture")
    state = bridge.get_current_state()
    assert state["components"][0]["type"] == "text"
    required = {
        "id", "type", "text", "bbox", "center", "confidence", "source", "visible", "enabled"
    }
    assert required <= set(state["components"][0])
