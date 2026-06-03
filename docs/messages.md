# Messages & Message Types — `kagraph.messages`

## Source Map

| File | Description |
|---|---|
| `src/kagraph/messages.py` | Complete implementation of all message factories, `add_messages`, `MessagesState`, `coerce_messages`, `coerce_message`, content block handling, and `_decorate_message`. |
| `src/kagraph/__init__.py` | Re-exports all public message symbols from the top-level `kagraph` package. |
| `src/kagraph/graph/message.py` | Re-exports for the `kagraph.graph` subpackage. |
| `src/kagraph/prompts.py` | Uses message types for LLM generation (`invoke_llm`, `ChatPrompt`). |
| `src/kagraph/graph/state.py` | Utilizes `MessagesState` and the `add_messages` reducer to accumulate message histories. |

---

## Overview

KaGraph uses `kaggle_benchmarks.messages.Message` as its native message type. The `AnyMessage` type alias is defined as:

```python
AnyMessage = kaggle_benchmarks.messages.Message
```

All message-related utilities — factories, reducers, and coercion helpers — live in `kagraph.messages` and are also re-exported from the top-level `kagraph` package.

---

## Type Alias

```python
from kagraph.messages import AnyMessage
# equivalent to:
from kaggle_benchmarks.messages import Message as AnyMessage
```

---

## Role-to-Actor Mapping (Internal)

Internally, KaGraph maps message role strings to actor objects. This mapping is used when constructing `Message` instances:

| Role string | Actor |
|---|---|
| `'user'` / `'human'` | `actors.user` |
| `'assistant'` / `'ai'` | `actors.Actor(name='assistant', role='assistant')` |
| `'system'` | `actors.system` |
| `'developer'` | `actors.Actor(name='developer', role='developer')` |
| `'tool'` | `actors.Tool()` |

> [!NOTE]
> This mapping is internal — you do not need to import or use actors directly. Use the factory functions or role strings described below.

---

## Message Factory Functions

All factory functions return an `AnyMessage` instance.

### Summary Table

| Function | Role | Notes |
|---|---|---|
| `make_message(role, content, *, name, id, tool_calls, tool_call_id, additional_kwargs)` | any | Low-level factory; all others delegate to this |
| `HumanMessage(content, **kwargs)` | `'user'` | User input messages |
| `AIMessage(content, **kwargs)` | `'assistant'` | Model response messages |
| `SystemMessage(content, **kwargs)` | `'system'` | System-level instructions |
| `DeveloperMessage(content, **kwargs)` | `'developer'` | Developer role messages |
| `ToolMessage(content, **kwargs)` | `'tool'` | Tool execution results |
| `ImageMessage(image: ImageContent, **kwargs)` | `'user'` | Image-carrying messages |

### `make_message`

The low-level constructor. All high-level factories are thin wrappers around this function.

```python
from kagraph.messages import make_message

msg = make_message(
    role='user',
    content='Hello, world!',
    name='Alice',          # optional sender name override
    id='msg-001',          # optional message ID (used by add_messages for replacement)
    tool_calls=[...],      # list of tool call dicts (for assistant messages)
    tool_call_id='tc-1',   # links a ToolMessage back to a specific tool call
    additional_kwargs={},  # extra metadata stored in _meta
)
```

**Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `role` | `str` | One of `'user'`, `'assistant'`, `'system'`, `'developer'`, `'tool'` |
| `content` | `Any` | Message content — plain text, `ImageContent`, dict, etc. |
| `name` | `str \| None` | Optional sender name override |
| `id` | `str \| None` | Optional message ID; used by `add_messages` for in-place replacement |
| `tool_calls` | `list \| None` | List of tool call dicts (typically for assistant messages) |
| `tool_call_id` | `str \| None` | Links a `ToolMessage` to its originating tool call |
| `additional_kwargs` | `dict \| None` | Extra metadata stored in `_meta` |

### High-Level Constructors

```python
from kagraph.messages import (
    HumanMessage, AIMessage, SystemMessage,
    DeveloperMessage, ToolMessage, ImageMessage,
)

# User input
user_msg = HumanMessage('What is the capital of France?')

# Model response
ai_msg = AIMessage('The capital of France is Paris.')

# System instructions
sys_msg = SystemMessage('You are a helpful geography assistant.')

# Developer role
dev_msg = DeveloperMessage('Enable verbose reasoning mode.')

# Tool execution result
tool_msg = ToolMessage('42', tool_call_id='tc-abc123')

# Image-carrying message
from kaggle_benchmarks.messages import ImageContent
img_msg = ImageMessage(image=ImageContent(url='https://example.com/photo.jpg'))
```

---

## `MessagesState`

A ready-made [`TypedDict`](https://docs.python.org/3/library/typing.html#typing.TypedDict) for graphs that accumulate a conversation history:

```python
from typing import Annotated
from kagraph.messages import AnyMessage, add_messages

class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

Use `MessagesState` as your `state_schema` to get automatic message merging (append / replace-by-ID / clear) whenever a node returns a `messages` update:

```python
from kagraph import StateGraph
from kagraph.messages import MessagesState

graph = StateGraph(state_schema=MessagesState)
```

---

## `add_messages` Reducer

```python
from kagraph.messages import add_messages
```

`add_messages(left: list[AnyMessage], right: list[AnyMessage] | str) -> list[AnyMessage]`

This is the reducer registered via `Annotated` in `MessagesState`. It is called automatically by the graph executor whenever a node returns a `messages` key.

### Behavior

| Scenario | Result |
|---|---|
| Normal (no ID overlap) | Appends all messages from `right` to `left` |
| Message in `right` has same `id` as one in `left` | Replaces the existing message **in place** (preserving order) |
| `right == REMOVE_ALL_MESSAGES` | Returns `[]` — clears the entire history |

### Examples

```python
from kagraph.messages import HumanMessage, AIMessage, add_messages, REMOVE_ALL_MESSAGES

user_msg = HumanMessage('What is 2+2?')
ai_msg   = AIMessage('The answer is 4.')

# --- Append ---
conversation = add_messages([], [user_msg, ai_msg])
# → [user_msg, ai_msg]

# --- In-place replacement by ID ---
draft_v1 = HumanMessage('Draft 1',          id='msg-1')
draft_v2 = HumanMessage('Draft 2 (revised)', id='msg-1')
result = add_messages([draft_v1], [draft_v2])
# → [draft_v2]  (replaced in place, order preserved)

# --- Clear all messages ---
empty = add_messages([user_msg, ai_msg], REMOVE_ALL_MESSAGES)
# → []
```

---

## `REMOVE_ALL_MESSAGES` Sentinel

```python
from kagraph.messages import REMOVE_ALL_MESSAGES

REMOVE_ALL_MESSAGES = '__remove_all__'
```

Return this sentinel as the value of `messages` from any node to clear the entire conversation history:

```python
def reset_node(state):
    return {'messages': REMOVE_ALL_MESSAGES}
```

> [!CAUTION]
> This irreversibly clears all accumulated messages in the current execution context. Use only when you intentionally want to start a fresh conversation within the same graph run.

---

## `coerce_messages`

```python
from kagraph.messages import coerce_messages

def coerce_messages(value) -> list[AnyMessage]: ...
```

Converts virtually any input representation into a list of `AnyMessage` objects. Useful when your graph receives data from external systems or mixed-format sources.

### Conversion Rules

| Input type | Result |
|---|---|
| `None` | `[]` |
| `Message` (already an `AnyMessage`) | `[message]` |
| `LLMResponse` | `[AIMessage(content, tool_calls=..., reasoning_traces=...)]` |
| `(role, content)` tuple | `[make_message(role, content)]` |
| `{'role': ..., 'content': ...}` dict | Single message; content blocks list supported |
| `list` of any of the above | Flattened list of messages |
| Object with `.content` attribute | Message using `.role` or `.type` attribute |
| Anything else | `[HumanMessage(str(value))]` |

### Content Block Handling

When `content` is a list of block dicts, each block is processed individually:

| Block type | Result |
|---|---|
| `{"type": "text", "text": "..."}` | Plain text message |
| `{"type": "image_url", "image_url": {"url": "..."}}` | Image message (data URIs decoded, remote URLs referenced) |

### Example

```python
from kagraph.messages import coerce_messages, AIMessage

ai_msg = AIMessage('Hi there!')

msgs = coerce_messages([
    ('user', 'Hello'),
    {'role': 'assistant', 'content': 'Hi there!'},
    ai_msg,
    [
        {'type': 'text', 'text': 'What is this?'},
        {'type': 'image_url', 'image_url': {'url': 'https://example.com/img.png'}},
    ],
])
# → list of AnyMessage objects
```

---

## `coerce_message`

```python
from kagraph.messages import coerce_message

def coerce_message(value) -> AnyMessage: ...
```

Identical to `coerce_messages`, but asserts that exactly **one** message is produced. Raises an assertion error if zero or more than one message results.

```python
single = coerce_message(('user', 'Hello'))
# → HumanMessage('Hello')
```

---

## Message Decoration

All messages produced by KaGraph factories are decorated with the following attributes for LangChain compatibility:

| Attribute | Example value | Description |
|---|---|---|
| `.type` | `'human'`, `'ai'`, `'system'` | String type tag |
| `.role` | `'user'`, `'assistant'`, `'system'` | Role string |
| `.additional_kwargs` | `{}` | Extra metadata dict |
| `.tool_call_id` | `'tc-abc'` or `None` | Tool call link |
| `.pretty_print()` | — | Formatted console output |

---

## Complete Usage Example

```python
from kagraph.messages import (
    HumanMessage, AIMessage, SystemMessage, ToolMessage,
    add_messages, MessagesState, REMOVE_ALL_MESSAGES, coerce_messages,
)
from kagraph import StateGraph

# --- Basic message creation ---
user_msg = HumanMessage('What is 2+2?')
ai_msg   = AIMessage('The answer is 4.')
sys_msg  = SystemMessage('You are a math tutor.')

# --- add_messages reducer: appending ---
conversation = add_messages([], [user_msg, ai_msg])
# → [user_msg, ai_msg]

# --- add_messages reducer: in-place replacement by ID ---
msg_v1 = HumanMessage('Draft 1',          id='msg-1')
msg_v2 = HumanMessage('Draft 2 (revised)', id='msg-1')
result = add_messages([msg_v1], [msg_v2])
# → [msg_v2]

# --- Clear all messages ---
empty = add_messages([user_msg, ai_msg], REMOVE_ALL_MESSAGES)
# → []

# --- Coerce from mixed formats ---
msgs = coerce_messages([
    ('user', 'Hello'),
    {'role': 'assistant', 'content': 'Hi there!'},
    ai_msg,
])

# --- Build a graph with MessagesState ---
def chat_node(state: MessagesState):
    # state['messages'] is a list[AnyMessage]
    last = state['messages'][-1]
    return {'messages': [AIMessage(f'You said: {last.content}')]}

graph = StateGraph(state_schema=MessagesState)
graph.add_node('chat', chat_node)
graph.set_entry_point('chat')
graph.set_finish_point('chat')
app = graph.compile()

result = app.invoke({'messages': [HumanMessage('Hello!')]})
print(result['messages'])
```


