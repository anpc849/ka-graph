# LLM Loading — `kagraph.llms`

The `kagraph.llms` module provides two thin factory functions for loading language models from the `kaggle_benchmarks` model registry. The returned objects are **unchanged** `kaggle_benchmarks` model instances — KaGraph adds no wrapper layer.

---

## Source Map

| Source File | Description |
|-------------|-------------|
| `src/kagraph/llms.py` | Full implementation of `load_default_llm()` and `load_llm()`. |
| `src/kagraph/__init__.py` | Re-exports both functions in `__all__`. |
| `src/kagraph/prompts.py` | Prompting layer that consumes the loaded LLMs (`invoke_llm`, `prompt_llm`, `ChatPrompt`). |
| `src/kagraph/messages.py` | Message types (`HumanMessage`, `AIMessage`, etc.) used in the prompting layer. |

---

## Overview

> [!IMPORTANT]
> KaGraph does **not** wrap or replace the model object. What you get back from `load_llm()` or `load_default_llm()` is the canonical `kaggle_benchmarks` `LLMChat` / `ModelProxy` implementation. This object plugs directly into [`invoke_llm`](./prompts.md#invoke_llm), [`prompt_llm`](./prompts.md#prompt_llm), and [`ChatPrompt`](./prompts.md#chatprompt).

This design means:

- You can use the LLM object anywhere `kaggle_benchmarks` expects one, with no adaptation needed.
- KaGraph's prompting utilities (`invoke_llm`, `prompt_llm`) accept the object as-is.
- All model-level configuration (API key, base URL, structured output support) is managed at load time via environment variables and `**kwargs`.

---

## `load_default_llm()`

Loads the environment's configured **default** model.

### Signature

```python
def load_default_llm() -> Any
```

### Description

Delegates to `kaggle_benchmarks.kaggle.models.load_default_model()`. The specific model returned depends on the Kaggle environment configuration — no model identifier is required.

Use this when your code should be environment-agnostic and respect whatever model the competition infrastructure has pre-configured.

### Parameters

_None._

### Returns

The default `kaggle_benchmarks` model instance for the current environment.

### Example

```python
from kagraph.llms import load_default_llm

llm = load_default_llm()
```

---

## `load_llm(model_id, *, api='openai', **kwargs)`

Creates and returns a `kaggle_benchmarks.kaggle.model_proxy.ModelProxy` instance for a specific model.

### Signature

```python
def load_llm(model_id: str, *, api: str = 'openai', **kwargs: Any) -> Any
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model_id` | `str` | **required** | Model identifier string passed to `ModelProxy` (e.g. `"qwen/qwen3-235b-a22b-instruct-2507"`). |
| `api` | `str` | `"openai"` | API protocol to use when communicating with the model backend. |
| `**kwargs` | `Any` | — | Extra keyword arguments forwarded verbatim to `ModelProxy` (see [Common kwargs](#common-kwargs) below). |

### Returns

A `kaggle_benchmarks` `ModelProxy` instance configured for the requested model.

### Environment Variables

`load_llm()` respects the following environment variables when constructing the `ModelProxy`:

| Variable | Purpose |
|----------|---------|
| `MODEL_PROXY_API_KEY` | Authentication key sent to the model backend. |
| `MODEL_PROXY_URL` | Base URL of the model proxy endpoint. |

> [!NOTE]
> In a standard Kaggle notebook environment these variables are automatically injected. When running locally, set them before calling `load_llm()`.

---

## Common `**kwargs`

The following keyword arguments are commonly passed through to `ModelProxy`:

| Kwarg | Type | Description |
|-------|------|-------------|
| `support_structured_outputs` | `bool` | Set to `True` when you intend to pass a Pydantic `schema=` argument to `invoke_llm` or `prompt_llm`. Required for JSON-mode / structured output parsing. |

Additional kwargs supported by `ModelProxy` may be passed here as well — consult the `kaggle_benchmarks` documentation for a complete list.

---

## Code Examples

### Load a specific model

```python
from kagraph.llms import load_llm

llm = load_llm("qwen/qwen3-235b-a22b-instruct-2507")
```

### Load with structured output support enabled

Required when you plan to call `invoke_llm(..., schema=MyPydanticModel)` or `prompt_llm(..., schema=MyPydanticModel)`.

```python
from kagraph.llms import load_llm

llm = load_llm(
    "qwen/qwen3-235b-a22b-instruct-2507",
    support_structured_outputs=True,
)
```

### Load a different API backend

```python
from kagraph.llms import load_llm

llm = load_llm("some-model-id", api="anthropic")
```

### Load the environment default

```python
from kagraph.llms import load_default_llm

llm = load_default_llm()
```

### Use directly with `invoke_llm`

Once loaded, pass the object directly to KaGraph's prompting layer:

```python
from kagraph.llms import load_llm
from kagraph.prompts import invoke_llm
from kagraph.messages import HumanMessage

llm = load_llm("qwen/qwen3-235b-a22b-instruct-2507")

response = invoke_llm(
    llm,
    messages=[HumanMessage("What is 2 + 2?")],
    prompt="Answer concisely.",
)
print(response.text)  # "4"
```

---

## Top-level Re-exports

Both functions are re-exported at the top-level `kagraph` package for convenience:

```python
# These are equivalent:
from kagraph.llms import load_llm, load_default_llm
from kagraph import load_llm, load_default_llm
```


