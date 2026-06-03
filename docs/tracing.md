# KaTrace Tracing System — `kagraph.tracing`

## Source Map

| Source File | Description |
|---|---|
| `src/kagraph/tracing/katrace.py` | Full `KaGraphTracer` implementation: event handlers, span management, HTTP transport layer, serialization, payload truncation, background thread. |
| `src/kagraph/tracing/__init__.py` | `trace()` context manager and public re-exports (`KaGraphTracer`, `trace`). |

---

## Overview

KaGraph ships with a built-in observability layer called **KaTrace Studio**. The `KaGraphTracer` class hooks into `kaggle_benchmarks.events.manager` to capture every lifecycle event emitted during a graph run and forward them asynchronously to the KaTrace Studio backend (a FastAPI server backed by SQLite).

Events are **batched** and delivered in a **background daemon thread**, so tracing adds minimal latency to your agent runs. The companion web UI — the Studio — lets you inspect traces, browse events, view LLM generation costs, replay agents with modified inputs, and conduct multi-turn conversations with replayed agents.

```
KaGraph Agent
     │
     │  emit events via kbench event manager
     ▼
KaGraphTracer
     │
     │  background thread, batched HTTP POST
     ▼
KaTrace Studio Backend (FastAPI :8000)
     │
     │  REST API
     ▼
KaTrace Studio Frontend (Next.js :3000)
```

---

## Quick Start — `trace()` Context Manager

The primary API for enabling tracing is the `trace()` context manager exported from `kagraph.tracing`.

```python
from kagraph.tracing import trace

with trace('MyAgent', include_agent_binary=True):
    result = app.invoke({'question': 'What is KaGraph?'})
# All events are flushed after the context exits
```

The context manager:
1. Instantiates a `KaGraphTracer` with the given arguments.
2. Calls `tracer.attach()` on entry — registers event handlers with the kbench event manager.
3. **Yields** the `KaGraphTracer` instance (usable as the `as` target).
4. Calls `tracer.detach()` on exit — unregisters handlers and flushes the delivery queue.

### Signature

```python
def trace(
    name: str,
    *,
    backend_url: str | None = None,
    include_agent_binary: bool = False,
    **kwargs,
) -> contextlib.AbstractContextManager[KaGraphTracer]:
    ...
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | *(required)* | Trace name shown in the Studio UI |
| `backend_url` | `str \| None` | `None` | URL of the KaTrace backend. Falls back to `KATRACE_BACKEND_URL`, `KAGRAPH_STUDIO_BACKEND_URL`, the Studio runtime file, then `http://127.0.0.1:8000` |
| `include_agent_binary` | `bool` | `False` | If `True`, pickles the compiled graph with `cloudpickle` and stores it in the `Trace` record for Studio replay |
| `**kwargs` | | | All remaining keyword arguments are forwarded directly to the `KaGraphTracer` constructor |

### Accessing the Tracer Inside the Block

```python
from kagraph.tracing import trace

with trace('MyAgent', backend_url='http://studio:8000') as tracer:
    result = app.invoke({'question': 'Hello'})
    print(f"Tracing to: {tracer.backend_url}")
```

---

## `KaGraphTracer` — Full Constructor Reference

For advanced usage, you can instantiate `KaGraphTracer` directly and manage `attach()`/`detach()` yourself.

```python
from kagraph.tracing import KaGraphTracer

tracer = KaGraphTracer(
    backend_url='http://my-studio:8000',
    trace_name='production_agent',
    include_state=False,
    include_agent_binary=True,
    max_payload_bytes=100_000,
    batch_size=50,
)
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `backend_url` | `str` | `'http://127.0.0.1:8000'` | KaTrace Studio backend URL |
| `trace_name` | `str` | `'kagraph_run'` | Name label shown in the Studio UI |
| `include_state` | `bool` | `True` | Include full state dicts in captured events |
| `include_messages` | `bool` | `True` | Include LLM message content in captured events |
| `include_checkpoints` | `bool` | `True` | Include checkpoint data in captured events |
| `include_agent_binary` | `bool` | `False` | Serialize the compiled graph with `cloudpickle` and store in the trace for replay |
| `include_tool_signatures` | `bool` | `False` | Include tool function signatures in metadata |
| `agent_factory` | `str \| dict \| None` | `None` | Alternative to `agent_binary`: an import path string or dict describing how to reconstruct the agent programmatically |
| `max_payload_bytes` | `int` | `250_000` | Maximum bytes per HTTP payload before truncation kicks in |
| `batch_size` | `int` | `25` | Number of events included in each HTTP batch request |
| `request_timeout` | `float` | `10.0` | Per-request HTTP timeout in seconds |
| `max_retries` | `int` | `2` | Number of HTTP retry attempts on failure |

---

## Lifecycle Events Captured

`KaGraphTracer` listens for all events emitted by `kaggle_benchmarks.events.manager`. Each kbench event is translated into one or more Studio API calls:

| kbench Event | Studio Event | Description |
|---|---|---|
| `kagraph_invoke_start` | `on_graph_start` | Graph run begins; creates the `Trace` record in the backend |
| `kagraph_invoke_end` | `on_graph_end` | Graph run completes successfully; closes the `Trace` record |
| `kagraph_invoke_error` | `on_graph_error` | Graph run raised an exception; marks trace `ERROR` |
| `kagraph_step_start` | `on_step_start` | Execution step begins |
| `kagraph_step_end` | `on_step_end` | Execution step ends |
| `kagraph_node_start` | `on_node_start` + span | Node begins; creates a `Span` record |
| `kagraph_node_update` | `on_node_update` | Node produces a partial state update |
| `kagraph_node_end` | `on_node_end` + span update | Node finishes; closes the `Span` record |
| `kagraph_checkpoint` | `on_checkpoint` | Checkpoint saved to the checkpointer |
| `kagraph_tool_start` | `on_tool_start` + span | Tool invocation begins; creates a child `Span` |
| `kagraph_tool_end` | `on_tool_end` + span update | Tool invocation ends; closes child span |
| `new_message` (chat) | `on_message` + generation | LLM response received; creates a `Generation` record |
| `start_streaming` | `on_chat_model_start` | LLM streaming begins |
| `new_chunk` | `on_chat_model_stream` | Streaming token chunk received |
| `new_tool_call` | `on_tool_stream` | Tool-call stream chunk received |

All events are also recorded as raw `TraceEvent` rows in the database, preserving the full sequence for later inspection.

---

## Span Hierarchy

Each `Span` record carries a `parent_id` field that enables the Studio to render a nested execution tree:

```
Trace
  └── Graph run (root)
        └── Step 0
              └── Node span (e.g. 'agent')
                    └── Generation (LLM call)
                    └── Tool span (e.g. 'web_search')
              └── Node span (e.g. 'tools')
        └── Step 1
              └── Node span (...)
        └── Subgraph run (nested graph)
              └── Step 0
                    └── Node span (...)
```

The tracer maintains an internal **graph context stack** that grows when a nested `invoke()` enters a subgraph and shrinks when it exits. This allows events from inner graphs to be correctly attributed to their subgraph span rather than the root graph.

---

## Subgraph Tracing

When a node calls an inner compiled graph (a subgraph), the nested `invoke()` emits its own `kagraph_invoke_start` / `kagraph_invoke_end` events. The tracer detects these and emits `on_subgraph_start` / `on_subgraph_end` Studio events, creating a nested span hierarchy that reflects the actual call structure.

---

## Async Event Delivery

Events captured by the tracer are placed into an in-memory queue. A **background daemon thread** continuously drains the queue, batching events into HTTP requests and POSTing them to the Studio backend.

Key properties of this design:

- **Non-blocking**: the agent thread is never stalled waiting for HTTP responses.
- **Batching**: up to `batch_size` events are sent per request, reducing overhead.
- **Retries**: failed requests are retried up to `max_retries` times before being dropped.
- **Flush on exit**: when the `trace()` context manager exits (or `detach()` is called), the queue is drained synchronously before returning. This guarantees all events are delivered.

### Manual Flush

```python
tracer.flush()  # Blocks until the delivery queue is empty
```

---

## Payload Size Management

For large state objects or long message histories, payloads can grow very large. When a serialized payload exceeds `max_payload_bytes`, the tracer replaces the oversized field with a compact truncation marker:

```python
{
    '__truncated__': True,
    'type': 'dict',       # original Python type
    'bytes': 512000       # original serialized size
}
```

This prevents accidentally sending multi-megabyte HTTP requests while still recording that data was present.

---

## Agent Binary for Replay

When `include_agent_binary=True`, the tracer serializes the **compiled graph object** using `cloudpickle` immediately after the graph run starts. The binary blob is stored in the `agent_binary` column of the `Trace` record.

The Studio can later:
1. Deserialize the binary with `cloudpickle.loads()`.
2. Reconstruct the live graph object.
3. Call `invoke()` on it with new inputs provided via the Playground UI.
4. Stream the replay events back to the browser in real-time.

> **Note**: `cloudpickle` can serialize most Python closures and lambda functions, but some objects (e.g., database connections, open file handles) may not survive pickling. Test replay in your environment before relying on it in production.

---

## `agent_factory` — Alternative to Binary Pickling

If pickling the compiled graph is impractical, pass `agent_factory` instead:

```python
# String form: an importable factory function
tracer = KaGraphTracer(
    agent_factory='mypackage.agents.build_react_agent',
)

# Dict form: structured description for the Studio
tracer = KaGraphTracer(
    agent_factory={
        'module': 'mypackage.agents',
        'function': 'build_react_agent',
        'kwargs': {'model': 'gpt-4o'},
    },
)
```

The Studio uses the factory information to recreate the agent without needing a pickled binary.

---

## Advanced Manual Usage

For long-running services where you want to control the attach/detach lifecycle explicitly:

```python
from kagraph.tracing import KaGraphTracer

tracer = KaGraphTracer(
    backend_url='http://my-studio:8000',
    trace_name='production_agent',
    include_state=False,        # Omit state dicts to reduce payload size
    include_messages=True,
    include_agent_binary=True,
    max_payload_bytes=100_000,
    batch_size=50,
    request_timeout=15.0,
    max_retries=3,
)

tracer.attach()   # Start listening for events
try:
    result = app.invoke(inputs)
finally:
    tracer.detach()  # Unregisters handlers and flushes the queue
```

You can also attach and detach across multiple invocations, though each `invoke()` will create a separate `Trace` record in the Studio.

