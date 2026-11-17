"""Atomic local state publication; no screen content is sent to a network service."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from PIL import Image


def read_json(path: Path, timeout: float = 2) -> dict:
    """Retry Windows sharing violations during a concurrent atomic replacement."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (PermissionError, FileNotFoundError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.005)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/antivirus may briefly deny replacement while holding the old file.
        deadline = time.monotonic() + 2
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)
    finally:
        temporary.unlink(missing_ok=True)


class StateStore:
    def __init__(self, root: Path, retain: int = 5):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.images = self.root / "snapshots"
        self.images.mkdir(exist_ok=True)
        self.retain = retain
        self.lock_path = self.root / "collector.lock"
        self._lock_stream = None

    def acquire(self) -> None:
        """OS lock releases on crashes; a leftover lock file does not block restart."""
        import sys

        self._lock_stream = self.lock_path.open("a+b")
        self._lock_stream.seek(0)
        if self.lock_path.stat().st_size == 0:
            self._lock_stream.write(b"0")
            self._lock_stream.flush()
        self._lock_stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_stream.close()
            self._lock_stream = None
            raise RuntimeError(f"Another collector is using {self.root}") from None

    def release(self) -> None:
        if self._lock_stream is not None:
            self._lock_stream.close()
            self._lock_stream = None

    def write(self, state: dict, image: Image.Image | None = None) -> None:
        if image is not None:
            target = self.images / f"{state['snapshot_id']}.png"
            temporary = target.with_suffix(".tmp")
            image.save(temporary, format="PNG")
            os.replace(temporary, target)
            state["image_path"] = str(target)
        atomic_json(self.root / "screen.json", state)
        snapshots = sorted(self.images.glob("*.png"), key=lambda p: p.stat().st_mtime_ns)
        for old in snapshots[: max(0, len(snapshots) - self.retain)]:
            old.unlink(missing_ok=True)

    def load_descriptions(self) -> dict[str, str]:
        path = self.root / "descriptions.json"
        if not path.exists():
            return {}
        value = read_json(path)
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise ValueError("Invalid descriptions.json; expected a string-to-string mapping")
        return value

    def save_descriptions(self, descriptions: dict[str, str]) -> None:
        atomic_json(self.root / "descriptions.json", descriptions)
