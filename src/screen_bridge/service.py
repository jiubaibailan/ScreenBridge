"""Collector lifetime, state publication, image annotations and freshness-guarded input."""

from __future__ import annotations

import copy
import io
import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path

from PIL import ImageChops

from screen_bridge.desktop import Frame, NativeDesktop
from screen_bridge.models import Box, BridgeError, content_hash, finite_number, timestamp
from screen_bridge.perception import (
    AccessibilityReader,
    LocalOCR,
    dirty_box,
    expand_to_old_text,
    merge_text,
    visual_regions,
)
from screen_bridge.storage import StateStore

log = logging.getLogger(__name__)


class ScreenService:
    def __init__(
        self,
        output: Path,
        interval: float = 0.35,
        ocr: str = "auto",
        uia: bool = True,
        desktop=None,
        reader=None,
        uia_reader=None,
    ):
        if interval < 0.1 or interval > 60:
            raise ValueError("interval must be between 0.1 and 60 seconds")
        if ocr not in {"auto", "always", "off"}:
            raise ValueError("ocr must be auto, always or off")
        self.store = StateStore(output)
        self.desktop = desktop if desktop is not None else NativeDesktop()
        self.reader = reader if reader is not None else LocalOCR()
        self.accessibility = AccessibilityReader() if uia and uia_reader is None else None
        self.uia_reader = uia_reader or (self.accessibility.read if self.accessibility else None)
        self.interval, self.ocr, self.uia = interval, ocr, uia
        self._condition = threading.Condition(threading.RLock())
        self._io_lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._state = None
        self._frame = None
        self._ocr_items = []
        self._error = None
        self._last_heartbeat = 0.0
        self._descriptions = self.store.load_descriptions()
        self._issued_regions = OrderedDict()
        self._events = deque(maxlen=128)
        self._subscribed_task = None
        self._task_auto = False
        self._task_stopped = False
        self._event_sequence = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self.store.acquire()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="screen-bridge-collector", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise RuntimeError("Collector is still busy; its output lock remains held")
            self._thread = None
            with self._condition:
                if self._state is not None:
                    self.store.write(
                        {**self._state, "status": "stopped", "stopped_at": timestamp()}
                    )
        self.store.release()

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                started = time.perf_counter()
                try:
                    self.collect_once()
                except Exception as exc:
                    log.warning("Screen collection failed: %s", exc)
                    with self._condition:
                        self._error = str(exc)
                        self.store.write(
                            {
                                "schema_version": 1,
                                "status": "unavailable",
                                "observed_at": timestamp(),
                                "message": self._error,
                            }
                        )
                        self._condition.notify_all()
                remaining = max(0.01, self.interval - (time.perf_counter() - started))
                self._wake.wait(remaining)
                self._wake.clear()
        finally:
            self.close_resources()

    def close_resources(self) -> None:
        self.desktop.close()
        if self.accessibility is not None:
            self.accessibility.close()

    def collect_once(self) -> dict:
        started = time.perf_counter()
        with self._io_lock:
            frame = self.desktop.capture()
        captured_at = timestamp()
        fingerprint = frame.fingerprint
        capture_ms = (time.perf_counter() - started) * 1000
        same_window = self._frame is not None and self._frame.identity == frame.identity
        if same_window and self._state and fingerprint == self._state["fingerprint"]:
            with self._condition:
                recovering = self._error is not None
                self._error = None
                self._state["observed_at"] = captured_at
                self._state["window"]["title"] = frame.window["title"]
                self._state["timings_ms"]["last_capture"] = round(capture_ms, 2)
                if recovering or time.monotonic() - self._last_heartbeat >= 1:
                    self.store.write(self._state)
                    self._last_heartbeat = time.monotonic()
                self._condition.notify_all()
                return copy.deepcopy(self._state)
        warnings, uia_items = [], []
        uia_start = time.perf_counter()
        if self.uia:
            try:
                uia_items, uia_warnings = self.uia_reader(frame)
                warnings.extend(uia_warnings)
            except Exception as exc:
                warnings.append(f"UIA unavailable; using OCR: {exc}")
        uia_ms = (time.perf_counter() - uia_start) * 1000
        previous_ocr = self._ocr_items if same_window else []
        dirty = frame.box
        if same_window and self.ocr == "auto":
            dirty = expand_to_old_text(
                dirty_box(self._frame.image, frame.image, frame.box),
                previous_ocr,
                frame.box,
            )
        ocr_start = time.perf_counter()
        ocr_items = []
        if self.ocr != "off":
            try:
                ocr_items = [
                    item for item in previous_ocr if not Box.parse(item["bbox"]).intersect(dirty)
                ]
                ocr_items += self.reader.read(frame.image.crop(dirty.crop_tuple(frame.box)), dirty)
            except Exception as exc:
                warnings.append(f"OCR failed: {exc}")
                # Never carry old OCR through a failed update.
                ocr_items = []
        ocr_ms = (time.perf_counter() - ocr_start) * 1000
        elements = merge_text(uia_items, ocr_items)
        regions = visual_regions(frame.image, frame.box, elements)
        for region in regions:
            self._enrich_region(region, frame)
        state = {
            "schema_version": 1,
            "status": "ready",
            "snapshot_id": uuid.uuid4().hex,
            "captured_at": captured_at,
            "observed_at": captured_at,
            "processed_at": timestamp(),
            "coordinate_system": "physical_virtual_desktop_pixels",
            "bbox_format": "x,y,width,height",
            "window": frame.window,
            "fingerprint": fingerprint,
            "elements": elements,
            "regions": regions,
            "components": self._components(elements, regions),
            "warnings": warnings,
            "ocr_mode": self.ocr,
            "dirty_bbox": dirty.as_list(),
            "timings_ms": {
                "capture": round(capture_ms, 2),
                "uia": round(uia_ms, 2),
                "ocr": round(ocr_ms, 2),
                "processing": round((time.perf_counter() - started) * 1000, 2),
            },
        }
        with self._condition:
            self.store.write(state, frame.image)
            self._frame, self._state, self._ocr_items = frame, state, ocr_items
            self._error = None
            self._last_heartbeat = time.monotonic()
            self._event_sequence += 1
            self._events.append(
                {
                    "sequence": self._event_sequence,
                    "event": "state_changed",
                    "task": self._subscribed_task,
                    "snapshot_id": state["snapshot_id"],
                    "captured_at": state["captured_at"],
                    "components": copy.deepcopy(state["components"]),
                }
            )
            self._condition.notify_all()
            return copy.deepcopy(state)

    @staticmethod
    def _components(elements: list[dict], regions: list[dict]) -> list[dict]:
        components = []
        for item in elements:
            role = item.get("role", "text")
            kind = {"edit": "input", "button": "button", "image": "image"}.get(role, "text")
            bbox = Box.parse(item["bbox"])
            components.append(
                {
                    "id": item["id"],
                    "type": kind,
                    "text": item.get("text", ""),
                    "bbox": item["bbox"],
                    "center": list(bbox.center()),
                    "confidence": item.get(
                        "confidence", 1.0 if item.get("source") == "uia" else None
                    ),
                    "source": item.get("source", "unknown"),
                    "visible": True,
                    "enabled": item.get("enabled", True),
                }
            )
        for item in regions:
            bbox = Box.parse(item["bbox"])
            components.append(
                {
                    "id": item["id"],
                    "type": "image",
                    "text": item.get("name", ""),
                    "bbox": item["bbox"],
                    "center": list(bbox.center()),
                    "confidence": item.get("confidence"),
                    "source": item.get("source", "visual"),
                    "visible": True,
                    "enabled": False,
                }
            )
        return components

    def subscribe_task(self, task: str, auto_execute: bool = False) -> dict:
        if not task or len(task) > 120:
            raise BridgeError("INVALID_ARGUMENT", "task must contain 1..120 characters")
        with self._condition:
            self._subscribed_task = task
            self._task_auto = bool(auto_execute)
            self._task_stopped = False
            self._events.clear()
            current = copy.deepcopy(self._state)
            return {
                "status": "subscribed",
                "task": task,
                "auto_execute": self._task_auto,
                "snapshot_id": current["snapshot_id"] if current else None,
            }

    def wait_for_event(self, after_sequence: int | None = None, timeout_ms: int = 10000) -> dict:
        if not 0 <= timeout_ms <= 30000:
            raise BridgeError("INVALID_ARGUMENT", "timeout_ms must be between 0 and 30000")
        deadline = time.monotonic() + timeout_ms / 1000
        with self._condition:
            while True:
                for event in self._events:
                    if after_sequence is None or event["sequence"] > after_sequence:
                        return copy.deepcopy(event)
                if self._task_stopped:
                    return {"event": "task_stopped", "task": self._subscribed_task}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"event": "timeout", "task": self._subscribed_task}
                self._condition.wait(remaining)

    def get_current_state(self) -> dict:
        state = self.get_screen()
        return {
            "snapshot_id": state["snapshot_id"],
            "task": self._subscribed_task,
            "components": copy.deepcopy(state.get("components", [])),
            "window": copy.deepcopy(state["window"]),
            "observed_at": state["observed_at"],
        }

    def stop_task(self) -> dict:
        with self._condition:
            self._task_stopped = True
            self._condition.notify_all()
            return {"status": "stopped", "task": self._subscribed_task}

    def verify_action(self, snapshot_id: str, expected_component: str | None = None) -> dict:
        state = self.get_screen(wait_for_change_from=snapshot_id, timeout_ms=3000)
        if not state.get("changed"):
            return {"verified": False, "reason": "state did not change", "snapshot_id": snapshot_id}
        if expected_component:
            found = any(
                item["id"] == expected_component for item in state.get("components", [])
            )
            return {
                "verified": found,
                "snapshot_id": state["snapshot_id"],
                "component_present": found,
            }
        return {"verified": True, "snapshot_id": state["snapshot_id"]}

    def _enrich_region(self, region: dict, frame: Frame) -> None:
        box = Box.parse(region["bbox"])
        crop = frame.image.crop(box.crop_tuple(frame.box))
        digest = content_hash(f"{crop.size}".encode() + crop.tobytes())
        region.update(
            {
                "id": "region_" + content_hash(f"{digest}:{box.as_list()}".encode())[:16],
                "content_hash": digest,
                "description": self._descriptions.get(digest),
                "description_source": "ai_annotation" if digest in self._descriptions else None,
            }
        )

    def get_screen(self, wait_for_change_from: str | None = None, timeout_ms: int = 10000) -> dict:
        if not 0 <= timeout_ms <= 30000:
            raise BridgeError("INVALID_ARGUMENT", "timeout_ms must be between 0 and 30000")
        deadline = time.monotonic() + timeout_ms / 1000
        with self._condition:
            while self._state is None or (
                wait_for_change_from is not None
                and self._state["snapshot_id"] == wait_for_change_from
            ):
                if self._error is not None:
                    raise BridgeError("SCREEN_UNAVAILABLE", self._error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            if self._error is not None:
                raise BridgeError("SCREEN_UNAVAILABLE", self._error)
            if self._state is None:
                raise BridgeError("NOT_READY", "Screen state is not ready; retry shortly")
            result = copy.deepcopy(self._state)
            result["changed"] = result["snapshot_id"] != wait_for_change_from
            return result

    def find_text(self, query: str, exact: bool = False) -> dict:
        if not query or len(query) > 1000:
            raise BridgeError("INVALID_ARGUMENT", "query length must be between 1 and 1000")
        state = self.get_screen()
        needle = query.casefold()
        matches = [
            item
            for item in state["elements"]
            if (needle == item["text"].casefold() if exact else needle in item["text"].casefold())
        ]
        return {"snapshot_id": state["snapshot_id"], "matches": matches}

    def _require_snapshot(self, snapshot_id: str) -> tuple[dict, Frame]:
        if self._error is not None:
            raise BridgeError("SCREEN_UNAVAILABLE", self._error)
        if self._state is None or self._frame is None:
            raise BridgeError("NOT_READY", "Screen state is not ready")
        if self._state["snapshot_id"] != snapshot_id:
            raise BridgeError(
                "STALE_SNAPSHOT", "Read get_screen again before using this screen state"
            )
        return self._state, self._frame

    def get_region_image(
        self,
        snapshot_id: str,
        region_id: str | None = None,
        bbox: list[int] | None = None,
    ) -> tuple[dict, bytes]:
        with self._condition:
            state, frame = self._require_snapshot(snapshot_id)
            if region_id and bbox is not None:
                raise BridgeError("INVALID_ARGUMENT", "Supply region_id or bbox, not both")
            if region_id:
                region = next((r for r in state["regions"] if r["id"] == region_id), None)
                if region is None:
                    raise BridgeError("REGION_NOT_FOUND", "Region does not belong to this snapshot")
            else:
                box = Box.parse(bbox) if bbox is not None else frame.box
                if box.intersect(frame.box) != box:
                    raise BridgeError(
                        "OUT_OF_BOUNDS", "Requested region is outside the captured window"
                    )
                region = {
                    "bbox": box.as_list(),
                    "kind": "requested",
                    "source": "request",
                    "name": "",
                }
                self._enrich_region(region, frame)
                if not any(r["id"] == region["id"] for r in state["regions"]):
                    state["regions"].append(region)
                    self.store.write(state)
            crop = frame.image.crop(Box.parse(region["bbox"]).crop_tuple(frame.box))
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            key = (snapshot_id, region["id"])
            self._issued_regions[key] = copy.deepcopy(region)
            self._issued_regions.move_to_end(key)
            while len(self._issued_regions) > 256:
                self._issued_regions.popitem(last=False)
            return {"snapshot_id": snapshot_id, **copy.deepcopy(region)}, buffer.getvalue()

    def describe_region(self, snapshot_id: str, region_id: str, description: str) -> dict:
        if not description.strip() or len(description) > 8000:
            raise BridgeError("INVALID_ARGUMENT", "description must contain 1..8000 characters")
        with self._condition:
            region = self._issued_regions.get((snapshot_id, region_id))
            if region is None:
                raise BridgeError("REGION_NOT_FOUND", "Get the region image before describing it")
            self._descriptions[region["content_hash"]] = description.strip()
            self.store.save_descriptions(self._descriptions)
            region.update(description=description.strip(), description_source="ai_annotation")
            applied = False
            if self._state is not None and self._error is None:
                for item in self._state["regions"]:
                    if item["content_hash"] == region["content_hash"]:
                        item.update(
                            description=description.strip(), description_source="ai_annotation"
                        )
                        applied = True
                self.store.write(self._state)
            return {
                "snapshot_id": snapshot_id,
                **copy.deepcopy(region),
                "applied_to_current_snapshot": applied,
            }

    def act(self, action: str, snapshot_id: str, **arguments) -> dict:
        # Serialize actions. The collector publishes through the same state lock.
        with self._io_lock, self._condition:
            state, frame = self._require_snapshot(snapshot_id)
            live = self.desktop.capture()
            if live.identity != frame.identity or live.fingerprint != state["fingerprint"]:
                self._wake.set()
                reason = (
                    f"window changed (expected={frame.identity}, current={live.identity}, "
                    f"title={live.window['title'][:80]})"
                )
                if live.identity == frame.identity:
                    diff = ImageChops.difference(live.image, frame.image)
                    inner = diff.crop((12, 12, diff.width - 12, diff.height - 12)).getbbox()
                    reason = f"pixels changed in {diff.getbbox()} (inner={inner})"
                raise BridgeError("STALE_SNAPSHOT", f"{reason}; read get_screen again")
            target_id = arguments.pop("element_id", None)
            target = None
            if target_id:
                target = next((e for e in state["elements"] if e["id"] == target_id), None)
                if target is None:
                    raise BridgeError(
                        "ELEMENT_NOT_FOUND", "Element does not belong to this snapshot"
                    )
            if action in {"click", "scroll"}:
                x, y = arguments.pop("x", None), arguments.pop("y", None)
                if target:
                    if x is not None or y is not None:
                        raise BridgeError(
                            "INVALID_ARGUMENT", "Supply element_id or coordinates, not both"
                        )
                    x, y = Box.parse(target["bbox"]).center()
                elif x is None and y is None and action == "scroll":
                    x, y = frame.box.center()
                if x is None or y is None:
                    raise BridgeError("INVALID_ARGUMENT", "Provide both x and y, or an element_id")
                x, y = finite_number(x, "x"), finite_number(y, "y")
                if not frame.box.contains(x, y):
                    raise BridgeError(
                        "OUT_OF_BOUNDS", "Target is outside the captured foreground window"
                    )
                if action == "click":
                    self.desktop.click(x, y, arguments.get("button", "left"))
                else:
                    self.desktop.scroll(x, y, arguments["direction"], arguments.get("amount", 3))
            elif action == "type_text":
                self.desktop.type_text(arguments["text"])
            elif action == "press_key":
                self.desktop.press_key(arguments["key"])
            else:
                raise BridgeError("INVALID_ARGUMENT", f"Unknown action: {action}")
            self._wake.set()
            return {
                "status": "sent",
                "action": action,
                "snapshot_id": snapshot_id,
                "next_step": "Read get_screen to verify the effect before the next action",
            }
