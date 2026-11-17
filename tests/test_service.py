from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw

from screen_bridge.desktop import Frame, parse_key
from screen_bridge.models import Box, BridgeError, element
from screen_bridge.perception import AccessibilityReader, expand_to_old_text, merge_text
from screen_bridge.service import ScreenService
from screen_bridge.storage import StateStore, atomic_json, read_json


class FakeDesktop:
    def __init__(self, x=-200, y=100):
        self.image = Image.new("RGB", (320, 240), "white")
        self.window = {
            "hwnd": 123,
            "pid": 456,
            "title": "fixture",
            "bbox": [x, y, 320, 240],
            "capture_bbox": [x, y, 320, 240],
        }
        self.actions = []

    def capture(self):
        return Frame(deepcopy(self.window), self.image.copy())

    def close(self):
        pass

    def click(self, *args):
        self.actions.append(("click", args))

    def type_text(self, text):
        self.actions.append(("type_text", text))

    def press_key(self, key):
        self.actions.append(("press_key", key))

    def scroll(self, *args):
        self.actions.append(("scroll", args))


class FakeReader:
    def __init__(self):
        self.calls = []

    def read(self, image, origin):
        self.calls.append(origin)
        return [element("READABLE", Box(origin.x + 5, origin.y + 5, 30, 15), "text", "ocr")]


@pytest.fixture
def bridge(tmp_path):
    return ScreenService(tmp_path, desktop=FakeDesktop(), reader=FakeReader(), uia=False)


def test_unchanged_pixels_reuse_ocr_and_persist_complete_state(bridge):
    initial = bridge.collect_once()
    later = bridge.collect_once()
    assert initial["snapshot_id"] == later["snapshot_id"]
    assert len(bridge.reader.calls) == 1
    stored = json.loads((bridge.store.root / "screen.json").read_text("utf-8"))
    assert stored["elements"][0]["text"] == "READABLE"
    assert stored["coordinate_system"] == "physical_virtual_desktop_pixels"
    assert stored["elements"][0]["bbox"][0] < 0
    assert stored["captured_at"] <= stored["observed_at"]
    assert Image.open(stored["image_path"]).size == (320, 240)


def test_collection_failure_invalidates_file_and_recovery_and_stop_are_published(bridge):
    original = bridge.desktop.capture
    failing = threading.Event()

    def capture():
        if failing.is_set():
            raise RuntimeError("fixture unavailable")
        return original()

    bridge.desktop.capture = capture
    bridge.interval = 0.1
    bridge.start()
    try:
        initial = bridge.get_screen(timeout_ms=3000)
        failing.set()
        for _ in range(60):
            if read_json(bridge.store.root / "screen.json")["status"] == "unavailable":
                break
            time.sleep(0.02)
        else:
            pytest.fail("Collection failure did not invalidate the state file")
        with pytest.raises(BridgeError) as error:
            bridge.get_screen(timeout_ms=0)
        assert error.value.code == "SCREEN_UNAVAILABLE"
        failing.clear()
        for _ in range(60):
            if read_json(bridge.store.root / "screen.json")["status"] == "ready":
                break
            time.sleep(0.02)
        else:
            pytest.fail("Recovery did not republish a ready state")
        assert bridge.get_screen()["snapshot_id"] == initial["snapshot_id"]
    finally:
        failing.clear()
        bridge.stop()
    assert read_json(bridge.store.root / "screen.json")["status"] == "stopped"


def test_changed_area_preserves_unaffected_text_and_bounds_ocr_work(bridge):
    initial = bridge.collect_once()
    old_element = initial["elements"][0]
    ImageDraw.Draw(bridge.desktop.image).rectangle((240, 180, 260, 200), fill="red")
    changed = bridge.collect_once()
    assert changed["snapshot_id"] != initial["snapshot_id"]
    assert bridge.reader.calls[-1].area < 320 * 240 / 2
    assert old_element in changed["elements"]


def test_failed_ocr_does_not_retain_stale_text(bridge):
    bridge.collect_once()
    bridge.desktop.image = Image.new("RGB", (320, 240), "black")

    def broken(*args):
        raise RuntimeError("test OCR failure")

    bridge.reader.read = broken
    state = bridge.collect_once()
    assert not state["elements"]
    assert any("OCR failed" in warning for warning in state["warnings"])


@pytest.mark.parametrize("change", ["pixels", "window", "position"])
def test_live_screen_guard_prevents_actions_without_waiting_for_collector(bridge, change):
    state = bridge.collect_once()
    if change == "pixels":
        bridge.desktop.image.putpixel((100, 100), (0, 0, 0))
    elif change == "window":
        bridge.desktop.window["hwnd"] = 999
    else:
        bridge.desktop.window["bbox"][0] += 1
    with pytest.raises(BridgeError, match="changed") as error:
        bridge.act("click", state["snapshot_id"], x=-100, y=150)
    assert error.value.code == "STALE_SNAPSHOT"
    assert bridge.desktop.actions == []


def test_old_snapshot_and_out_of_bounds_are_rejected(bridge):
    state = bridge.collect_once()
    with pytest.raises(BridgeError) as error:
        bridge.act("click", "wrong", x=-100, y=150)
    assert error.value.code == "STALE_SNAPSHOT"
    with pytest.raises(BridgeError) as error:
        bridge.act("click", state["snapshot_id"], x=999, y=150)
    assert error.value.code == "OUT_OF_BOUNDS"
    assert bridge.desktop.actions == []


def test_element_click_uses_physical_coordinates_on_negative_origin(bridge):
    state = bridge.collect_once()
    target = state["elements"][0]
    result = bridge.act("click", state["snapshot_id"], element_id=target["id"])
    assert result["status"] == "sent"
    assert bridge.desktop.actions == [("click", (*Box.parse(target["bbox"]).center(), "left"))]


def test_image_crop_description_is_written_and_reused_after_other_region_changes(bridge):
    state = bridge.collect_once()
    metadata, png = bridge.get_region_image(state["snapshot_id"], bbox=[-150, 150, 80, 60])
    assert Image.open(io.BytesIO(png)).size == (80, 60)
    bridge.describe_region(state["snapshot_id"], metadata["id"], "白色矩形区域")
    persisted = json.loads((bridge.store.root / "screen.json").read_text("utf-8"))
    assert any(region["description"] == "白色矩形区域" for region in persisted["regions"])
    bridge.desktop.image.putpixel((300, 200), (255, 0, 0))
    current = bridge.collect_once()
    reused, _ = bridge.get_region_image(current["snapshot_id"], bbox=[-150, 150, 80, 60])
    assert reused["description"] == "白色矩形区域"
    bridge.desktop.image.putpixel((60, 60), (0, 0, 0))
    changed = bridge.collect_once()
    invalidated, _ = bridge.get_region_image(changed["snapshot_id"], bbox=[-150, 150, 80, 60])
    assert invalidated["description"] is None


def test_crop_outside_window_is_rejected(bridge):
    state = bridge.collect_once()
    with pytest.raises(BridgeError) as error:
        bridge.get_region_image(state["snapshot_id"], bbox=[0, 0, 200, 100])
    assert error.value.code == "OUT_OF_BOUNDS"


def test_image_description_remains_valid_after_the_foreground_snapshot_changes(bridge):
    initial = bridge.collect_once()
    region, _ = bridge.get_region_image(initial["snapshot_id"], bbox=[-150, 150, 80, 60])
    bridge.desktop.window["hwnd"] = 999
    bridge.desktop.image = Image.new("RGB", (320, 240), "black")
    bridge.collect_once()
    described = bridge.describe_region(initial["snapshot_id"], region["id"], "白色区域")
    assert not described["applied_to_current_snapshot"]
    assert described["description"] == "白色区域"
    assert all(r["description"] is None for r in bridge.get_screen()["regions"])
    bridge.desktop.image = Image.new("RGB", (320, 240), "white")
    current = bridge.collect_once()
    reused, _ = bridge.get_region_image(current["snapshot_id"], bbox=[-150, 150, 80, 60])
    assert reused["description"] == "白色区域"


def test_blocked_accessibility_provider_is_terminated_and_falls_back(monkeypatch):
    real_popen = subprocess.Popen
    processes = []

    def start_blocked(*args, **kwargs):
        process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("screen_bridge.perception.subprocess.Popen", start_blocked)
    reader = AccessibilityReader()
    started = time.monotonic()
    try:
        elements, warnings = reader.read(FakeDesktop().capture())
        assert not elements and any("timed out" in warning for warning in warnings)
        assert time.monotonic() - started < 5
        assert processes[0].poll() is not None
    finally:
        reader.close()


def test_accessibility_helper_reuses_one_process_and_frames_json(monkeypatch):
    real_popen = subprocess.Popen
    processes = []

    def start_responder(*args, **kwargs):
        code = (
            "import sys,json\n"
            "for line in sys.stdin:\n"
            " print(json.dumps({'elements': [], 'warnings': ['fixture reply']}), flush=True)\n"
        )
        process = real_popen([sys.executable, "-c", code], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("screen_bridge.perception.subprocess.Popen", start_responder)
    reader = AccessibilityReader()
    try:
        assert reader.read(FakeDesktop().capture()) == ([], ["fixture reply"])
        assert reader.read(FakeDesktop().capture()) == ([], ["fixture reply"])
        assert len(processes) == 1
    finally:
        reader.close()
    assert processes[0].poll() is not None


def test_wait_timeout_reports_unchanged_and_find_text_is_case_insensitive(bridge):
    state = bridge.collect_once()
    assert not bridge.get_screen(state["snapshot_id"], timeout_ms=0)["changed"]
    assert len(bridge.find_text("read")["matches"]) == 1
    assert not bridge.find_text("read", exact=True)["matches"]


def test_uia_and_ocr_duplicates_merge_but_distinct_labels_remain():
    box = Box(20, 30, 100, 20)
    uia = [element("Hello World", box, "button", "uia")]
    ocr = [
        element("HelloWorld", box, "text", "ocr"),
        element("Other", Box(20, 90, 60, 20), "text", "ocr"),
    ]
    assert [e["text"] for e in merge_text(uia, ocr)] == ["Hello World", "Other"]


def test_incremental_crop_expands_to_complete_old_line():
    old = [element("long line", Box(10, 30, 300, 20), "text", "ocr")]
    expanded = expand_to_old_text(Box(150, 30, 40, 10), old, Box(0, 0, 400, 100))
    assert expanded.x <= 10 and expanded.right >= 310


def test_process_lock_is_exclusive_and_recoverable(tmp_path):
    first, second = StateStore(tmp_path), StateStore(tmp_path)
    first.acquire()
    try:
        with pytest.raises((RuntimeError, OSError)):
            second.acquire()
    finally:
        first.release()
        second.release()
    second.acquire()
    second.release()


def test_atomic_file_is_never_partial_under_concurrent_reads(tmp_path):
    path = tmp_path / "state.json"
    atomic_json(path, {"generation": 0, "payload": "x" * 10000})
    errors = []
    stopped = threading.Event()

    def read():
        while not stopped.is_set():
            try:
                value = read_json(path)
                assert len(value["payload"]) == 10000
            except Exception as exc:
                errors.append(exc)

    thread = threading.Thread(target=read)
    thread.start()
    try:
        for generation in range(20):
            atomic_json(path, {"generation": generation, "payload": "x" * 10000})
    finally:
        stopped.set()
        thread.join()
    assert errors == []


@pytest.mark.parametrize("key", ["Ctrl+A", "Enter", "Alt+F4", "Shift+Tab", "Win+Left"])
def test_supported_shortcuts(key):
    assert parse_key(key)


@pytest.mark.parametrize("key", ["", "Ctrl++A", "Ctrl+Ctrl", "unsupported"])
def test_invalid_shortcuts_cannot_leave_modifiers_pressed(key):
    with pytest.raises(BridgeError):
        parse_key(key)


@pytest.mark.parametrize("values", [[1, 2, 0, 1], [1, 2, 3], [True, 0, 2, 2]])
def test_invalid_rectangles(values):
    with pytest.raises(BridgeError):
        Box.parse(values)
