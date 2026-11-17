"""Disposable UI Automation helper; JSON lines only on stdout."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from screen_bridge.models import Box
from screen_bridge.perception import read_accessibility


def main():
    for line in sys.stdin:
        try:
            window = json.loads(line)
            frame = SimpleNamespace(window=window, box=Box.parse(window["capture_bbox"]))
            elements, warnings = read_accessibility(frame)
            result = {"elements": elements, "warnings": warnings}
        except Exception as exc:
            result = {"elements": [], "warnings": [f"UIA unavailable; using OCR: {exc}"]}
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
