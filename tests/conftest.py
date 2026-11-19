from __future__ import annotations

from copy import deepcopy

import pytest
from PIL import Image

from screen_bridge.desktop import Frame
from screen_bridge.models import Box, element
from screen_bridge.service import ScreenService


class FixtureDesktop:
    def __init__(self):
        self.image = Image.new("RGB", (320, 240), "white")
        self.window = {
            "hwnd": 123,
            "pid": 456,
            "title": "fixture",
            "bbox": [0, 0, 320, 240],
            "capture_bbox": [0, 0, 320, 240],
        }
        self.actions = []

    def capture(self):
        return Frame(deepcopy(self.window), self.image.copy())

    def close(self):
        return None

    def click(self, *args):
        self.actions.append(args)

    def type_text(self, text):
        self.actions.append(text)

    def press_key(self, key):
        self.actions.append(key)

    def scroll(self, *args):
        self.actions.append(args)


class FixtureReader:
    def read(self, image, origin):
        return [element("READABLE", Box(origin.x + 5, origin.y + 5, 30, 15), "text", "ocr")]


@pytest.fixture
def bridge(tmp_path):
    return ScreenService(tmp_path, desktop=FixtureDesktop(), reader=FixtureReader(), uia=False)
