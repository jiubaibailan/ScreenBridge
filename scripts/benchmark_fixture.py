"""Repeatable local timings using the same controlled window as the desktop smoke test."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="screen-bridge-benchmark-") as directory:
        root = Path(directory)
        ready = root / "ready.json"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "screen_bridge.demo",
                "--ready",
                str(ready),
            ]
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Test window did not become ready")
                time.sleep(0.1)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "screen_bridge",
                    "benchmark",
                    "--samples",
                    "10",
                    "--interval",
                    "0.1",
                    "--output",
                    str(root / "state"),
                ],
                check=True,
            )
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
