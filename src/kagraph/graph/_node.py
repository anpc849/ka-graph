from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from kagraph.types import CachePolicy, RetryPolicy, TimeoutPolicy


@dataclass
class StateNodeSpec:
    runnable: Callable[..., Any]
    metadata: dict[str, Any] | None
    input_schema: type[Any] | None = None
    retry_policy: RetryPolicy | tuple[RetryPolicy, ...] | None = None
    cache_policy: CachePolicy | None = None
    is_error_handler: bool = False
    error_handler_node: str | None = None
    ends: tuple[str, ...] | dict[str, str] | None = ()
    defer: bool = False
    timeout: TimeoutPolicy | None = None


@dataclass
class PregelNode:
    name: str
    bound: Callable[..., Any]
    triggers: list[str]
    channels: list[str] | str
    input_schema: type[Any] | None = None
    metadata: dict[str, Any] | None = None
    retry_policy: RetryPolicy | tuple[RetryPolicy, ...] | None = None
    cache_policy: CachePolicy | None = None
    is_error_handler: bool = False
    error_handler_node: str | None = None
    ends: tuple[str, ...] | dict[str, str] | None = ()
    defer: bool = False
    timeout: TimeoutPolicy | None = None
