from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Hashable


@dataclass
class BranchSpec:
    path: Callable[..., Hashable | list[Hashable]]
    ends: dict[Hashable, str] | None = None
    input_schema: type[Any] | None = None
    name: str = "condition"

    @property
    def router(self) -> Callable[..., Hashable | list[Hashable]]:
        return self.path

    @property
    def mapping(self) -> dict[Hashable, str]:
        return self.ends or {}
