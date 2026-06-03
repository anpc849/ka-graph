# Prompting the LLM — `kagraph.prompts`

The `kagraph.prompts` module is KaGraph's **LLM invocation layer**. It provides three abstractions — `invoke_llm`, `prompt_llm`, and `ChatPrompt` — that handle message assembly, temporary chat lifecycle, and response extraction in a way that is safe to use inside graph nodes.

---

## Source Map

| Source File | Description |
|-------------|-------------|
| `src/kagraph/prompts.py` | Full implementation of `invoke_llm`, `prompt_llm`, `ChatPrompt`, `MessagesPlaceholder`. |
| `src/kagraph/__init__.py` | Re-exports `invoke_llm`, `prompt_llm`, `ChatPrompt`, `MessagesPlaceholder` in `__all__`. |
| `src/kagraph/messages.py` | `coerce_messages()` used by `ChatPrompt.format_messages()` to normalise arbitrary items into typed message objects. |
| `src/kagraph/llms.py` | Functions to load LLM objects passed to `invoke_llm` and `prompt_llm`. |
| `src/kagraph/images.py` | Used for `ImageContent` and `ImageURL` when prompting with images. |

---

## Why KaGraph Has Its Own Prompting Layer

Two design goals drive this module:

1. **Role-preserving messages.** Rather than flattening a conversation to a single prompt string, KaGraph passes each message with its original role (`human`, `ai`, `system`, `tool`). This ensures the model sees a properly structured chat history and that tool-call round-trips work correctly.

2. **Orphan chats.** Each invocation creates an isolated temporary chat via `chats.new()`. The parent graph's chat object is never touched, so the graph's conversation history does not accumulate duplicate messages from intermediate LLM calls inside nodes.

---

## `invoke_llm`

The **low-level invocation primitive**. Returns the full `LLMResponse` object from `kaggle_benchmarks`.

### Signature

```python
def invoke_llm(
    llm,
    *,
    messages=None,
    prompt=None,
    system=None,
    schema=None,
    tools=None,
    image=None,
    images=None,
    chat_name=None,
    **kwargs,
) -> LLMResponse
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | `Any` | **required** | A `kaggle_benchmarks` model object (from `load_llm` / `load_default_llm`). |
| `messages` | `Iterable[AnyMessage] \| None` | `None` | Prior conversation messages to prepend. Each message retains its role. |
| `prompt` | `str \| None` | `None` | A final `HumanMessage`-role prompt appended after `messages`. |
| `system` | `str \| None` | `None` | A system instruction prepended to the chat (before all other messages). |
| `schema` | `type[BaseModel] \| None` | `None` | Pydantic model class for structured/JSON output. Requires the LLM to be loaded with `support_structured_outputs=True`. |
| `tools` | `list[Callable] \| None` | `None` | Tool functions the model may call. Each function's docstring and type hints are used to describe the tool. |
| `image` | `ImageContent \| None` | `None` | A single image payload attached to the last user message. |
| `images` | `Iterable[ImageContent] \| None` | `None` | Multiple image payloads attached to the last user message. |
| `chat_name` | `str \| None` | `None` | Optional name for the temporary chat (useful for tracing / debugging). |
| `**kwargs` | `Any` | — | Extra keyword arguments forwarded to `llm.respond(...)`. |

### Returns

A `kaggle_benchmarks` **`LLMResponse`** object with the following attributes:

| Attribute | Description |
|-----------|-------------|
| `.text` | The model's response as a plain string. |
| `.content` | Parsed Pydantic object when `schema=` is provided; otherwise the raw string. |
| `.tool_calls` | List of tool call requests made by the model, if any. |
| `.reasoning_traces` | Chain-of-thought / reasoning traces (for models that expose them). |
| `.usage` | Token usage metadata (`prompt_tokens`, `completion_tokens`, `total_tokens`). |

### Implementation Note

Internally, `invoke_llm`:
1. Creates a fresh isolated chat with `chats.new(chat_name)`.
2. Appends `system` as a system message if provided.
3. Appends each item from `messages` (preserving roles).
4. Appends `prompt` as a human message if provided.
5. Attaches `image` / `images` (fetching any `ImageURL` objects via `kbench_images.from_image_url()`).
6. Calls `llm.respond(schema=schema, tools=tools, **kwargs)` on the temporary chat.

> [!TIP]
> Use `invoke_llm` when you need full response metadata — tool calls, token usage, or reasoning traces. Use [`prompt_llm`](#prompt_llm) when you only need the output string or Pydantic object.

---

## `prompt_llm`

A **higher-level convenience wrapper** around `invoke_llm`. Returns just the model's output value, discarding the rest of the response envelope.

### Signature

```python
def prompt_llm(
    llm,
    prompt,
    *,
    messages=None,
    system=None,
    schema=None,
    tools=None,
    image=None,
    images=None,
    chat_name=None,
    **kwargs,
) -> str | Any
```

### Parameters

`prompt` is a **positional** parameter here (unlike in `invoke_llm`). All remaining parameters are identical to `invoke_llm` — see the table above.

### Returns

| Condition | Return value |
|-----------|-------------|
| `schema` is `None` or `str` (default) | `response.text` — the model's plain-text reply as a `str`. |
| `schema` is a Pydantic `BaseModel` subclass | `response.content` — the parsed Pydantic object. |

### When to Use

Use `prompt_llm` when you only need the model's output value and do not need to inspect tool calls, token usage, or reasoning traces.

---

## `ChatPrompt`

A **template-based prompting helper** that lets you define reusable prompt structures with variable slots.

### Constructor

```python
ChatPrompt(messages: Iterable[Any])
```

Accepts a list of template items (see [Template Items](#template-items) below).

### Class Methods

#### `from_messages(messages)`

```python
@classmethod
def from_messages(cls, messages: Iterable[Any]) -> ChatPrompt
```

Convenience constructor — identical to calling `ChatPrompt(messages)` directly, but reads more naturally when chaining.

### Instance Methods

#### `format_messages(values=None, **kwargs) -> list[AnyMessage]`

Renders the prompt template into a concrete list of messages.

```python
def format_messages(values: dict | None = None, **kwargs) -> list[AnyMessage]
```

The `values` dict and any `**kwargs` are merged into a single **context** dictionary used for template rendering. Each template item is processed as follows:

| Template item type | Rendering behaviour |
|--------------------|---------------------|
| `MessagesPlaceholder` | Looks up `context[variable_name]` and injects the message list at that position. If `optional=True` and the key is missing, the item is silently skipped. |
| `(role, template_str)` tuple | Formats `template_str` with `template_str.format(**context)`, then converts the result to the appropriate message type for `role`. |
| Any other item | Passed through `coerce_messages()` to produce one or more `AnyMessage` objects. |

#### `invoke(llm, values, *, schema=None, tools=None, chat_name=None, **kwargs)`

Format the template and invoke the LLM in a single step.

```python
def invoke(llm, values: dict, *, schema=None, tools=None, chat_name=None, **kwargs)
```

Equivalent to:

```python
msgs = prompt.format_messages(values)
return invoke_llm(llm, messages=msgs, schema=schema, tools=tools, chat_name=chat_name, **kwargs)
```

---

## `MessagesPlaceholder`

A **dataclass** used inside `ChatPrompt.from_messages([...])` to reserve a slot for a dynamic list of messages.

### Signature

```python
@dataclass
class MessagesPlaceholder:
    variable_name: str = "messages"
    optional: bool = False
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `variable_name` | `str` | `"messages"` | Key in the `format_messages` context dict from which to read the message list. |
| `optional` | `bool` | `False` | If `True`, the placeholder is silently skipped when `variable_name` is absent from the context. If `False`, a missing key raises an error. |

### Usage

```python
from kagraph.prompts import ChatPrompt, MessagesPlaceholder

prompt = ChatPrompt.from_messages([
    ('system', 'You are a helpful assistant.'),
    MessagesPlaceholder('history'),          # injected at runtime
    ('user', '{question}'),
])
```

---

## Image Support

Both `invoke_llm` and `prompt_llm` accept image payloads for multimodal models:

- **`image`** — a single `ImageContent` object.
- **`images`** — an iterable of `ImageContent` objects.

`ImageURL` instances (from `kagraph.images`) are automatically fetched via `kbench_images.from_image_url()` before being forwarded to the model. Use `ImageContent` / `ImageURL` from `kagraph.images` to construct these payloads.

---

## Code Examples

### Simple prompt — string output

```python
from kagraph.llms import load_llm
from kagraph.prompts import prompt_llm

llm = load_llm("qwen/qwen3-235b-a22b-instruct-2507")
result = prompt_llm(llm, "What is 2 + 2?")
print(result)  # '4'
```

### With system instruction and prior messages

```python
from kagraph.prompts import invoke_llm
from kagraph.messages import HumanMessage, SystemMessage

response = invoke_llm(
    llm,
    messages=[HumanMessage("Hello")],
    system="You are a helpful assistant.",
    prompt="What is the capital of France?",
)
print(response.text)       # 'Paris'
print(response.usage)      # token counts
```

### Structured / Pydantic output

```python
from pydantic import BaseModel
from kagraph.llms import load_llm
from kagraph.prompts import prompt_llm

class Answer(BaseModel):
    answer: str
    confidence: float

# Must enable structured output support at load time
llm = load_llm("qwen/qwen3-235b-a22b-instruct-2507", support_structured_outputs=True)

result = prompt_llm(llm, "What is the capital of France?", schema=Answer)
print(result.answer)      # 'Paris'
print(result.confidence)  # e.g. 0.99
```

### `ChatPrompt` template with variable slots

```python
from kagraph.prompts import ChatPrompt, MessagesPlaceholder
from kagraph.messages import HumanMessage

prompt = ChatPrompt.from_messages([
    ("system", "You are {persona}."),
    MessagesPlaceholder("history"),
    ("user", "{question}"),
])

# Render to a concrete message list
msgs = prompt.format_messages(
    persona="a professional chef",
    history=[],                      # no prior history
    question="What is risotto?",
)

# Or invoke the LLM in one step
response = prompt.invoke(llm, {"persona": "a chef", "history": [], "question": "What is risotto?"})
print(response.text)
```

### Tool calling

```python
from kagraph.prompts import invoke_llm

def search(query: str) -> str:
    """Search the web for current information."""
    return f"Results for: {query}"

response = invoke_llm(
    llm,
    prompt="Search for the latest Python news.",
    tools=[search],
)

# Inspect the model's tool call request
for tc in response.tool_calls:
    print(tc.name, tc.arguments)
```

### Inside a graph node

The typical pattern inside a `StateGraph` node:

```python
from kagraph import START, END, StateGraph, MessagesState
from kagraph.llms import load_llm
from kagraph.prompts import invoke_llm

llm = load_llm("qwen/qwen3-235b-a22b-instruct-2507")

def assistant_node(state: MessagesState):
    response = invoke_llm(
        llm,
        messages=state["messages"],
        system="You are a concise assistant.",
    )
    return {"messages": [response]}

graph = StateGraph(MessagesState)
graph.add_node("assistant", assistant_node)
graph.add_edge(START, "assistant")
graph.add_edge("assistant", END)
app = graph.compile()
```


