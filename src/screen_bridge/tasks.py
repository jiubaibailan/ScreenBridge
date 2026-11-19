"""Task policies and adapter contracts for real-time screen workflows."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class TaskPolicy:
    """Declarative permissions and thresholds shared by all task adapters."""

    name: str
    auto_execute: bool = False
    min_confidence: float = 0.75
    allowed_actions: tuple[str, ...] = ("click", "type_text", "press_key", "scroll")
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskAdapter(Protocol):
    """Optional code plug-in contract for task-specific state interpretation."""

    name: str

    def interpret(self, state: dict) -> dict:
        ...

    def verify(self, before: dict, after: dict) -> bool:
        ...


def policy_from_mapping(data: dict[str, Any]) -> TaskPolicy:
    name = str(data.get("name") or data.get("task") or "")
    if not name:
        raise ValueError("task policy requires name")
    confidence = float(data.get("min_confidence", 0.75))
    if not 0 <= confidence <= 1:
        raise ValueError("min_confidence must be between 0 and 1")
    actions = tuple(str(item) for item in data.get("allowed_actions", TaskPolicy.allowed_actions))
    return TaskPolicy(
        name=name,
        auto_execute=bool(data.get("auto_execute", False)),
        min_confidence=confidence,
        allowed_actions=actions,
        metadata=dict(data.get("metadata", {})),
    )


def load_policy(path: Path) -> TaskPolicy:
    """Load JSON policy without adding a YAML runtime dependency."""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("task policy must be a JSON object")
    return policy_from_mapping(data)


def load_plugin(path: Path) -> TaskAdapter:
    """Load a Python adapter exposing ``adapter`` or ``create_adapter``."""

    spec = importlib.util.spec_from_file_location("screen_bridge_task_plugin", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load task plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = getattr(module, "adapter", None)
    if adapter is None and hasattr(module, "create_adapter"):
        adapter = module.create_adapter()
    if adapter is None or not hasattr(adapter, "interpret") or not hasattr(adapter, "verify"):
        raise ValueError(
            "task plugin must expose adapter or create_adapter() with interpret/verify"
        )
    return adapter
