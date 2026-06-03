from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4


class BaseCheckpointer(Protocol):
    def get(self, key: str, checkpoint_id: str | None = None) -> dict[str, Any] | None:
        ...

    def put(self, key: str, state: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class StateSnapshot:
    values: dict[str, Any]
    next: tuple[tuple[str, Any], ...] = ()
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checkpoint_id: str | None = None
    parent_checkpoint_id: str | None = None
    channel_versions: dict[str, int] = field(default_factory=dict)
    versions_seen: dict[str, dict[str, int]] = field(default_factory=dict)
    pending_writes: tuple[tuple[str, str, Any, int], ...] = ()


class InMemorySaver:
    """Simple in-memory checkpoint saver keyed by thread id."""

    def __init__(self) -> None:
        self.storage: dict[str, list[dict[str, Any]]] = {}

    def get(self, key: str, checkpoint_id: str | None = None) -> dict[str, Any] | None:
        checkpoints = self.storage.get(key)
        if not checkpoints:
            return None
        if checkpoint_id is not None:
            for checkpoint in reversed(checkpoints):
                if checkpoint.get("checkpoint_id") == checkpoint_id:
                    return _checkpoint_copy(checkpoint)
            return None
        return _checkpoint_copy(checkpoints[-1])

    def put(self, key: str, state: dict[str, Any]) -> dict[str, Any]:
        checkpoints = self.storage.setdefault(key, [])
        checkpoint = _checkpoint_copy(state)
        checkpoint.setdefault("checkpoint_id", str(uuid4()))
        if checkpoint.get("parent_checkpoint_id") is None and checkpoints:
            checkpoint["parent_checkpoint_id"] = checkpoints[-1].get("checkpoint_id")
        checkpoints.append(checkpoint)
        return _checkpoint_copy(checkpoint)

    def list(self, key: str) -> list[dict[str, Any]]:
        return _checkpoint_copy(self.storage.get(key, []))


def _checkpoint_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {_checkpoint_copy(key): _checkpoint_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_checkpoint_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_checkpoint_copy(item) for item in value)
    if isinstance(value, set):
        return {_checkpoint_copy(item) for item in value}
    try:
        return deepcopy(value)
    except Exception:
        return value
