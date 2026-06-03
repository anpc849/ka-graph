# Running Graphs — `CompiledStateGraph`

## Source Map

| File | Contents |
|------|----------|
| `src/kagraph/graph/state.py` | Full `CompiledStateGraph` implementation: `invoke`, `ainvoke`, `stream`, `astream`, `get_state`, `update_state`, `_run_packets`, `_execute_node` |
| `src/kagraph/checkpoint/base.py` | `InMemorySaver` and `StateSnapshot` |
| `src/kagraph/runtime.py` | `Runtime` context object injected during node execution |
| `src/kagraph/__init__.py` | Re-exports `CompiledStateGraph` |

---

## Overview

`CompiledStateGraph` is the executable graph produced by `StateGraph.compile()`. It is the runtime engine of KaGraph: it initializes state, routes packets between nodes, executes node callables (synchronously or asynchronously), manages checkpoints, and streams intermediate results.

You never instantiate `CompiledStateGraph` directly — it is always returned by `StateGraph.compile()`.

```python
app = graph.compile(checkpointer=checkpointer, name='my-graph')
# app is a CompiledStateGraph
result = app.invoke({'input': 'hello'})
```

---

## `invoke()`

```python
result = app.invoke(
    input,
    config=None,
    *,
    initial_state=None,
    chat_name='kagraph_run',
    system_instructions=None,
    session_id=None,
    user_id=None,
    context=None,
    recursion_limit=100,
)
```

Runs the graph **synchronously** from start to finish (or until an interrupt) and returns the final state.

### Return Value

A `dict` containing:
- `'chat'` — the kbench `Chat` object for the run (contains logs, messages, metadata).
- All keys from your `state_schema` (or `output_schema` if defined).

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `input` | `Any` | `None` | Graph input: a `dict` of state fields, a plain string, or a `Command` object to resume from an interrupt. |
| `config` | `dict` | `None` | Run configuration. Pass `{"configurable": {"thread_id": "..."}}` to enable checkpointing and multi-turn state. |
| `initial_state` | `dict` | `None` | Pre-populate the graph state *before* applying `input`. Useful for seeding defaults. |
| `chat_name` | `str` | `'kagraph_run'` | Name for the kbench chat session created for this run. |
| `system_instructions` | `str` | `None` | System prompt text attached to the graph's chat session. |
| `session_id` | `str` | `None` | User session ID forwarded to tracing and logging. |
| `user_id` | `str` | `None` | User ID forwarded to tracing and logging. |
| `context` | `dict` | `None` | Run-scoped context values available inside nodes via `runtime.context`. Not persisted in state. |
| `recursion_limit` | `int` | `100` | Maximum number of node execution steps before raising `GraphRecursionError`. |

### Example

```python
result = app.invoke(
    {'messages': [HumanMessage('What is KaGraph?')]},
    config={'configurable': {'thread_id': 'user-42'}},
    context={'model': 'qwen3-235b'},
    recursion_limit=50,
)
print(result['messages'][-1].content)
```

---

## `ainvoke()`

```python
result = await app.ainvoke(input, config=None, **kwargs)
```

Async version of `invoke()` with identical parameters. Use in async contexts (FastAPI endpoints, Jupyter notebooks with `asyncio`, etc.).

```python
import asyncio

async def run():
    result = await app.ainvoke({'input': 'hello'})
    return result

asyncio.run(run())
```

---

## `stream()`

```python
for chunk in app.stream(input, config=None, *, stream_mode='values', **kwargs):
    ...
```

Runs the graph synchronously and yields intermediate results as the graph executes, enabling real-time output without waiting for full completion.

### `stream_mode` Options

| Mode | Yields | Description |
|------|--------|-------------|
| `'values'` | `dict` | Full state snapshot after each node execution step. |
| `'updates'` | `dict[str, dict]` | `{node_name: state_update}` — only the delta produced by each node. |
| `'events'` | `dict` | Raw event dicts (see [Stream Events](#stream-events)). Uses a background thread for live delivery. |
| `list` / `set` of modes | `{mode: data}` | Tagged payload containing data from each requested mode simultaneously. |

### Examples

**`'updates'` mode** — see exactly what each node produced:

```python
for snapshot in app.stream(
    {'messages': [HumanMessage('Hello')]},
    stream_mode='updates',
):
    for node_name, update in snapshot.items():
        print(f'{node_name}: {update}')
```

**`'values'` mode** — inspect full state after each step:

```python
for state in app.stream({'input': 'start'}, stream_mode='values'):
    print('Current state:', state)
```

**Multiple modes** — receive both updates and events:

```python
for chunk in app.stream({'input': 'go'}, stream_mode=['updates', 'events']):
    if 'updates' in chunk:
        print('Node update:', chunk['updates'])
    if 'events' in chunk:
        print('Event:', chunk['events']['event'])
```

---

## `astream()`

```python
async for chunk in app.astream(input, config=None, *, stream_mode='values', **kwargs):
    ...
```

Async generator version of `stream()` with identical parameters. Yields the same chunk types determined by `stream_mode`.

```python
async for chunk in app.astream({'input': 'hello'}, stream_mode='updates'):
    print(chunk)
```

---

## State Management

State management methods require a `checkpointer` to be configured on the graph and a `config` dict with a `thread_id`.

### `get_state(config) -> StateSnapshot`

Returns the most recently saved `StateSnapshot` for the given thread:

```python
snapshot = app.get_state({'configurable': {'thread_id': 'thread-1'}})
print(snapshot.values)       # current state dict
print(snapshot.next)         # pending (node, arg) pairs
print(snapshot.checkpoint_id)
```

### `get_state_history(config) -> list[StateSnapshot]`

Returns the full list of checkpoints saved for the thread, ordered from most recent to oldest:

```python
history = app.get_state_history({'configurable': {'thread_id': 'thread-1'}})
for snap in history:
    print(snap.checkpoint_id, snap.metadata)
```

### `update_state(config, values, as_node=None) -> dict`

Manually patches state and saves a new checkpoint. Useful for human-in-the-loop corrections or test scaffolding:

```python
app.update_state(
    {'configurable': {'thread_id': 'thread-1'}},
    {'messages': []},    # overwrite the messages field
    as_node='human',     # attribute the write to a virtual 'human' node
)
```

---

## Config Dict Format

The `config` dict controls checkpointing and run identity:

```python
config = {
    'configurable': {
        'thread_id': 'user-123-session-1',   # identifies the conversation thread
        'checkpoint_id': 'some-uuid',         # optional: resume from a specific checkpoint
    }
}
```

> [!IMPORTANT]
> Without a `thread_id`, no checkpointing occurs even if a `checkpointer` is attached. Each `invoke()` call starts with a fresh state.

---

## `get_graph() -> KaGraphView`

```python
view = app.get_graph()
```

Returns a `KaGraphView` object containing the list of nodes and edges as structured data, suitable for visualization or inspection. Subgraph nodes are automatically expanded when their bound callable carries a `__kagraph_subgraph__` attribute.

---

## Resume After Interrupt

When a node calls `interrupt()` (or the graph is compiled with `interrupt_before`/`interrupt_after`), execution pauses and the checkpoint is saved. Resume by passing a `Command(resume=...)` as the `input` on the next call:

```python
from kagraph import Command

config = {'configurable': {'thread_id': 'review-thread'}}

# First run — pauses at interrupt()
try:
    result = app.invoke({'question': 'Approve this action?'}, config=config)
except Exception:
    pass  # state is persisted in the checkpointer

# Human reviews, then resumes with their answer
result = app.invoke(Command(resume='yes'), config=config)
print(result['approved'])
```

> [!NOTE]
> The `Command(resume=value)` object is how you feed human input back into a paused graph. The `value` is delivered to the `interrupt()` call that caused the pause.

---

## Stream Events

When using `stream_mode='events'`, the graph emits structured event dicts throughout execution:

| Event | Trigger |
|-------|---------|
| `on_graph_start` | Graph execution begins |
| `on_node_start` | A node begins executing |
| `on_node_update` | A node produces a state update |
| `on_node_end` | A node finishes |
| `on_step_start` | A new execution step begins |
| `on_step_end` | An execution step finishes |
| `on_graph_end` | Graph execution completes |
| `on_graph_error` | Graph execution fails with an error |
| `on_checkpoint` | A checkpoint is saved to the checkpointer |

Each event dict contains at minimum: `event` (event name), `name` (node or graph name), `data` (payload), and a `metadata` field.

```python
for event in app.stream({'input': 'go'}, stream_mode='events'):
    if event['event'] == 'on_node_end':
        print(f"Node '{event['name']}' finished.")
```

---

## Subgraph Invocation

A node can itself be a `CompiledStateGraph`, enabling nested graph composition. The outer graph invokes the inner graph as a standard callable. For visualization, mark a wrapper function with the `__kagraph_subgraph__` attribute so `get_graph()` can expand it:

```python
inner_app = inner_graph.compile(name='inner')

def wrapper_node(state):
    return inner_app.invoke(state)

wrapper_node.__kagraph_subgraph__ = inner_app

outer_graph.add_node('inner', wrapper_node)
```

When `outer_app.get_graph()` is called, the `inner` node is expanded to show its internal topology.

---

## Complete Example — Multi-Turn Chatbot with Checkpointing

```python
from typing import Annotated
from typing_extensions import TypedDict
from kagraph import START, END, StateGraph, InMemorySaver
from kagraph.messages import add_messages, AnyMessage, HumanMessage

class ChatState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]

def agent(state: ChatState):
    # ... call LLM with full message history ...
    reply = call_llm(state['messages'])
    return {'messages': [reply]}

graph = StateGraph(ChatState)
graph.add_node('agent', agent)
graph.add_edge(START, 'agent')
graph.add_edge('agent', END)

checkpointer = InMemorySaver()
app = graph.compile(checkpointer=checkpointer, name='chatbot')
config = {'configurable': {'thread_id': 'thread-1'}}

# Turn 1
result1 = app.invoke(
    {'messages': [HumanMessage('Hello!')]},
    config=config,
)
print(result1['messages'][-1].content)

# Turn 2 — picks up from saved state, full history available
result2 = app.invoke(
    {'messages': [HumanMessage('What did I say?')]},
    config=config,
)
print(result2['messages'][-1].content)
```


