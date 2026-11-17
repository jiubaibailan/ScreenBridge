"""Authenticated local named-pipe transport for the standalone skill CLI."""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from pathlib import Path

from screen_bridge.models import BridgeError, content_hash
from screen_bridge.storage import atomic_json, read_json


def default_output() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "state")))
    return base / "screen-bridge" / "runtime"


def request(output: Path, tool: str, arguments: dict | None = None, timeout: float = 40) -> dict:
    try:
        session = read_json(output.resolve() / "session.json", timeout=0.1)
        with Client(
            session["address"], family="AF_PIPE", authkey=bytes.fromhex(session["auth"])
        ) as conn:
            conn.send_bytes(json.dumps({"tool": tool, "arguments": arguments or {}}).encode())
            if not conn.poll(timeout):
                raise BridgeError("TIMEOUT", "The screen service did not reply before the timeout")
            reply = json.loads(conn.recv_bytes(32 * 1024 * 1024))
    except (AuthenticationError, OSError, EOFError, KeyError, ValueError) as exc:
        raise BridgeError(
            "NOT_RUNNING", "Run screen-bridge start before using screen tools"
        ) from exc
    if "error" in reply:
        raise BridgeError(reply["error"], reply["message"])
    return reply


def dispatch(service, tool: str, arguments: dict) -> dict:
    if tool == "get_screen":
        return service.get_screen(**arguments)
    if tool == "find_text":
        return service.find_text(**arguments)
    if tool == "get_region_image":
        metadata, data = service.get_region_image(**arguments)
        return {**metadata, "png_base64": base64.b64encode(data).decode("ascii")}
    if tool == "describe_region":
        return service.describe_region(**arguments)
    if tool in {"click", "type_text", "press_key", "scroll"}:
        return service.act(tool, **arguments)
    raise BridgeError("UNKNOWN_TOOL", f"Unknown tool: {tool}")


def serve(service) -> None:
    if sys.platform != "win32":
        raise BridgeError("UNSUPPORTED_PLATFORM", "The skill service requires Windows named pipes")
    output = service.store.root
    address = r"\\.\pipe\screen-bridge-" + content_hash(str(output).casefold().encode())
    auth = secrets.token_bytes(32)
    service.start()
    try:
        with Listener(address, family="AF_PIPE", authkey=auth) as listener:
            atomic_json(
                output / "session.json",
                {
                    "pid": os.getpid(),
                    "address": address,
                    "auth": auth.hex(),
                },
            )
            stopping = False
            while not stopping:
                try:
                    connection = listener.accept()
                except (AuthenticationError, EOFError, OSError):
                    continue
                with connection:
                    try:
                        message = json.loads(connection.recv_bytes(2 * 1024 * 1024))
                        tool, args = message["tool"], message.get("arguments", {})
                        if tool == "ping":
                            reply = {
                                "status": "running",
                                "pid": os.getpid(),
                                "state_file": str(output / "screen.json"),
                            }
                        elif tool == "stop":
                            reply = {"status": "stopping"}
                            stopping = True
                        else:
                            reply = dispatch(service, tool, args)
                    except BridgeError as exc:
                        reply = exc.payload()
                    except Exception as exc:
                        reply = {"error": "REQUEST_FAILED", "message": str(exc)}
                    try:
                        connection.send_bytes(json.dumps(reply, ensure_ascii=False).encode())
                    except (EOFError, OSError):
                        pass
    finally:
        try:
            service.stop()
        finally:
            (output / "session.json").unlink(missing_ok=True)


def start(output: Path, interval: float, ocr: str, uia: bool) -> dict:
    try:
        return request(output, "ping", timeout=2)
    except BridgeError:
        pass
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "screen_bridge",
        "serve",
        "--output",
        str(output),
        "--interval",
        str(interval),
        "--ocr",
        ocr,
    ]
    if not uia:
        command.append("--no-uia")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with (output / "service.log").open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=flags,
            close_fds=True,
        )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BridgeError("START_FAILED", f"Service exited; inspect {output / 'service.log'}")
        try:
            return request(output, "ping", timeout=2)
        except BridgeError:
            time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise BridgeError(
        "START_FAILED", f"Service startup timed out; inspect {output / 'service.log'}"
    )


def stop(output: Path) -> dict:
    import ctypes
    from ctypes import wintypes

    session = read_json(output.resolve() / "session.json", timeout=0.1)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x00100000, False, session["pid"])
    try:
        reply = request(output, "stop", timeout=5)
        if handle and kernel32.WaitForSingleObject(handle, 15000) != 0:
            raise BridgeError(
                "STOP_TIMEOUT", "Collector process has not exited; inspect service.log"
            )
    finally:
        if handle:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + 15
    while (output.resolve() / "session.json").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if (output.resolve() / "session.json").exists():
        raise BridgeError("STOP_TIMEOUT", "Collector has not stopped yet; inspect service.log")
    return {**reply, "status": "stopped"}
