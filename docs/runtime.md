# Runtime Context — `kagraph.runtime`

## Source Map

| File | Contents |
|------|----------|
| `src/kagraph/runtime.py` | `Runtime` dataclass, `get_runtime()`, `get_chat()`, `runtime_scope` |
| `src/kagraph/__init__.py` | Re-exports `Runtime`, `get_runtime`, `get_chat` |
| `src/kagraph/graph/state.py` | Creates and installs `Runtime` in `_invoke_internal` |

---

## Overview

`Runtime` is a frozen dataclass that is automatically injected into every node execution during a graph run. It gives node functions first-class access to the live kbench `Chat` object, a run-scoped `context` dict for passing configuration, the full `config` passed to `invoke()`, a `writer` for emitting custom streaming events, and optional pluggable state storage.

Rather than threading these concerns through `state`, you declare a `runtime` keyword-only parameter on any node function, and KaGraph injects the correct `Runtime` instance automatically.

```python
from kagraph.runtime import Runtime
```

---

## `Runtime` Dataclass

```python
@dataclass(frozen=True)
class Runtime:
    chat: Chat
    context: dict[str, Any]
    config: dict[str, Any] | None
    writer: Any
    store: Any
    previous: Any
```

`Runtime` is **frozen** — its fields cannot be reassigned after creation. This ensures that all nodes within a single graph step share the same, consistent runtime view.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `chat` | `Chat` | The active kbench `Chat` object for this graph run. |
| `context` | `dict[str, Any]` | Run-scoped values merged from `config['configurable']` and any `context={}` passed to `invoke()`. |
| `config` | `dict[str, Any] \| None` | The full config dict passed to `invoke()`. May be `None` if no config was supplied. |
| `writer` | `Any` | Callable for emitting custom stream events. `None` when not streaming. |
| `store` | `Any` | Optional pluggable external store (e.g. a vector store or key-value backend). |
| `previous` | `Any` | Previous state snapshot, if available. |

### `runtime.write(value)`

```python
runtime.write(value: Any) -> None
```

Emit a custom event on the active stream. If no writer is present (i.e. the graph was invoked with `invoke()` rather than `stream()`), this is a silent no-op — your node code does not need to guard against a missing writer.

---

## Accessing `Runtime` in Nodes

Declare a **keyword-only** `runtime: Runtime` parameter on your node function. KaGraph detects it via `inspect.signature` and injects the live `Runtime` automatically — you never construct a `Runtime` yourself.

```python
from kagraph.runtime import Runtime

def my_node(state: MyState, *, runtime: Runtime):
    # Access context values set at invoke() time
    threshold = runtime.context.get('threshold', 0.5)

    # Emit custom stream events (no-op if not streaming)
    runtime.write({'status': 'processing', 'progress': 0.5})

    # Access the live kbench Chat object
    chat = runtime.chat

    return {'result': 'done'}
```

> **Note**: The `runtime` parameter must be keyword-only (placed after `*`). KaGraph will raise a descriptive error at graph-compile time if it detects a positional `runtime` parameter.

---

## `get_runtime()`

```python
from kagraph.runtime import get_runtime

runtime: Runtime = get_runtime()
```

Module-level function that returns the `Runtime` for the **currently active** graph execution. This is useful in helper functions that are called from within a node but do not themselves receive a `runtime` parameter.

- **Raises** `RuntimeError` if called outside of a graph run (i.e. no `runtime_scope` is active).
- Used internally by `ToolNode` and `interrupt()`.

```python
from kagraph.runtime import get_runtime

def helper_function():
    # Can be called from within any node without threading `runtime` through
    runtime = get_runtime()
    runtime.write({'helper': 'called'})
```

---

## `get_chat()`

```python
from kagraph.runtime import get_chat

chat: Chat = get_chat()
```

Convenience shorthand for `get_runtime().chat`. Returns the active kbench `Chat` object for the current graph run. Raises `RuntimeError` if called outside a graph run.

---

## `runtime_scope` Context Manager

`runtime_scope` is an internal utility used by the graph executor. It sets the `ContextVar[Runtime]` for the current execution context and properly resets it on exit, so that nested graph invocations (subgraphs) each receive their own isolated `Runtime` instance.

You do not need to use `runtime_scope` directly in application code. It is documented here for completeness and for authors of custom executors.

```python
# Internal usage within the graph executor (src/kagraph/graph/state.py):
with runtime_scope(runtime_instance):
    result = node_fn(state, runtime=runtime_instance)
```

---

## Passing Context to a Graph Run

The `context` dict in `Runtime` is the primary mechanism for injecting run-time configuration without modifying state schema. It is assembled from two sources:

1. The `context={}` keyword argument passed to `app.invoke()` or `app.stream()`.
2. Fields from `config['configurable']`.

These are merged (with `context=` taking precedence on key conflicts).

### Declaring Expected Context Fields with `context_schema`

For documentation, type-checking, and IDE support, declare the expected context shape using a `TypedDict` passed to `StateGraph`:

```python
from typing_extensions import TypedDict
from kagraph import StateGraph

class AppContext(TypedDict, total=False):
    max_depth: int
    threshold: float
    debug: bool

graph = StateGraph(MyState, context_schema=AppContext)
# ... add nodes, edges ...
app = graph.compile()

result = app.invoke(
    {'question': 'hello'},
    context={'max_depth': 10, 'threshold': 0.9}
)
```

Inside any node, `runtime.context` will contain `{'max_depth': 10, 'threshold': 0.9}`:

```python
def my_node(state: MyState, *, runtime: Runtime):
    max_depth = runtime.context.get('max_depth', 5)
    debug = runtime.context.get('debug', False)
    if debug:
        runtime.write({'debug': f'max_depth={max_depth}'})
    ...
```

---

## Custom Streaming with `runtime.write()`

`runtime.write()` enables nodes to push arbitrary data onto the active event stream while the graph is running. This is useful for progress reporting, intermediate results, and debug telemetry.

```python
def streaming_node(state, *, runtime: Runtime):
    for step in range(3):
        runtime.write({'step': step, 'message': f'Processing step {step}'})
        # ... do real work here ...
    return {'result': 'complete'}
```

### Consuming Custom Events

When you invoke the graph with `app.stream(..., stream_mode='events')`, custom events emitted by `runtime.write()` appear as `'on_custom'` events in the stream:

```python
for event in app.stream(input_data, stream_mode='events'):
    if event.get('event') == 'on_custom':
        print(event['data'])  # {'step': 0, 'message': 'Processing step 0'}, ...
```

If the graph is invoked with `app.invoke()` instead of `app.stream()`, `runtime.write()` is silently ignored — no error is raised and no data is buffered.

---

## Lifecycle

The following diagram shows how `Runtime` flows through a graph execution:

```
app.invoke(state, context={...}, config={...})
    │
    ▼
graph executor builds Runtime(
    chat=active_chat,
    context=merged_context,
    config=config,
    writer=None or stream_writer,
    ...
)
    │
    ▼
runtime_scope(runtime) ──► sets ContextVar for this thread/task
    │
    ├─► node_a(state, *, runtime=runtime)   # injected by executor
    ├─► node_b(state, *, runtime=runtime)
    └─► ...
    │
    ▼
runtime_scope exits ──► ContextVar reset
```

Each nested subgraph invocation creates its own `runtime_scope`, ensuring complete isolation between graph levels.


