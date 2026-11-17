"""Verify the installed package through a real MCP subprocess and a controlled Windows window."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from screen_bridge.models import Box


def read_json(path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def exercise_skill_cli(root: Path, controls: dict):
    output = root / "skill-screen"

    def cli(*arguments, check=True):
        completed = subprocess.run(
            [sys.executable, "-m", "screen_bridge", *arguments, "--output", str(output)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=45,
        )
        if check:
            assert completed.returncode == 0, completed.stderr
            return json.loads(completed.stdout)
        return completed

    started = cli("start", "--interval", "0.2")
    try:
        assert cli("start")["pid"] == started["pid"], "start must reuse its service"
        state = cli("screen")
        assert state["window"]["title"] == "Screen Bridge Test Window"
        assert cli("find", "CLICK TARGET")["matches"]
        assert cli("read")["snapshot_id"]
        # The entry caret may blink; the image request is versioned and can be retried.
        for _ in range(12):
            state = cli("screen")
            image = cli(
                "image",
                "--snapshot",
                state["snapshot_id"],
                "--bbox",
                *map(str, controls["canvas"]),
                check=False,
            )
            if image.returncode == 0:
                break
            assert "STALE_SNAPSHOT" in image.stderr, image.stderr
            time.sleep(0.2)
        else:
            raise AssertionError("Could not obtain region image through CLI")
        region = json.loads(image.stdout)
        assert Image.open(region["image_path"]).size == tuple(controls["canvas"][2:])
        events_before = read_json(root / "events.json")["clicks"]
        x, y = Box.parse(controls["button"]).center()
        for _ in range(12):
            state = cli("screen")
            clicked = cli(
                "click",
                "--snapshot",
                state["snapshot_id"],
                "--x",
                str(x),
                "--y",
                str(y),
                check=False,
            )
            if clicked.returncode == 0:
                break
            assert "STALE_SNAPSHOT" in clicked.stderr, clicked.stderr
            time.sleep(0.2)
        else:
            raise AssertionError("Could not click through skill CLI")
        time.sleep(0.3)
        assert read_json(root / "events.json")["clicks"] == events_before + 1
        invalid = cli("click", "--snapshot", "invalid", "--x", str(x), "--y", str(y), check=False)
        assert invalid.returncode != 0 and "STALE_SNAPSHOT" in invalid.stderr
    finally:
        assert cli("stop")["status"] == "stopped"
    assert not (output / "session.json").exists()


async def exercise(root: Path, controls: dict):
    output = root / "screen"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "screen_bridge", "mcp", "--output", str(output), "--interval", "0.2"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert names == {
                "get_screen",
                "find_text",
                "get_region_image",
                "describe_region",
                "click",
                "type_text",
                "press_key",
                "scroll",
            }

            async def call(name, args=None):
                result = await client.call_tool(name, args or {})
                assert not result.isError, (name, result.content)
                if result.structuredContent is not None:
                    return result.structuredContent
                return json.loads(result.content[0].text)

            async def current():
                value = await call("get_screen")
                assert value["window"]["title"] == "Screen Bridge Test Window", value["window"]
                return value

            async def action(name, values):
                # Caret/hover animation may update pixels between observation and action.
                for _ in range(12):
                    value = await current()
                    result = await client.call_tool(
                        name, {"snapshot_id": value["snapshot_id"], **values}
                    )
                    if not result.isError:
                        return value
                    assert "STALE_SNAPSHOT" in str(result.content), result.content
                    await asyncio.sleep(0.2)
                raise AssertionError(f"Could not obtain stable screen for {name}: {result.content}")

            first = await current()
            words = " ".join(item["text"] for item in first["elements"])
            assert "SCREEN BRIDGE" in words and "CLICK TARGET" in words, words
            assert any(item["source"] == "ocr" for item in first["elements"])
            assert any("中文" in item["text"] for item in first["elements"]), words
            assert not any("OCR failed" in warning for warning in first["warnings"]), first[
                "warnings"
            ]
            assert (output / "screen.json").exists()
            assert (await call("find_text", {"query": "CLICK TARGET"}))["matches"]
            image_result = await client.call_tool(
                "get_region_image",
                {
                    "snapshot_id": first["snapshot_id"],
                    "bbox": controls["canvas"],
                },
            )
            assert not image_result.isError, image_result.content
            metadata = json.loads(image_result.content[0].text)
            png = base64.b64decode(image_result.content[1].data)
            assert Image.open(io.BytesIO(png)).size == tuple(controls["canvas"][2:])
            await call(
                "describe_region",
                {
                    "snapshot_id": first["snapshot_id"],
                    "region_id": metadata["id"],
                    "description": "浅蓝背景上有红色圆形、蓝色矩形和绿色三角形。",
                },
            )
            saved = read_json(output / "screen.json")
            assert any(region["description"] for region in saved["regions"])

            x, y = Box.parse(controls["button"]).center()
            before = await action("click", {"x": x, "y": y})
            await asyncio.sleep(0.5)
            assert read_json(root / "events.json")["clicks"] == 1
            stale = await client.call_tool(
                "click",
                {
                    "snapshot_id": before["snapshot_id"],
                    "x": x,
                    "y": y,
                },
            )
            assert stale.isError and "STALE_SNAPSHOT" in str(stale.content)
            assert read_json(root / "events.json")["clicks"] == 1

            x, y = Box.parse(controls["entry"]).center()
            await action("click", {"x": x, "y": y})
            await asyncio.sleep(0.5)
            await action("type_text", {"text": "Hello 中文 😀"})
            await asyncio.sleep(0.5)
            assert read_json(root / "events.json")["text"] == "Hello 中文 😀"
            await action("press_key", {"key": "Ctrl+A"})
            await asyncio.sleep(0.3)
            await action("type_text", {"text": "REPLACED"})
            await asyncio.sleep(0.3)
            assert read_json(root / "events.json")["text"] == "REPLACED"
            x, y = Box.parse(controls["canvas"]).center()
            await action("scroll", {"direction": "down", "amount": 1, "x": x, "y": y})
            await asyncio.sleep(0.5)
            assert read_json(root / "events.json")["scrolls"] >= 1
            final = await current()
            assert final["window"]["capture_bbox"][2] > 0
            print(
                json.dumps(
                    {
                        "first_processing_ms": first["timings_ms"]["processing"],
                        "element_count": len(first["elements"]),
                        "uia_elements": sum(item["source"] == "uia" for item in first["elements"]),
                        "verified_events": read_json(root / "events.json"),
                    },
                    ensure_ascii=True,
                )
            )


def main():
    if sys.platform != "win32":
        raise RuntimeError("The real desktop smoke test requires Windows")
    with tempfile.TemporaryDirectory(prefix="screen-bridge-smoke-") as temporary:
        root = Path(temporary)
        ready = root / "ready.json"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "screen_bridge.demo",
                "--events",
                str(root / "events.json"),
                "--ready",
                str(ready),
            ]
        )
        try:
            deadline = time.monotonic() + 15
            while (controls := read_json(ready)) is None:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Test window did not become ready")
                time.sleep(0.1)
            asyncio.run(exercise(root, controls))
            exercise_skill_cli(root, controls)
        finally:
            process.terminate()
            process.wait(timeout=10)
    print("DESKTOP_MCP_SMOKE_OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        leaf = error
        while isinstance(leaf, BaseExceptionGroup):
            leaf = leaf.exceptions[0]
        print(f"DESKTOP_SMOKE_FAILED: {leaf}", file=sys.stderr)
        report = Path("runtime/smoke-failure.log")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1) from error
