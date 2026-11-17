"""Small, JSON-friendly screen geometry and error contracts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def payload(self) -> dict:
        return {"error": self.code, "message": str(self)}


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def content_hash(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def parse(cls, values: list[int] | tuple[int, ...]) -> Box:
        if len(values) != 4 or any(type(v) is not int for v in values):
            raise BridgeError("INVALID_BOX", "bbox must contain four integers: x, y, width, height")
        if values[2] <= 0 or values[3] <= 0:
            raise BridgeError("INVALID_BOX", "bbox width and height must be positive")
        return cls(*values)

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.width, self.height]

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def intersect(self, other: Box) -> Box | None:
        x, y = max(self.x, other.x), max(self.y, other.y)
        right, bottom = min(self.right, other.right), min(self.bottom, other.bottom)
        return Box(x, y, right - x, bottom - y) if right > x and bottom > y else None

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def crop_tuple(self, origin: Box) -> tuple[int, int, int, int]:
        return self.x - origin.x, self.y - origin.y, self.right - origin.x, self.bottom - origin.y


def element(text: str, box: Box, role: str, source: str, **extra) -> dict:
    key = f"{source}|{role}|{text}|{box.as_list()}".encode()
    return {
        "id": "el_" + content_hash(key)[:16],
        "text": text,
        "role": role,
        "bbox": box.as_list(),
        "source": source,
        **extra,
    }


def finite_number(value: float, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BridgeError("INVALID_ARGUMENT", f"{name} must be a finite number")
    return round(value)
