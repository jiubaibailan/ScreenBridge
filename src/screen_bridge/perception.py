"""Local UI Automation + incremental OCR, with explicit heuristic image regions."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Protocol

import numpy as np
from PIL import Image, ImageChops

from screen_bridge.desktop import Frame
from screen_bridge.models import Box, element


class VisualSegmenter(Protocol):
    """Optional local segmenter contract for SAM 2, Grounding DINO or YOLO adapters."""

    name: str

    def detect(self, image: Image.Image, origin: Box, elements: list[dict]) -> list[dict]:
        """Return image-like regions in physical desktop coordinates."""


class AccessibilityReader:
    """Keep potentially blocking third-party UI providers outside the collector process."""

    def __init__(self):
        self.process = None
        self.replies = None
        self.thread = None

    def _start(self):
        self.replies = queue.Queue()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "screen_bridge.uia_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        process, replies = self.process, self.replies

        def receive():
            try:
                for line in process.stdout:
                    try:
                        replies.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                replies.put(None)

        self.thread = threading.Thread(target=receive, daemon=True)
        self.thread.start()

    def read(self, frame: Frame) -> tuple[list[dict], list[str]]:
        fresh = self.process is None
        if fresh:
            self._start()
        try:
            self.process.stdin.write(json.dumps(frame.window) + "\n")
            self.process.stdin.flush()
            result = self.replies.get(timeout=1.5 if fresh else 0.4)
            if result is None:
                raise OSError("UIA helper exited")
            return result["elements"], result["warnings"]
        except (OSError, KeyError, queue.Empty):
            self.close()
            return [], ["UIA helper timed out or exited; using OCR for this frame"]

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            process.stdin.close()
            if self.thread is not None:
                self.thread.join(timeout=1)
            process.stdout.close()


def read_accessibility(frame: Frame, budget_seconds: float = 0.25) -> tuple[list[dict], list[str]]:
    import uiautomation as auto

    result, warnings = [], []
    excluded = {"WindowControl", "PaneControl", "GroupControl", "DocumentControl"}
    with auto.UIAutomationInitializerInThread():
        root = auto.ControlFromHandle(frame.window["hwnd"])
        started = time.perf_counter()
        stack = [(root, 0)]
        visited = 0
        while stack and visited < 1000:
            if time.perf_counter() - started > budget_seconds:
                warnings.append(
                    "UIA traversal budget reached; OCR supplies additional visible text"
                )
                break
            control, depth = stack.pop()
            visited += 1
            try:
                if control.IsOffscreen:
                    continue
                rect = control.BoundingRectangle
                box = Box(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                box = box.intersect(frame.box)
                kind = control.ControlTypeName
                name = control.Name or ""
                password = bool(control.IsPassword)
                if kind == "EditControl" and not password:
                    try:
                        pattern = control.GetValuePattern()
                        name = pattern.Value if pattern is not None else name
                    except Exception:
                        pass
                if box and kind not in excluded:
                    if name or kind in {"EditControl", "ImageControl", "ButtonControl"}:
                        result.append(
                            element(
                                "[password]" if password else str(name)[:4000],
                                box,
                                kind.removesuffix("Control").lower(),
                                "uia",
                                password=password,
                            )
                        )
                if depth < 20:
                    stack.extend((child, depth + 1) for child in reversed(control.GetChildren()))
            except Exception:
                # A disappearing control is normal while an application redraws itself.
                continue
    return result, warnings


def dirty_box(previous: Image.Image | None, current: Image.Image, origin: Box) -> Box:
    if previous is None or previous.size != current.size:
        return origin
    bounds = ImageChops.difference(previous, current).getbbox()
    if bounds is None:
        return origin
    left, top, right, bottom = bounds
    padded = Box(origin.x + left - 32, origin.y + top - 32, right - left + 64, bottom - top + 64)
    return padded.intersect(origin) or origin


def expand_to_old_text(dirty: Box, previous: list[dict], origin: Box) -> Box:
    """Do not leave the unchanged half of a changed OCR line in the state."""
    while True:
        expanded = dirty
        for item in previous:
            box = Box.parse(item["bbox"])
            if box.intersect(dirty):
                x, y = min(expanded.x, box.x - 8), min(expanded.y, box.y - 8)
                right, bottom = (
                    max(expanded.right, box.right + 8),
                    max(expanded.bottom, box.bottom + 8),
                )
                expanded = Box(x, y, right - x, bottom - y).intersect(origin) or origin
        if expanded == dirty:
            return dirty
        dirty = expanded


class LocalOCR:
    def __init__(self):
        self._engine = None

    def initialize(self) -> None:
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)

    def read(self, image: Image.Image, origin: Box) -> list[dict]:
        self.initialize()
        # RapidOCR expects OpenCV's BGR ordering for ndarray inputs.
        result, _ = self._engine(np.asarray(image.convert("RGB"))[:, :, ::-1].copy())
        items = []
        for polygon, text, confidence in result or []:
            if not str(text).strip() or confidence < 0.45:
                continue
            xs, ys = zip(*polygon, strict=True)
            left, top = int(min(xs)), int(min(ys))
            right, bottom = int(max(xs) + 1), int(max(ys) + 1)
            box = Box(origin.x + left, origin.y + top, right - left, bottom - top).intersect(origin)
            if box:
                items.append(
                    element(str(text), box, "text", "ocr", confidence=round(confidence, 4))
                )
        return items


def merge_text(uia: list[dict], ocr: list[dict]) -> list[dict]:
    result = list(uia)
    for item in ocr:
        box = Box.parse(item["bbox"])
        duplicate = False
        for other in uia:
            overlap = box.intersect(Box.parse(other["bbox"]))
            same = (
                "".join(item["text"].split()).casefold()
                == "".join(other["text"].split()).casefold()
            )
            if same and overlap and overlap.area / box.area > 0.5:
                duplicate = True
                break
        if not duplicate:
            result.append(item)
    return sorted(result, key=lambda item: (item["bbox"][1], item["bbox"][0], item["source"]))


def visual_regions(image: Image.Image, origin: Box, elements: list[dict]) -> list[dict]:
    """Candidates, not a claim that arbitrary pictures can be detected perfectly."""
    import cv2

    found = [
        {"bbox": item["bbox"], "kind": "image", "source": "uia", "name": item["text"]}
        for item in elements
        if item["role"] == "image"
    ]
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 70, 150)
    for item in elements:
        if item["role"] == "image":
            continue
        box = Box.parse(item["bbox"])
        left, top, right, bottom = box.crop_tuple(origin)
        edges[max(0, top - 4) : bottom + 4, max(0, left - 4) : right + 4] = 0
    joined = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((17, 17), np.uint8))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        x, y, width, height = cv2.boundingRect(contour)
        if width < 48 or height < 40 or width * height > origin.area * 0.8:
            continue
        box = Box(origin.x + x, origin.y + y, width, height)
        if any(
            (overlap := box.intersect(Box.parse(region["bbox"]))) is not None
            and overlap.area > min(box.area, Box.parse(region["bbox"]).area) * 0.6
            for region in found
        ):
            continue
        found.append(
            {
                "bbox": box.as_list(),
                "kind": "visual_candidate",
                "source": "heuristic",
                "name": "",
            }
        )
        if len(found) >= 30:
            break
    return found
