from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from kagraph._studio_config import resolve_backend_url
from kagraph.tracing.katrace import KaGraphTracer


@contextmanager
def trace(
    name: str = "kagraph_run",
    *,
    backend_url: str | None = None,
    include_agent_binary: bool = False,
    **kwargs: Any,
) -> Iterator[KaGraphTracer]:
    """Attach a KaTrace Studio tracer for the duration of a graph run."""

    tracer = KaGraphTracer(
        resolve_backend_url(backend_url),
        trace_name=name,
        include_agent_binary=include_agent_binary,
        **kwargs,
    )
    tracer.attach()
    try:
        yield tracer
    finally:
        tracer.detach()


__all__ = ["KaGraphTracer", "trace"]
