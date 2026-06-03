# Prebuilt Nodes & Utilities — `kagraph.prebuilt`

## Source Map

| File | Contents |
|------|----------|
| `src/kagraph/prebuilt/tool_node.py` | `ToolNode`, `tools_condition`, and all private helpers |
| `src/kagraph/prebuilt/validation.py` | `ValidationNode` |
| `src/kagraph/prebuilt/__init__.py` | Re-exports `ToolNode`, `ValidationNode`, `tools_condition` |
| `src/kagraph/__init__.py` | Top-level re-exports |

---

## Overview

The `kagraph.prebuilt` module provides drop-in graph nodes and routing helpers for common agentic patterns, with a particular focus on tool-use (ReAct-style) workflows. Rather than writing boilerplate for every project, you can wire these ready-made components directly into your `StateGraph` to handle tool dispatch, argument validation, and conditional routing.

```python
from kagraph.prebuilt import ToolNode, ValidationNode, tools_condition
```

---

## `ToolNode`

```python
ToolNode(tools: list[Callable])
```

`ToolNode` is a callable class that acts as a graph node responsible for executing tool calls. When invoked, it reads the last message from `state['messages']`, extracts any tool call requests embedded in that message, and runs the corresponding tool functions.

### Constructor

| Parameter | Type | Description |
|-----------|------|-------------|
| `tools` | `list[Callable]` | List of Python callables to make available. Tool names are resolved from `.name`, `.__name__`, or the class name, in that order. |

### Methods

#### `__call__(state) -> dict | None`

The primary entrypoint when `ToolNode` is registered as a graph node. Internally delegates to `invoke()`.

- If `state['messages']` is empty or missing, returns `None`.
- If the last message contains no tool calls, returns `None`.
- Otherwise executes each tool call and returns a result dict.

#### `invoke(state, config=None) -> dict | None`

Explicit invocation with an optional runtime `config` dict. Tool functions that declare a `config` parameter in their signature will receive the runtime config automatically (discovered via `inspect.signature`).

**Return value:**

```python
{
    'messages': [ToolMessage(...)],       # One per tool call
    'tool_results': [ToolInvocationResult(...)]  # Structured result records
}
```

Returns `None` if there are no tool calls to execute.

#### `with_fallbacks(fallbacks, *, exception_key='error') -> ToolNode`

Returns a new `ToolNode` that catches exceptions during tool execution and tries each callable in `fallbacks` in order. The caught exception is stored in state under `exception_key`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `fallbacks` | `list[Callable]` | Fallback callables tried in sequence on failure. |
| `exception_key` | `str` | State key where the exception is stored. Default: `'error'`. |

### Tracing Events

`ToolNode` dispatches two lifecycle events picked up by `KaGraphTracer`:

- **`kagraph_tool_start`** — emitted before a tool is called.
- **`kagraph_tool_end`** — emitted after a tool returns (or raises).

### Tool Discovery

`ToolNode` uses `inspect.signature(tool)` to determine which parameters to pass. Only parameters declared in the tool's signature are forwarded — surplus state keys are filtered out. If the tool's signature includes a `config` parameter, the runtime config dict is injected automatically.

### Example

```python
from kagraph.prebuilt import ToolNode, tools_condition
from kagraph import StateGraph, START, END
from kagraph.messages import MessagesState

def search(query: str) -> str:
    """Search the web."""
    return f'Search results for: {query}'

def calculator(expression: str) -> str:
    """Evaluate a math expression."""
    return str(eval(expression))

tool_node = ToolNode([search, calculator])

# With fallback on error
tool_node_safe = ToolNode([search]).with_fallbacks(
    [lambda state: {'error': 'Search unavailable'}],
    exception_key='error'
)
```

---

## `tools_condition`

```python
tools_condition(state: dict) -> str
```

A standard routing function for ReAct-style agent loops. Inspect the last message in `state['messages']` and decide whether to route to the tool executor or terminate.

### Routing Logic

| Condition | Returns |
|-----------|---------|
| `state['messages']` is empty or missing | `END` |
| Last message has tool calls | `'tools'` |
| Last message has no tool calls | `END` |

### Usage with `add_conditional_edges`

```python
graph.add_conditional_edges(
    'agent',
    tools_condition,
    {'tools': 'tools', END: END}
)
```

This is the canonical pattern for building a ReAct loop: the agent node runs an LLM, and `tools_condition` decides whether the LLM's response should trigger tool execution or end the run.

---

## `ValidationNode`

```python
ValidationNode(tools: list[Any], format_error=None)
```

`ValidationNode` validates tool call *arguments* **before** execution using Pydantic schemas. Insert it between the agent node and `ToolNode` to catch malformed or missing arguments early and return structured error messages back to the LLM for self-correction.

### Constructor

| Parameter | Type | Description |
|-----------|------|-------------|
| `tools` | `list[Any]` | Pydantic v2 models (with `model_validate`) or v1 models (with `parse_obj`) representing each tool's argument schema. |
| `format_error` | `Callable \| None` | Optional `(exc, call_data, tool) -> str` callable for custom error message formatting. |

### Methods

#### `__call__(state) -> dict`

Reads the last message's tool calls and validates each set of arguments against the corresponding Pydantic model.

- **Success**: returns `Message('Validated tool call: <name>', is_error=False)`.
- **Failure**: returns `Message(str(exc), is_error=True)`.

Always returns a dict with `{'messages': [...]}`.

#### `invoke(state, config=None) -> dict`

Explicit invocation with an optional config dict.

### Example: Validation before execution

```python
from kagraph.prebuilt import ToolNode, ValidationNode, tools_condition
from pydantic import BaseModel

class SearchArgs(BaseModel):
    query: str

validation_node = ValidationNode([SearchArgs])
tool_node = ToolNode([search])

graph.add_node('agent', agent)
graph.add_node('validate', validation_node)
graph.add_node('tools', tool_node)

graph.add_conditional_edges('agent', tools_condition, {'tools': 'validate', END: END})
graph.add_edge('validate', 'tools')
graph.add_edge('tools', 'agent')
```

If the LLM produces a tool call with an invalid `query` field (e.g. a number instead of a string), `ValidationNode` intercepts it and feeds a descriptive `ToolMessage` with `is_error=True` back to the agent node, triggering self-correction without invoking the actual tool.

---

## Complete ReAct Wiring Example

The following is a complete, runnable ReAct-style agent that uses `ToolNode` and `tools_condition`:

```python
from typing import Annotated
from typing_extensions import TypedDict
from kagraph import START, END, StateGraph
from kagraph.messages import AnyMessage, add_messages, HumanMessage
from kagraph.prebuilt import ToolNode, tools_condition
from kagraph.prompts import invoke_llm
from kagraph.llms import load_llm

def web_search(query: str) -> str:
    """Search the web for information."""
    return f'Results for: {query}'

tools = [web_search]
llm = load_llm('qwen/qwen3-235b-a22b-instruct-2507')

class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]

def agent(state: AgentState):
    response = invoke_llm(llm, messages=state['messages'], tools=tools)
    return {'messages': [response]}

graph = StateGraph(AgentState)
graph.add_node('agent', agent)
graph.add_node('tools', ToolNode(tools))
graph.add_edge(START, 'agent')
graph.add_conditional_edges('agent', tools_condition, {'tools': 'tools', END: END})
graph.add_edge('tools', 'agent')
app = graph.compile()

result = app.invoke({'messages': [HumanMessage('What is the capital of France?')]})
```

**Execution flow:**

```
START → agent → [LLM decides to call web_search] → tools → agent → [LLM produces final answer] → END
```


