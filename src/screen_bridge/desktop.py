"""Windows capture and SendInput, in physical virtual-desktop pixel coordinates."""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image, ImageDraw

from screen_bridge.models import Box, BridgeError, content_hash


@dataclass
class Frame:
    window: dict
    image: Image.Image

    @property
    def box(self) -> Box:
        return Box.parse(self.window["capture_bbox"])

    @property
    def fingerprint(self) -> str:
        return content_hash(self.image.tobytes())

    @property
    def identity(self) -> tuple:
        return self.window["hwnd"], tuple(self.window["bbox"]), tuple(self.window["capture_bbox"])


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", INPUTUNION)]


KEYS = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "space": 0x20,
    "home": 0x24,
    "end": 0x23,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "pageup": 0x21,
    "pagedown": 0x22,
    "insert": 0x2D,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def parse_key(key: str) -> list[int]:
    parts = key.lower().split("+")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        raise BridgeError("INVALID_KEY", "Use a key or combination such as Enter or Ctrl+A")
    codes = []
    for part in parts:
        if part in KEYS:
            codes.append(KEYS[part])
        elif len(part) == 1 and part.isascii() and part.isalnum():
            codes.append(ord(part.upper()))
        else:
            raise BridgeError("INVALID_KEY", f"Unsupported key: {part}")
    if len(set(codes)) != len(codes):
        raise BridgeError("INVALID_KEY", "Duplicate keys are not allowed")
    return codes


class NativeDesktop:
    def __init__(self):
        if sys.platform != "win32":
            raise BridgeError("UNSUPPORTED_PLATFORM", "Screen capture and input require Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self.dwmapi = ctypes.WinDLL("dwmapi")
        self.dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._local = threading.local()

    def capture(self) -> Frame:
        import mss

        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            raise BridgeError("NO_WINDOW", "No foreground window is available")
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise BridgeError("WINDOW_UNAVAILABLE", "Could not read the foreground window")
        window_box = Box(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        if window_box.width <= 0 or window_box.height <= 0:
            raise BridgeError("WINDOW_UNAVAILABLE", "Foreground window has no visible area")
        if not hasattr(self._local, "capture"):
            self._local.capture = mss.MSS()
        capture = self._local.capture
        monitors = capture.monitors
        bounds = monitors[0]
        # GetWindowRect includes transparent resize margins, where background animation
        # would otherwise invalidate an unchanged foreground window.
        frame_rect = wintypes.RECT()
        capture_box = window_box
        if (
            self.dwmapi.DwmGetWindowAttribute(
                hwnd, 9, ctypes.byref(frame_rect), ctypes.sizeof(frame_rect)
            )
            == 0
        ):
            capture_box = Box(
                frame_rect.left + 1,
                frame_rect.top + 1,
                frame_rect.right - frame_rect.left - 2,
                frame_rect.bottom - frame_rect.top - 2,
            )
        visible_box = capture_box.intersect(
            Box(bounds["left"], bounds["top"], bounds["width"], bounds["height"])
        )
        if visible_box is None:
            raise BridgeError("WINDOW_UNAVAILABLE", "Foreground window is outside the desktop")
        text = ctypes.create_unicode_buffer(2048)
        self.user32.GetWindowTextW(hwnd, text, len(text))
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        shot = capture.grab(
            {
                "left": visible_box.x,
                "top": visible_box.y,
                "width": visible_box.width,
                "height": visible_box.height,
            }
        )
        if self.user32.GetForegroundWindow() != hwnd:
            raise BridgeError("WINDOW_CHANGED", "Foreground window changed during capture; retry")
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        # Windows 11 rounded corners expose the background; normalize those decorative pixels.
        if not self.user32.IsZoomed(hwnd) and visible_box == capture_box:
            mask = Image.new("L", image.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, image.width - 1, image.height - 1),
                radius=12,
                fill=255,
            )
            image = Image.composite(image, Image.new("RGB", image.size, "white"), mask)
        return Frame(
            window={
                "hwnd": int(hwnd),
                "pid": pid.value,
                "title": text.value,
                "bbox": window_box.as_list(),
                "capture_bbox": visible_box.as_list(),
            },
            image=image,
        )

    def close(self) -> None:
        if hasattr(self._local, "capture"):
            self._local.capture.close()
            del self._local.capture

    def _send(self, inputs: list[INPUT]) -> None:
        array = (INPUT * len(inputs))(*inputs)
        if self.user32.SendInput(len(array), array, ctypes.sizeof(INPUT)) != len(array):
            raise BridgeError(
                "INPUT_FAILED",
                "Windows rejected input; elevated/protected windows may be inaccessible",
            )

    def click(self, x: int, y: int, button: str = "left") -> None:
        if button not in {"left", "right"}:
            raise BridgeError("INVALID_ARGUMENT", "button must be left or right")
        if not self.user32.SetCursorPos(x, y):
            raise BridgeError("INPUT_FAILED", "Could not move the pointer")
        down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
        self._send(
            [INPUT(type=0, mi=MOUSEINPUT(dwFlags=down)), INPUT(type=0, mi=MOUSEINPUT(dwFlags=up))]
        )

    def type_text(self, text: str) -> None:
        if not text or len(text) > 10000:
            raise BridgeError(
                "INVALID_ARGUMENT", "text length must be between 1 and 10000 characters"
            )
        encoded = text.encode("utf-16-le")
        inputs = []
        for offset in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[offset : offset + 2], "little")
            inputs.extend(
                [
                    INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=0x0004)),
                    INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=0x0004 | 0x0002)),
                ]
            )
        for offset in range(0, len(inputs), 256):
            self._send(inputs[offset : offset + 256])

    def press_key(self, key: str) -> None:
        codes = parse_key(key)
        pressed = []
        try:
            for code in codes:
                self._send([INPUT(type=1, ki=KEYBDINPUT(wVk=code))])
                pressed.append(code)
        finally:
            for code in reversed(pressed):
                self._send([INPUT(type=1, ki=KEYBDINPUT(wVk=code, dwFlags=0x0002))])

    def scroll(self, x: int, y: int, direction: str, amount: int) -> None:
        if direction not in {"up", "down"} or type(amount) is not int or not 1 <= amount <= 20:
            raise BridgeError("INVALID_ARGUMENT", "direction must be up/down; amount must be 1..20")
        if not self.user32.SetCursorPos(x, y):
            raise BridgeError("INPUT_FAILED", "Could not move the pointer")
        delta = amount * 120 * (1 if direction == "up" else -1)
        self._send([INPUT(type=0, mi=MOUSEINPUT(mouseData=delta & 0xFFFFFFFF, dwFlags=0x0800))])
