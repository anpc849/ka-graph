# State Persistence & Checkpointing — `kagraph.checkpoint`

## Source Map

| File | Contents |
|------|----------|
| `src/kagraph/checkpoint/base.py` | `BaseCheckpointer` protocol, `InMemorySaver`, `StateSnapshot` dataclass, `_checkpoint_copy` helper |
| `src/kagraph/checkpoint/__init__.py` | Re-exports `InMemorySaver`, `StateSnapshot` |
| `src/kagraph/graph/state.py` | Checkpoint saving/loading logic in `_invoke_internal`, `_run_packets`, `_checkpoint` |
| `src/kagraph/__init__.py` | Re-exports `InMemorySaver`, `StateSnapshot` |

---

## Overview

Checkpointing enables multi-turn, resumable graph execution in KaGraph. When a `checkpointer` is provided to `StateGraph.compile()` and each call to `invoke()` / `stream()` includes a `config` with a `thread_id`, the graph **automatically saves the full state after every node execution**.

This means:
- **Multi-turn conversations** accumulate state across separate `invoke()` calls without you managing it manually.
- **Human-in-the-loop workflows** can pause mid-graph and resume later, even across process restarts (with a persistent checkpointer).
- **State history** lets you inspect, debug, or roll back to any previous checkpoint.

```
invoke() → node runs → checkpoint saved → next node runs → checkpoint saved → ...
```

---

## `BaseCheckpointer` Protocol

Any object that implements the following interface can be used as a checkpointer:

```python
class BaseCheckpointer(Protocol):
    def get(self, key: str, checkpoint_id: str | None = None) -> dict | None: ...
    def put(self, key: str, state: dict) -> dict: ...
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `get` | `(key, checkpoint_id=None) -> dict \| None` | Retrieve the latest checkpoint for `key` (the `thread_id`), or a specific one by `checkpoint_id`. Returns `None` if no checkpoint exists yet. |
| `put` | `(key, state) -> dict` | Save a checkpoint for `key`. Returns the saved dict, which must include an assigned `checkpoint_id`. |

> [!NOTE]
> `key` is always the `thread_id` string from `config['configurable']['thread_id']`.

---

## `InMemorySaver`

The built-in checkpointer. Stores all checkpoints in memory within the Python process — no external dependencies required.

```python
from kagraph import InMemorySaver

checkpointer = InMemorySaver()
app = graph.compile(checkpointer=checkpointer)
```

### Internal Storage

```python
checkpointer.storage  # dict[str, list[dict]] — thread_id -> list of checkpoint dicts
```

### Methods

| Method | Description |
|--------|-------------|
| `get(key, checkpoint_id=None)` | Returns a deep copy of the latest checkpoint, or the specific one identified by `checkpoint_id`. Returns `None` if no checkpoints exist for `key`. |
| `put(key, state)` | Appends a new checkpoint, auto-assigns a UUID `checkpoint_id`, and links `parent_checkpoint_id` to the previous checkpoint in the thread. |
| `list(key) -> list[dict]` | Returns the full checkpoint history for a thread, oldest first. |

> [!WARNING]
> `InMemorySaver` data is **lost when the process exits**. For production multi-turn applications, implement a database-backed checkpointer (see [Custom Checkpointers](#implementing-a-custom-checkpointer)).

`InMemorySaver` is **thread-safe** for concurrent access within the same process.

---

## `StateSnapshot`

`StateSnapshot` is the dataclass returned by `CompiledStateGraph.get_state()` and `get_state_history()`. It provides a complete picture of graph state at a given checkpoint.

```python
snapshot = app.get_state(config)
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `values` | `dict[str, Any]` | The current state values — all accumulated field values at this checkpoint. |
| `next` | `tuple[tuple[str, Any], ...]` | Pending `(node_name, argument)` pairs that will execute on the next step. Empty when graph is complete. |
| `config` | `dict` | The config dict used to retrieve this snapshot (includes `thread_id` and `checkpoint_id`). |
| `metadata` | `dict` | Step metadata: `step` number, `source` (e.g., `'loop'`, `'input'`), and other internal info. |
| `created_at` | `str` | ISO 8601 timestamp of when this checkpoint was saved. |
| `checkpoint_id` | `str \| None` | Unique UUID identifying this checkpoint. |
| `parent_checkpoint_id` | `str \| None` | The `checkpoint_id` of the preceding checkpoint (enables history traversal). |
| `channel_versions` | `dict[str, int]` | Monotonically increasing version counters per state channel (field). Used internally for deduplication. |
| `versions_seen` | `dict[str, dict[str, int]]` | Per-node tracking of which channel versions each node has already processed. |
| `pending_writes` | `tuple` | State writes that have been computed but not yet applied to the main state. |

---

## Usage Pattern

### Basic Multi-Turn

```python
from kagraph import InMemorySaver, StateGraph, START, END
from kagraph.messages import HumanMessage, add_messages, AnyMessage
from typing import Annotated
from typing_extensions import TypedDict

class ChatState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]

checkpointer = InMemorySaver()
app = graph.compile(checkpointer=checkpointer)

# Always pass config with thread_id to enable checkpointing
config = {'configurable': {'thread_id': 'user-session-42'}}

# Run 1 — starts fresh, saves checkpoint
result = app.invoke({'messages': [HumanMessage('Hi')]}, config=config)

# Inspect current state
snapshot = app.get_state(config)
print(snapshot.values['messages'])

# Run 2 — state accumulates from previous checkpoint
result = app.invoke({'messages': [HumanMessage('What did I ask?')]}, config=config)

# Browse history
history = app.get_state_history(config)
for snapshot in history:
    print(snapshot.checkpoint_id, snapshot.metadata)

# Manual state patch (e.g., clear messages)
app.update_state(config, {'messages': REMOVE_ALL_MESSAGES})
```

---

## Interrupt + Resume Pattern

KaGraph supports **human-in-the-loop** workflows via the `interrupt()` primitive. When a node calls `interrupt(value)`, graph execution pauses, the checkpoint is saved, and `value` is surfaced to the caller. Execution resumes when the caller passes a `Command(resume=answer)`.

```python
from kagraph import interrupt, Command, InMemorySaver

def review_node(state):
    # Pause here and ask for human approval
    decision = interrupt({'items': state['items'], 'question': 'Approve?'})
    return {'approved': decision == 'yes'}

app = graph.compile(checkpointer=InMemorySaver())
config = {'configurable': {'thread_id': 'review-1'}}

# First invoke — pauses at interrupt(), saves checkpoint
try:
    app.invoke({'items': ['a', 'b']}, config=config)
except Exception:
    pass  # GraphInterrupt is raised; checkpoint is persisted

# Human reviews the interrupt value, then resumes
result = app.invoke(Command(resume='yes'), config=config)
print(result['approved'])  # True
```

> [!IMPORTANT]
> After an interrupt, always pass `Command(resume=value)` — **not** a new input dict — to resume. Passing a new input dict starts a new execution from `START` instead of resuming.

---

## `interrupt_before` / `interrupt_after` in `compile()`

For step-by-step debugging or structured approval workflows, you can declare interrupt points at compile time rather than using `interrupt()` inside node code:

```python
app = graph.compile(
    checkpointer=InMemorySaver(),
    interrupt_before=['review_node'],    # pause BEFORE these nodes run
    interrupt_after=['fetch_node'],      # pause AFTER these nodes run
)
```

| Parameter | Behavior |
|-----------|----------|
| `interrupt_before=['node_a', 'node_b']` | Graph pauses *before* `node_a` or `node_b` executes |
| `interrupt_after=['node_a']` | Graph pauses *after* `node_a` finishes |
| `None` or `[]` | No interrupt points (normal execution) |

After an `interrupt_before` / `interrupt_after` pause, resume with an empty `Command(resume=None)` or any sentinel value your application uses:

```python
# Resume after compile-time interrupt
result = app.invoke(Command(resume=None), config=config)
```

> [!TIP]
> Use `interrupt_before` / `interrupt_after` during development to step through graph execution node-by-node and inspect state at each point via `get_state()`.

---

## Implementing a Custom Checkpointer

To persist state across process restarts (e.g., using Redis, PostgreSQL, or a file system), implement the `BaseCheckpointer` protocol:

```python
import json
import uuid
from typing import Any

class RedisCheckpointer:
    def __init__(self, redis_client):
        self.redis = redis_client

    def get(self, key: str, checkpoint_id: str | None = None) -> dict | None:
        if checkpoint_id:
            raw = self.redis.hget(f'ckpt:{key}', checkpoint_id)
        else:
            # Get the latest checkpoint ID
            latest_id = self.redis.get(f'ckpt:{key}:latest')
            if not latest_id:
                return None
            raw = self.redis.hget(f'ckpt:{key}', latest_id.decode())
        return json.loads(raw) if raw else None

    def put(self, key: str, state: dict) -> dict:
        checkpoint_id = str(uuid.uuid4())
        state = {**state, 'checkpoint_id': checkpoint_id}
        self.redis.hset(f'ckpt:{key}', checkpoint_id, json.dumps(state))
        self.redis.set(f'ckpt:{key}:latest', checkpoint_id)
        return state
```

Pass your custom checkpointer to `compile()` as usual:

```python
app = graph.compile(checkpointer=RedisCheckpointer(redis_client))
```

> [!NOTE]
> Your `put()` implementation **must** assign and return a `checkpoint_id` in the returned dict. The graph relies on this field for history linking and targeted resume.

---

## Checkpoint Lifecycle During Execution

Understanding when checkpoints are written helps reason about resume behavior:

```
invoke({'input': 'start'}, config=config)
  │
  ├─ Load latest checkpoint for thread_id (or start fresh)
  ├─ Apply input to state
  │
  ├─ Execute node_a
  │    └─ Save checkpoint  ← checkpoint_1
  ├─ Execute node_b
  │    └─ Save checkpoint  ← checkpoint_2 (parent: checkpoint_1)
  ├─ Execute node_c
  │    └─ interrupt() called → raise GraphInterrupt
  │         └─ Save checkpoint  ← checkpoint_3 (parent: checkpoint_2)
  │
  └─ Return / raise GraphInterrupt to caller

invoke(Command(resume='yes'), config=config)
  │
  ├─ Load checkpoint_3 (latest for thread_id)
  ├─ Resume node_c from interrupt point with 'yes'
  │    └─ Save checkpoint  ← checkpoint_4
  └─ Continue to END
```


