from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Generic, Hashable, NamedTuple, TypeVar


N = TypeVar("N", bound=Hashable)


@dataclass(frozen=True)
class Command:
    """Node return value that updates state and optionally routes execution."""

    goto: str | Send | list[str | Send] | None = None
    update: dict[str, Any] | None = None
    resume: Any = None
    graph: str | None = None

    PARENT = "__parent__"


@dataclass(frozen=True)
class Send(Generic[N]):
    """Route a packet of state to a specific node."""

    node: N
    arg: Any = None


class RetryPolicy(NamedTuple):
    """Best-effort retry policy for node execution."""

    initial_interval: float = 0.5
    backoff_factor: float = 2.0
    max_interval: float = 128.0
    max_attempts: int = 3
    retry_on: tuple[type[BaseException], ...] = (Exception,)


@dataclass(frozen=True)
class TimeoutPolicy:
    """Timeout configuration for a node attempt.

    Sync Python callables cannot be safely interrupted in-process, so KaGraph
    detects elapsed-time overruns after the callable returns. This is still
    useful for benchmarking slow nodes, but it is not a hard cancellation.
    """

    run_timeout: float | None = None
    idle_timeout: float | None = None

    @classmethod
    def coerce(
        cls,
        value: float | timedelta | "TimeoutPolicy" | None,
    ) -> "TimeoutPolicy | None":
        if value is None or isinstance(value, TimeoutPolicy):
            return value
        if isinstance(value, timedelta):
            return cls(run_timeout=value.total_seconds())
        return cls(run_timeout=float(value))


@dataclass(frozen=True)
class CachePolicy:
    """Best-effort in-process cache policy."""

    key_func: Callable[[Any], str | bytes] = repr
    ttl: float | None = None


@dataclass(frozen=True)
class Interrupt:
    value: Any
    when: float = 0.0


class GraphInterrupt(Exception):
    """Raised internally by interrupt()."""

    def __init__(self, value: Any):
        super().__init__("Graph execution interrupted.")
        self.value = value


def interrupt(value: Any) -> Any:
    """Request a resumable interrupt.

    If the active graph invocation was resumed with ``Command(resume=...)``,
    this returns the supplied resume value. Otherwise it raises a graph
    interrupt that checkpointed graphs can resume from later.
    """

    try:
        from kagraph.runtime import get_runtime

        runtime = get_runtime()
    except RuntimeError:
        runtime = None
    if runtime is not None:
        resume_values = runtime.context.get("__kagraph_resume_values__")
        resume_index = runtime.context.get("__kagraph_resume_index__", 0)
        if resume_values is not None and resume_index < len(resume_values):
            runtime.context["__kagraph_resume_index__"] = resume_index + 1
            return resume_values[resume_index]
    raise GraphInterrupt(Interrupt(value=value, when=time.time()))
