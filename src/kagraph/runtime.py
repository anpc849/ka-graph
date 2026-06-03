from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from kaggle_benchmarks.chats import Chat


@dataclass(frozen=True)
class Runtime:
    """Run-scoped values available to nodes."""

    chat: Chat
    context: dict[str, Any]
    config: dict[str, Any] | None = None
    writer: Any = None
    store: Any = None
    previous: Any = None

    def write(self, value: Any) -> None:
        """Emit a custom stream event when the active graph invocation is streaming."""

        if self.writer is None:
            return
        self.writer(value)


_runtime_var: ContextVar[Runtime | None] = ContextVar("kagraph_runtime", default=None)


def get_runtime() -> Runtime:
    runtime = _runtime_var.get()
    if runtime is None:
        raise RuntimeError("No active KaGraph runtime.")
    return runtime


def get_chat() -> Chat:
    return get_runtime().chat


class runtime_scope:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self._token = None

    def __enter__(self) -> Runtime:
        self._token = _runtime_var.set(self.runtime)
        return self.runtime

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _runtime_var.reset(self._token)
