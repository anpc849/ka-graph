# Advanced Node Configuration

## Source Map

| File | Contents |
|------|----------|
| `src/kagraph/graph/_node.py` | `StateNodeSpec` and `PregelNode` dataclasses with all configuration fields |
| `src/kagraph/graph/state.py` | `StateGraph.add_node()` implementation that applies these configurations |
| `src/kagraph/types.py` | `RetryPolicy`, `TimeoutPolicy`, `CachePolicy` type definitions |

---

## Overview

`StateGraph.add_node()` accepts several optional keyword arguments beyond the node callable itself. These configuration options give you fine-grained control over retry behaviour, result caching, execution timeouts, error recovery, routing declarations, execution scheduling, state filtering, and metadata attachment — all without modifying the node function itself.

```python
graph.add_node(
    'my_node',        # node name
    my_fn,            # callable
    retry_policy=..., # optional kwargs below
    cache_policy=...,
    timeout=...,
    error_handler=...,
    destinations=...,
    defer=...,
    input_schema=...,
    metadata=...,
)
```

---

## Retry Policy

```python
retry_policy: RetryPolicy | Sequence[RetryPolicy]
```

When a node raises an exception, a `RetryPolicy` causes it to be re-executed automatically with exponential backoff. If multiple policies are provided as a sequence, KaGraph tries each policy in order with its own independent attempt counter and settings.

### `RetryPolicy` fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `initial_interval` | `float` | `1.0` | Seconds to wait before the first retry. |
| `backoff_factor` | `float` | `2.0` | Multiplier applied to the interval after each failed attempt. |
| `max_interval` | `float` | `60.0` | Upper bound on the wait interval (seconds). |
| `max_attempts` | `int` | `3` | Maximum number of total attempts (including the first). |
| `retry_on` | `tuple[type[Exception], ...]` | `(Exception,)` | Only retry on exceptions that are instances of one of these types. |

### Examples

```python
from kagraph import RetryPolicy

# Retry up to 5 times on network errors with exponential backoff
network_retry = RetryPolicy(
    initial_interval=1.0,
    backoff_factor=2.0,
    max_interval=30.0,
    max_attempts=5,
    retry_on=(ConnectionError, TimeoutError),
)

graph.add_node('api_call', call_external_api, retry_policy=network_retry)

# Multiple policies: try once for ValueError, then up to 3 times for ConnectionError
graph.add_node('robust_node', my_fn, retry_policy=[
    RetryPolicy(max_attempts=2, retry_on=(ValueError,)),
    RetryPolicy(max_attempts=3, retry_on=(ConnectionError,)),
])
```

> **Tip**: Keep `retry_on` as specific as possible. Retrying on broad `Exception` types can mask bugs and cause long delays for non-transient failures.

---

## Cache Policy

```python
cache_policy: CachePolicy
```

`CachePolicy` enables **in-process result caching** for a node. If the same input (as determined by `key_func`) is seen again within the TTL window, the cached result is returned immediately without re-executing the node function.

### `CachePolicy` fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key_func` | `Callable[[state], str \| bytes]` | `repr` | Function that maps the input state to a cache key. |
| `ttl` | `float \| None` | `None` | Cache TTL in seconds. `None` means no expiry within the process lifetime. |

### Example

```python
from kagraph import CachePolicy

# Cache retrieval results by query string for 5 minutes
graph.add_node(
    'retrieve',
    retrieval_fn,
    cache_policy=CachePolicy(
        key_func=lambda s: s['query'],
        ttl=300.0,
    ),
)
```

> **Note**: Caching is in-process and non-persistent. The cache is cleared when the Python process restarts. Do not use `cache_policy` for nodes with side effects (e.g. database writes) or whose results depend on external state that changes independently.

---

## Timeout

```python
timeout: float | timedelta | TimeoutPolicy
```

Detect slow or hung nodes. When the limit is exceeded, KaGraph raises `NodeTimeoutError`.

> **Important**: For synchronous Python callables, the timeout is **soft** — it is detected *after* the callable returns, not via a hard interrupt of the running thread. Use async node functions if you need a true hard timeout.

### Accepted forms

```python
from datetime import timedelta
from kagraph import TimeoutPolicy

# Float: seconds
graph.add_node('slow_node', my_fn, timeout=30.0)

# timedelta
graph.add_node('slow_node', my_fn, timeout=timedelta(minutes=2))

# Full policy object
graph.add_node('slow_node', my_fn, timeout=TimeoutPolicy(run_timeout=60.0))
```

### `TimeoutPolicy` fields

| Field | Type | Description |
|-------|------|-------------|
| `run_timeout` | `float` | Maximum allowed execution time in seconds. |

---

## Error Handler

```python
error_handler: Callable
```

A fallback callable that is invoked when the main node raises an unhandled exception (after all retry attempts are exhausted). The error handler receives the same `state` argument as the main node.

KaGraph automatically creates a companion node named `__error_handler__{node_name}` and wires it in. This companion node is visible in graph visualizations.

### Example

```python
def my_node(state):
    # Simulates a failure
    raise ValueError('something went wrong')

def my_error_handler(state):
    # Return a safe fallback result
    return {'error': 'Node failed, using fallback result.'}

graph.add_node('my_node', my_node, error_handler=my_error_handler)
```

> **Tip**: The error handler can access `runtime` via a keyword parameter just like a regular node:
> ```python
> def my_error_handler(state, *, runtime: Runtime):
>     runtime.write({'alert': 'my_node failed'})
>     return {'error': 'fallback'}
> ```

---

## Destinations

```python
destinations: dict[str, str] | tuple[str, ...]
```

Explicitly declares the set of nodes this node can route to. This is used by the graph **visualizer** to draw correct edges when routing logic is embedded inside the node function itself via `Command(goto=...)` rather than expressed through `add_conditional_edges`.

Without `destinations`, the visualizer cannot infer dynamic routing targets and will not draw outgoing conditional edges.

### Example

```python
from kagraph import Command

def router_fn(state):
    if state['score'] > 0.8:
        return Command(goto='node_a')
    else:
        return Command(goto='node_b')

graph.add_node(
    'router',
    router_fn,
    destinations={'path_a': 'node_a', 'path_b': 'node_b'},
)
```

When `destinations` is a `tuple`, the values are used as both keys and target node names.

---

## Defer

```python
defer: bool = False
```

When `defer=True`, the node waits until **all non-deferred nodes in the current execution step** have finished before it runs. This is the correct tool for aggregation nodes in fan-out/fan-in patterns, where you want to collect results from multiple parallel branches before combining them.

### Example

```python
graph.add_node('fetch_a', fetch_a)
graph.add_node('fetch_b', fetch_b)
graph.add_node('aggregator', aggregate_fn, defer=True)

graph.add_edge(START, 'fetch_a')
graph.add_edge(START, 'fetch_b')
graph.add_edge('fetch_a', 'aggregator')
graph.add_edge('fetch_b', 'aggregator')
```

`fetch_a` and `fetch_b` run in parallel (or concurrently). `aggregator` is deferred and only executes once both branches have completed, making `state` contain results from both.

---

## Input Schema

```python
input_schema: type[TypedDict]
```

Narrows which state fields are passed to this node. The node receives a **filtered view** of the full state containing only the keys declared in the `TypedDict`. This is useful for:

- Documenting which fields a node actually reads.
- Preventing accidental access to unintended state.
- Reducing the amount of data serialised for caching (when combined with `cache_policy`).

### Example

```python
from typing_extensions import TypedDict

class LookupInput(TypedDict):
    query: str

def lookup_fn(state: LookupInput):
    # state only contains {'query': ...}
    return {'result': f'Result for {state["query"]}'}

graph.add_node('lookup', lookup_fn, input_schema=LookupInput)
```

Even if the full state contains `{'query': 'foo', 'history': [...], 'user_id': 'abc'}`, `lookup_fn` will only receive `{'query': 'foo'}`.

---

## Metadata

```python
metadata: dict[str, Any]
```

Arbitrary key-value metadata attached to the node. This is not used by the graph executor at runtime — it exists purely for tooling, documentation generation, and tracing/observability integrations.

```python
graph.add_node('retrieve', retrieval_fn, metadata={
    'description': 'Retrieves relevant documents from the vector store',
    'version': '1.2',
    'owner': 'platform-team',
    'tags': ['rag', 'retrieval'],
})
```

Metadata is accessible via the `GraphNode.metadata` field in `KaGraphView` and is included in trace spans emitted by `KaGraphTracer`.

---

## Summary Reference

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `retry_policy` | `RetryPolicy \| list[RetryPolicy]` | `None` | Auto-retry on failure with backoff |
| `cache_policy` | `CachePolicy` | `None` | Cache results by input key |
| `timeout` | `float \| timedelta \| TimeoutPolicy` | `None` | Detect slow nodes |
| `error_handler` | `Callable` | `None` | Fallback on unhandled exceptions |
| `destinations` | `dict \| tuple` | `None` | Declare dynamic routing targets for the visualizer |
| `defer` | `bool` | `False` | Wait for all non-deferred nodes before executing |
| `input_schema` | `type[TypedDict]` | `None` | Filter state fields passed to this node |
| `metadata` | `dict[str, Any]` | `None` | Attach arbitrary metadata for tooling |


