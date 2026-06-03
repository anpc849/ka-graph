# Core Types — `kagraph.types`

## Source Map

| File | Description |
|---|---|
| `src/kagraph/types.py` | Full implementation of `Command`, `Send`, `RetryPolicy`, `TimeoutPolicy`, `CachePolicy`, `Interrupt`, `GraphInterrupt`, and `interrupt()`. |
| `src/kagraph/__init__.py` | Re-exports all types from the top-level `kagraph` package. |
| `src/kagraph/graph/state.py` | Consumes these types during graph execution (retry loop, timeout detection, cache lookup, interrupt handling). |
| `src/kagraph/runtime.py` | Used by `interrupt()` to fetch the active context and handle resumed values. |

---

## Overview

The `kagraph.types` module defines the fundamental control-flow and configuration primitives used throughout KaGraph. These types govern how nodes communicate state changes, how execution is routed, how failures are retried, how timeouts are enforced, how results are cached, and how graphs can be paused and resumed.

All types are re-exported from the top-level `kagraph` package for convenience.

---

## `Command`

```python
from kagraph import Command
```

`@dataclass(frozen=True)`

Returned from a node to simultaneously **update state** and **route execution** to one or more target nodes in a single operation. This is the primary mechanism for combining conditional routing with state mutations.

### Fields

| Field | Type | Description |
|---|---|---|
| `goto` | `str \| Send \| list[str \| Send] \| None` | Target node name(s) or `Send` object(s) |
| `update` | `dict[str, Any] \| None` | Partial state update to apply before routing |
| `resume` | `Any` | Value returned from `interrupt()` when the graph is resumed |
| `graph` | `str \| None` | Graph name, used in subgraph routing |

### Class Constant

```python
Command.PARENT = '__parent__'
```

Pass `goto=Command.PARENT` to route execution back to the **parent graph** from within a subgraph.

### Examples

```python
from kagraph import Command, Send

# Route to a single node and update state
def route_node(state):
    return Command(
        goto='processor',
        update={'status': 'routing'},
    )

# Fan out to multiple nodes with different data packets
def fan_out_node(state):
    return Command(goto=[
        Send('worker_a', {'task': 'analyze'}),
        Send('worker_b', {'task': 'summarize'}),
    ])

# Route from a subgraph back to the parent
def finish_subgraph(state):
    return Command(goto=Command.PARENT, update={'result': state['output']})
```

> [!TIP]
> Use `Command` instead of returning a bare dict when you need routing logic that depends on the computed update — it avoids the need for a separate conditional edge.

---

## `Send`

```python
from kagraph import Send
```

`@dataclass(frozen=True)`, `Generic[N]`

Routes a specific state **packet** to a named node. Used for parallel fan-out or dynamic dispatch — each `Send` delivers an independent data payload to its target node, allowing multiple instances of the same node to run concurrently with different inputs.

### Fields

| Field | Type | Description |
|---|---|---|
| `node` | `N` (node name) | Destination node name |
| `arg` | `Any` | State or data packet passed as the node's input |

### Example

```python
from kagraph import Send

def dispatch(state):
    tasks = state['tasks']
    # Launch one 'worker' node instance per task, in parallel
    return [Send('worker', {'task': t}) for t in tasks]

graph.add_conditional_edges('dispatcher', dispatch)
```

> [!NOTE]
> `Send` is typically used inside `add_conditional_edges` callbacks or inside a `Command.goto` list. Each `Send` creates an independent execution branch.

---

## `RetryPolicy`

```python
from kagraph import RetryPolicy
```

`NamedTuple`

Configures exponential-backoff retry behavior for a node. Attach to a node via `graph.add_node(..., retry_policy=policy)`.

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `initial_interval` | `float` | `0.5` | Delay (seconds) before the first retry |
| `backoff_factor` | `float` | `2.0` | Multiplier applied to the interval after each failed attempt |
| `max_interval` | `float` | `128.0` | Maximum delay cap in seconds |
| `max_attempts` | `int` | `3` | Total number of attempts (including the first) |
| `retry_on` | `tuple[type[BaseException], ...]` | `(Exception,)` | Exception types that trigger a retry |

### Retry Interval Sequence

With defaults (`initial_interval=0.5`, `backoff_factor=2.0`, `max_interval=128.0`):

| Attempt | Delay before retry |
|---|---|
| 1 → 2 | 0.5 s |
| 2 → 3 | 1.0 s |
| 3 → 4 | 2.0 s |
| … | doubles each time, capped at 128 s |

### Example

```python
from kagraph import RetryPolicy

policy = RetryPolicy(
    initial_interval=1.0,
    backoff_factor=2.0,
    max_attempts=5,
    retry_on=(ConnectionError, TimeoutError),
)

graph.add_node('my_node', my_fn, retry_policy=policy)
```

---

## `TimeoutPolicy`

```python
from kagraph import TimeoutPolicy
```

`@dataclass(frozen=True)`

Configures time limits for a node's execution. Attach via `graph.add_node(..., timeout=...)`.

### Fields

| Field | Type | Description |
|---|---|---|
| `run_timeout` | `float \| None` | Maximum wall-clock time (seconds) the node may run |
| `idle_timeout` | `float \| None` | Maximum time the node may spend idle/blocked |

> [!WARNING]
> Synchronous Python callables **cannot be safely interrupted mid-execution**. KaGraph detects elapsed-time overruns *after* the callable returns. This makes `TimeoutPolicy` most useful for benchmarking and flagging unexpectedly slow nodes rather than hard real-time enforcement.

### `TimeoutPolicy.coerce(value)`

Accepts multiple input formats for convenience:

| Input type | Behavior |
|---|---|
| `float` | Interpreted as `run_timeout` in seconds |
| `datetime.timedelta` | Converted to seconds for `run_timeout` |
| `TimeoutPolicy` | Passed through unchanged |
| `None` | Returns `None` (no timeout) |

### Examples

```python
from datetime import timedelta
from kagraph import TimeoutPolicy

# Using a plain float
graph.add_node('fast_node', my_fn, timeout=30.0)

# Using a timedelta
graph.add_node('medium_node', my_fn, timeout=timedelta(minutes=2))

# Using the full dataclass
graph.add_node('slow_node', my_fn, timeout=TimeoutPolicy(run_timeout=60.0, idle_timeout=10.0))
```

---

## `CachePolicy`

```python
from kagraph import CachePolicy
```

`@dataclass(frozen=True)`

Enables **best-effort in-process caching** for a node. If the cache key matches a previously computed result and the TTL has not expired, the node is skipped and the cached output is returned directly.

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `key_func` | `Callable[[Any], str \| bytes]` | `repr` | Function that produces a cache key from the node's input |
| `ttl` | `float \| None` | `None` | Time-to-live in seconds; `None` means no expiry |

> [!NOTE]
> The cache is **in-process only** — it does not persist across graph restarts or separate process invocations. It is best suited for idempotent, expensive computations within a single long-running session.

### Example

```python
from kagraph import CachePolicy

# Cache results for 5 minutes, using default repr key
graph.add_node('expensive_node', my_fn, cache_policy=CachePolicy(ttl=300.0))

# Custom key function (e.g., hash only a subset of the input)
graph.add_node(
    'selective_cache',
    my_fn,
    cache_policy=CachePolicy(
        key_func=lambda state: state['query'],
        ttl=60.0,
    ),
)
```

---

## `Interrupt`

```python
from kagraph.types import Interrupt
```

`@dataclass(frozen=True)`

A data container carried inside a `GraphInterrupt` exception. Represents a single pause point created by a call to `interrupt()`.

### Fields

| Field | Type | Description |
|---|---|---|
| `value` | `Any` | The value passed to `interrupt()` — typically a question or context for the human reviewer |
| `when` | `float` | Unix timestamp at which the interrupt was created |

---

## `GraphInterrupt`

```python
from kagraph import GraphInterrupt
```

Raised by `interrupt()` to signal that a node has paused and is awaiting external input. It is **not an error** — it is a control-flow signal.

### Fields

| Field | Type | Description |
|---|---|---|
| `value` | `Interrupt` | The `Interrupt` dataclass instance |

The graph executor catches `GraphInterrupt`, saves the current state to a checkpointer, and re-raises it to the caller so the application can handle the pause (e.g., prompt a human and then resume).

> [!NOTE]
> `GraphInterrupt` lives in `kagraph.types`, not `kagraph.errors`. It is intentionally separate from the error hierarchy because it represents normal graph control flow.

---

## `interrupt()`

```python
from kagraph import interrupt

def interrupt(value: Any) -> Any: ...
```

Call `interrupt()` inside a node function to **pause execution** and await external input.

### Behavior

| Scenario | Result |
|---|---|
| Graph is being invoked for the first time | Raises `GraphInterrupt(Interrupt(value=value, when=...))` |
| Graph is being resumed via `Command(resume=x)` | Returns `x` immediately (no exception raised) |

### Example

```python
from kagraph import interrupt, Command, StateGraph

def human_review_node(state):
    # Pause and surface a question to the human
    decision = interrupt({
        'question': 'Do you approve this action?',
        'context': state,
    })
    # Execution resumes here after the human responds
    return {'decision': decision}

graph = StateGraph(...)
graph.add_node('review', human_review_node)
# ... add edges, compile ...
app = graph.compile(checkpointer=my_checkpointer)

config = {'configurable': {'thread_id': 'thread-1'}}

# First call — pauses at interrupt(), saves checkpoint
try:
    app.invoke({'data': 'some data'}, config=config)
except GraphInterrupt as e:
    print('Waiting for human input:', e.value.value['question'])

# Resume with human decision
app.invoke(Command(resume='approved'), config=config)
```

> [!IMPORTANT]
> A **checkpointer** must be configured on the compiled graph for `interrupt()` / resume to work correctly across separate process invocations. Without a checkpointer, the state is lost between calls.


