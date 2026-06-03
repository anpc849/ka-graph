# Building Graphs — `StateGraph`

## Source Map

| File | Contents |
|------|----------|
| `src/kagraph/graph/state.py` | Full `StateGraph` implementation: `add_node`, `add_edge`, `add_conditional_edges`, `compile`, `validate` |
| `src/kagraph/graph/_node.py` | `StateNodeSpec` and `PregelNode` internal data classes |
| `src/kagraph/graph/_branch.py` | `BranchSpec` internal data class for conditional edges |
| `src/kagraph/__init__.py` | Re-exports `StateGraph`, `START`, `END` |

---

## Overview

`StateGraph` is the primary graph builder API in KaGraph. You define your state schema, add nodes and edges to describe the execution topology, then call `.compile()` to produce a `CompiledStateGraph` — the executable form of the graph that handles packet routing, node execution, checkpointing, and streaming.

The typical lifecycle is:

```
StateGraph(schema) → .add_node() → .add_edge() → .compile() → CompiledStateGraph
```

---

## Constructor

```python
StateGraph(
    state_schema=None,
    context_schema=None,
    *,
    input_schema=None,
    output_schema=None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `state_schema` | `TypedDict` or `dict` | `dict` | Defines all state fields for the graph. Fields annotated with `Annotated[T, reducer_fn]` use custom merge behavior; bare fields use last-write-wins. |
| `context_schema` | `TypedDict` | `None` | Optional schema for run-scoped context values. Context is injected via `invoke(context={...})` and is **not** persisted in checkpointed state. |
| `input_schema` | `type` | `state_schema` | Narrows which state fields are accepted from callers at graph entry. Defaults to the full `state_schema`. |
| `output_schema` | `type` | `state_schema` | Narrows which state fields are returned in the final result. Defaults to the full `state_schema`. |

---

## State Schema Design

The `state_schema` is the central data contract for your graph. Every node reads from and writes partial updates back to this schema.

```python
from typing import Annotated
from typing_extensions import TypedDict
from kagraph.messages import add_messages, AnyMessage
import operator

class MyState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # append-merge reducer
    count: Annotated[int, operator.add]                  # numeric accumulator
    result: str                                           # last-write wins
```

### Reducer Functions

When a field is annotated with `Annotated[T, reducer_fn]`, the reducer function is called to **merge** incoming values with the existing state instead of replacing them outright.

| Annotation | Behavior |
|-----------|----------|
| `Annotated[list[AnyMessage], add_messages]` | Appends new messages, deduplicates by ID |
| `Annotated[int, operator.add]` | Adds the new value to the existing integer |
| `Annotated[list[T], operator.add]` | Concatenates lists |
| *(bare field, no annotation)* | Last-write wins — the new value replaces the old one |

> [!TIP]
> Use `total=False` in your `TypedDict` so nodes don't need to return every key on every call — only the fields they update.

---

## `add_node()`

```python
graph.add_node(
    node,
    action=None,
    *,
    defer=False,
    metadata=None,
    input_schema=None,
    retry_policy=None,
    cache_policy=None,
    error_handler=None,
    destinations=None,
    timeout=None,
)
```

Registers an executable node in the graph. Returns `self` for chaining.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `node` | `str` or `Callable` | *(required)* | The node name (string) or a callable whose `__name__` is used as the name. |
| `action` | `Callable` | `None` | The node's callable when `node` is a string. If `node` is a callable, this is inferred automatically. |
| `defer` | `bool` | `False` | When `True`, this node waits until all non-deferred nodes in the same execution step have finished before running. |
| `metadata` | `dict` | `None` | Arbitrary metadata attached to the node for tooling or visualization. |
| `input_schema` | `type` | `state_schema` | Narrows which state fields are passed to this specific node's callable. |
| `retry_policy` | `RetryPolicy \| Sequence[RetryPolicy]` | `None` | Retry strategy applied when the node raises an exception. |
| `cache_policy` | `CachePolicy` | `None` | Caches node results; if a matching cache entry exists, the node is skipped. |
| `error_handler` | `Callable` | `None` | Fallback callable invoked if the node raises and all retries are exhausted. |
| `destinations` | `dict[str, str] \| tuple[str, ...]` | `None` | Explicitly declares which nodes this node may route to (used for validation and visualization). |
| `timeout` | `float \| TimeoutPolicy` | `None` | Maximum time (seconds) or policy for node execution. |

### Node Callable Signatures

Nodes are plain Python functions (sync or async). They receive the current state and must return a `dict` of partial state updates, a `Command`, or `None`.

```python
# Basic — receives state, returns partial update
def my_node(state: StateType) -> dict:
    return {'result': 'done'}

# With runtime access — inject the Runtime object for metadata, context, etc.
def my_node(state: StateType, *, runtime: Runtime) -> dict:
    ctx = runtime.context
    return {'result': ctx['model']}

# Async — fully supported
async def my_node(state: StateType) -> dict:
    result = await some_async_call(state['input'])
    return {'result': result}
```

> [!NOTE]
> Returning `None` from a node is valid and means "no state update." The graph continues routing normally.

### Chaining Example

```python
graph = (
    StateGraph(MyState)
    .add_node('fetch', fetch_data)
    .add_node('process', process_data, retry_policy=RetryPolicy(max_attempts=3))
    .add_node('store', store_result, defer=True)
)
```

---

## `add_edge()`

```python
graph.add_edge(start_key, end_key)
```

Adds an **unconditional edge** from `start_key` to `end_key`. The target node is always activated when the source completes. Returns `self`.

### Fan-in (Waiting Edges)

When `start_key` is a **list or tuple** of node names, the edge becomes a *waiting edge*: it only fires when **all** listed sources have completed in the current step.

```python
graph.add_edge(START, 'my_node')           # entry point
graph.add_edge('my_node', END)             # exit point
graph.add_edge(['fetch_a', 'fetch_b'], 'merge_node')  # fan-in: wait for BOTH
```

> [!IMPORTANT]
> `START` and `END` are special sentinel constants exported from `kagraph`. Every graph must have at least one edge from `START` and at least one path reaching `END`.

---

## `add_conditional_edges()`

```python
graph.add_conditional_edges(source, path, path_map=None)
```

Adds a **conditional edge** where the next node(s) are determined at runtime by a routing function. Returns `self`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `source` | `str` | The node whose completion triggers the routing decision. |
| `path` | `Callable` | A function `(state) -> str | list[str]` that returns node name(s) or `END`. |
| `path_map` | `dict[str, str] \| list[str]` | Optional mapping from `path` return values to actual node names. |

### Example

```python
def should_continue(state):
    if len(state['messages']) > 10:
        return 'end'
    return 'continue'

graph.add_conditional_edges(
    'agent',
    should_continue,
    {'continue': 'tools', 'end': END}
)
```

The `path` function can also return a **list** of node names to fan-out (activate multiple nodes in parallel):

```python
def fan_out_router(state):
    return ['branch_a', 'branch_b']  # both nodes run in the next step

graph.add_conditional_edges('dispatcher', fan_out_router)
```

---

## `set_entry_point()` / `set_finish_point()`

Convenience aliases:

```python
graph.set_entry_point('first_node')   # equivalent to graph.add_edge(START, 'first_node')
graph.set_finish_point('last_node')   # equivalent to graph.add_edge('last_node', END)
```

---

## `add_sequence()`

```python
graph.add_sequence(nodes)
```

Convenience method that adds a list of nodes and **automatically connects them in a linear chain** — each node's output edges to the next.

`nodes` is a list of:
- Callables — name inferred from `__name__`
- `(name, callable)` tuples — explicit name

```python
graph.add_sequence([preprocess, analyze, summarize])
# Equivalent to:
# graph.add_node('preprocess', preprocess)
# graph.add_node('analyze', analyze)
# graph.add_node('summarize', summarize)
# graph.add_edge('preprocess', 'analyze')
# graph.add_edge('analyze', 'summarize')
```

> [!TIP]
> `add_sequence()` does **not** automatically add edges from `START` to the first node or from the last node to `END`. You still need to set those explicitly.

---

## `compile()`

```python
app = graph.compile(
    checkpointer=None,
    *,
    interrupt_before=None,
    interrupt_after=None,
    debug=False,
    name=None,
    auto_log_to_chat=False,
)
```

Validates the graph and produces a `CompiledStateGraph` ready for execution.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `checkpointer` | `BaseCheckpointer` | `None` | Enables multi-turn state persistence. When set, state is saved after each node. |
| `interrupt_before` | `list[str] \| None` | `None` | Node name(s) to pause *before* executing. Pass `None` or `[]` to disable. |
| `interrupt_after` | `list[str] \| None` | `None` | Node name(s) to pause *after* executing. |
| `debug` | `bool` | `False` | Enables verbose internal logging. |
| `name` | `str` | `'KaGraph'` | Graph name used in tracing, visualization, and chat session naming. |
| `auto_log_to_chat` | `bool` | `False` | Automatically logs node outputs to the kbench chat session. |

`compile()` internally calls `validate()` and raises immediately if the graph topology is invalid.

---

## `validate()`

```python
graph.validate(interrupt=None)
```

Performs structural validation of the graph. Called automatically by `compile()`, but can be called independently for early error detection.

Checks performed:

- At least one node is defined.
- `START` has at least one outgoing edge.
- All edge source and destination node names exist.
- All nodes are reachable from `START`.
- `END` is reachable from at least one node.
- No unconditional cycles exist in the graph.

Raises `InvalidGraphError` or `CycleError` on failure.

---

## Complete Example — Simple Sequential Pipeline

```python
from typing_extensions import TypedDict
from kagraph import START, END, StateGraph
from kagraph.llms import load_llm
from kagraph.prompts import prompt_llm

class PipelineState(TypedDict, total=False):
    input: str
    analysis: str
    summary: str

llm = load_llm('qwen/qwen3-235b-a22b-instruct-2507')

def analyze(state: PipelineState):
    result = prompt_llm(llm, f'Analyze: {state["input"]}')
    return {'analysis': result}

def summarize(state: PipelineState):
    result = prompt_llm(llm, f'Summarize: {state["analysis"]}')
    return {'summary': result}

graph = StateGraph(PipelineState)
graph.add_node('analyze', analyze)
graph.add_node('summarize', summarize)
graph.add_edge(START, 'analyze')
graph.add_edge('analyze', 'summarize')
graph.add_edge('summarize', END)

app = graph.compile(name='pipeline')
result = app.invoke({'input': 'KaGraph is a graph-based agent framework.'})
print(result['summary'])
```


