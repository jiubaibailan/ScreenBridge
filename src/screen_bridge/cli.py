"""CLI entry points for diagnostics, continuous state files, MCP and benchmarks."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import signal
import statistics
import sys
import time
from importlib.metadata import version
from pathlib import Path

from screen_bridge import __version__
from screen_bridge.models import BridgeError
from screen_bridge.rpc import default_output


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Local Windows screen state for AI assistants")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("watch", "mcp", "benchmark", "start", "serve"):
        child = commands.add_parser(name)
        child.add_argument("--output", type=Path, default=default_output())
        child.add_argument("--interval", type=float, default=0.35)
        child.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
        child.add_argument("--no-uia", action="store_true")
        if name == "watch":
            child.add_argument("--once", action="store_true")
        if name == "benchmark":
            child.add_argument("--samples", type=int, default=10)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--check-ocr", action="store_true")
    for name in (
        "read",
        "status",
        "stop",
        "screen",
        "find",
        "image",
        "describe",
        "click",
        "type",
        "key",
        "scroll",
    ):
        child = commands.add_parser(name)
        child.add_argument("--output", type=Path, default=default_output())
        if name == "screen":
            child.add_argument("--after")
            child.add_argument("--timeout", type=int, default=10000)
        if name == "find":
            child.add_argument("query")
            child.add_argument("--exact", action="store_true")
        if name in {"image", "describe", "click", "type", "key", "scroll"}:
            child.add_argument("--snapshot", required=True)
        if name in {"image", "describe"}:
            child.add_argument("--region", required=name == "describe")
        if name == "image":
            child.add_argument("--bbox", type=int, nargs=4)
            child.add_argument("--save", type=Path)
        if name in {"describe", "type"}:
            content = "description" if name == "describe" else "text"
            group = child.add_mutually_exclusive_group(required=True)
            group.add_argument(f"--{content}")
            group.add_argument(f"--{content}-file", type=Path)
        if name in {"click", "scroll"}:
            child.add_argument("--x", type=int)
            child.add_argument("--y", type=int)
        if name == "click":
            child.add_argument("--element")
            child.add_argument("--button", choices=("left", "right"), default="left")
        if name == "key":
            child.add_argument("key")
        if name == "scroll":
            child.add_argument("direction", choices=("up", "down"))
            child.add_argument("--amount", type=int, default=3)
    commands.add_parser("demo")
    return root


def remote_command(args) -> dict:
    from screen_bridge.rpc import request, start, stop

    name = args.command
    if name == "start":
        return start(args.output, args.interval, args.ocr, not args.no_uia)
    if name == "stop":
        return stop(args.output)
    if name == "status":
        return request(args.output, "ping")
    if name == "screen":
        return request(
            args.output,
            "get_screen",
            {
                "wait_for_change_from": args.after,
                "timeout_ms": args.timeout,
            },
        )
    if name == "find":
        return request(args.output, "find_text", {"query": args.query, "exact": args.exact})
    values = {"snapshot_id": args.snapshot}
    if name == "image":
        values.update(region_id=args.region, bbox=args.bbox)
        result = request(args.output, "get_region_image", values)
        target = args.save or args.output / "regions" / (result["id"] + ".png")
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(result.pop("png_base64")))
        return {**result, "image_path": str(target)}
    if name in {"type", "describe"}:
        field = "text" if name == "type" else "description"
        file = getattr(args, field + "_file")
        values[field] = file.read_text("utf-8-sig") if file else getattr(args, field)
    if name == "describe":
        values["region_id"] = args.region
    if name == "click":
        values.update(element_id=args.element, x=args.x, y=args.y, button=args.button)
    if name == "scroll":
        values.update(direction=args.direction, amount=args.amount, x=args.x, y=args.y)
    if name == "key":
        values["key"] = args.key
    tool = {"type": "type_text", "key": "press_key", "describe": "describe_region"}.get(name, name)
    return request(args.output, tool, values)


def doctor(check_ocr: bool) -> int:
    report = {"version": __version__, "python": sys.version.split()[0], "platform": sys.platform}
    report["packages"] = {
        name: version(name) for name in ("mcp", "mss", "Pillow", "rapidocr-onnxruntime")
    }
    report["supported"] = sys.platform == "win32"
    if check_ocr:
        from screen_bridge.perception import LocalOCR

        LocalOCR().initialize()
        report["ocr"] = "ready"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["supported"] else 1


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s"
    )
    try:
        if args.command == "doctor":
            return doctor(args.check_ocr)
        if args.command == "read":
            from screen_bridge.storage import read_json

            print(json.dumps(read_json(args.output / "screen.json"), ensure_ascii=False, indent=2))
            return 0
        if args.command == "demo":
            from screen_bridge.demo import main as demo_main

            demo_main([])
            return 0
        if args.command in {
            "start",
            "stop",
            "status",
            "screen",
            "find",
            "image",
            "describe",
            "click",
            "type",
            "key",
            "scroll",
        }:
            print(json.dumps(remote_command(args), ensure_ascii=False, indent=2))
            return 0
        from screen_bridge.service import ScreenService

        service = ScreenService(args.output, args.interval, args.ocr, not args.no_uia)
        if args.command == "serve":
            from screen_bridge.rpc import serve

            serve(service)
            return 0
        if args.command == "mcp":
            from screen_bridge.mcp_server import create_server

            create_server(service).run(transport="stdio")
            return 0
        if args.command == "benchmark":
            if not 2 <= args.samples <= 1000:
                raise ValueError("samples must be between 2 and 1000")
            service.store.acquire()
            try:
                durations, snapshots, ocr_times = [], set(), []
                for _ in range(args.samples):
                    started = time.perf_counter()
                    state = service.collect_once()
                    durations.append((time.perf_counter() - started) * 1000)
                    if state["snapshot_id"] not in snapshots:
                        ocr_times.append(state["timings_ms"]["ocr"])
                    snapshots.add(state["snapshot_id"])
                    time.sleep(args.interval)
                report = {
                    "samples": args.samples,
                    "changed_snapshots": len(snapshots),
                    "first_collection_ms": round(durations[0], 2),
                    "subsequent_median_ms": round(statistics.median(durations[1:]), 2),
                    "subsequent_max_ms": round(max(durations[1:]), 2),
                    "ocr_processing_ms": ocr_times,
                    "note": "Local collection only; excludes AI inference and network latency.",
                }
                print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                service.close_resources()
                service.store.release()
            return 0
        if args.once:
            service.store.acquire()
            try:
                print(json.dumps(service.collect_once(), ensure_ascii=False, indent=2))
            finally:
                service.close_resources()
                service.store.release()
            return 0
        service.start()
        print(f"Writing screen state to {service.store.root / 'screen.json'}", file=sys.stderr)
        stopped = False

        def stop(signum, frame):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        try:
            while not stopped:
                time.sleep(0.2)
        finally:
            service.stop()
        return 0
    except (BridgeError, ValueError, RuntimeError, OSError) as exc:
        if isinstance(exc, BridgeError):
            print(json.dumps(exc.payload(), ensure_ascii=False), file=sys.stderr)
            return 1
        print(f"screen-bridge: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
