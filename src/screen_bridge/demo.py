"""Controlled visual fixture for demos and real desktop integration tests."""

from __future__ import annotations

import argparse
import ctypes
import json
import tkinter as tk
from ctypes import wintypes
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    from screen_bridge.desktop import NativeDesktop

    desktop = NativeDesktop()  # Establish physical pixels before creating the fixture.
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path)
    parser.add_argument("--ready", type=Path)
    args = parser.parse_args(argv)
    root = tk.Tk()
    root.title("Screen Bridge Test Window")
    root.geometry("760x700+140+100")
    root.configure(bg="white")
    status = tk.StringVar(value="STATUS READY")
    input_value = tk.StringVar()
    event_state = {"clicks": 0, "text": "", "scrolls": 0}

    def save() -> None:
        if args.events:
            args.events.write_text(json.dumps(event_state, ensure_ascii=False), encoding="utf-8")

    def clicked() -> None:
        event_state["clicks"] += 1
        status.set("STATUS CLICKED")
        save()

    def typed(*_) -> None:
        event_state["text"] = input_value.get()
        save()

    def scrolled(event) -> None:
        event_state["scrolls"] += 1
        status.set("STATUS SCROLLED")
        save()

    tk.Label(root, text="SCREEN BRIDGE DEMO", font=("Arial", 22), bg="white").pack(pady=16)
    tk.Label(root, text="中文读屏测试 Windows OCR", font=("Microsoft YaHei", 16), bg="white").pack()
    tk.Label(root, textvariable=status, font=("Arial", 16), bg="white").pack(pady=10)
    button = tk.Button(root, text="CLICK TARGET", command=clicked, font=("Arial", 16))
    button.pack(pady=8)
    entry = tk.Entry(root, textvariable=input_value, font=("Arial", 18), width=30, insertofftime=0)
    entry.pack(pady=10)
    input_value.trace_add("write", typed)
    canvas = tk.Canvas(root, width=600, height=180, bg="#e8f4ff", highlightthickness=0)
    canvas.pack(pady=12)
    canvas.create_oval(30, 25, 150, 145, fill="#ef4444", outline="")
    canvas.create_rectangle(220, 35, 360, 145, fill="#2563eb", outline="")
    canvas.create_polygon(440, 145, 510, 25, 570, 145, fill="#16a34a", outline="")
    canvas.create_text(300, 165, text="IMAGE REGION", font=("Arial", 12))
    tk.Label(
        root, text="Scroll anywhere in this test window", font=("Arial", 14), bg="white"
    ).pack()
    root.bind_all("<MouseWheel>", scrolled)

    def ready() -> None:
        root.lift()
        root.attributes("-topmost", True)
        root.focus_force()
        root.update_idletasks()
        user32 = desktop.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        hwnd = user32.GetAncestor(root.winfo_id(), 2)
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        attached = user32.AttachThreadInput(current_thread, foreground_thread, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            root.focus_force()
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        if args.ready:
            controls = {}
            for name, widget in (("button", button), ("entry", entry), ("canvas", canvas)):
                controls[name] = [
                    widget.winfo_rootx(),
                    widget.winfo_rooty(),
                    widget.winfo_width(),
                    widget.winfo_height(),
                ]
            args.ready.write_text(json.dumps(controls), encoding="utf-8")
        save()

    root.after(300, ready)
    root.mainloop()


if __name__ == "__main__":
    main()
