# Image Handling — `kagraph.images`

## Source Map

| File | Contents |
|------|----------|
| `src/kagraph/images.py` | `image_from_base64`, `image_from_path`, `image_from_url`, and type re-exports (`ImageContent`, `ImageBase64`, `ImageURL`) |
| `src/kagraph/messages.py` | `_image_from_content_block`, `ImageMessage`, data URI handling in `coerce_messages` |
| `src/kagraph/prompts.py` | `_coerce_image_inputs` preprocessing applied before LLM calls |
| `src/kagraph/__init__.py` | Re-exports all image symbols |

---

## Overview

KaGraph provides thin, ergonomic wrappers around `kaggle_benchmarks.content_types.images` for creating image payloads that can be attached to messages and sent to multimodal LLMs. These wrappers abstract away the differences between URL references, local files, and raw base64 data so you can work with a single consistent API regardless of where your images come from.

```python
from kagraph.images import image_from_path, image_from_url, image_from_base64
```

---

## Types

The following types are re-exported from `kbench` for convenience:

| Type | Description |
|------|-------------|
| `ImageContent` | Base/union type covering all image payload variants. Use this for type annotations when a function accepts any image. |
| `ImageBase64` | A base64-encoded image payload. Returned by `image_from_base64` and `image_from_path`. |
| `ImageURL` | A URL reference to a remotely hosted image. Returned by `image_from_url`. No downloading occurs — the API receives the URL directly. |

```python
from kagraph.images import ImageContent, ImageBase64, ImageURL
```

---

## Constructor Functions

### `image_from_base64`

```python
image_from_base64(
    data: str | bytes,
    *,
    format: str = 'jpeg',
    caption: str | None = None,
    extra_api_params: dict | None = None,
) -> ImageBase64
```

Creates an `ImageBase64` payload from already-encoded base64 data.

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | `str \| bytes` | Base64-encoded image data. Both `str` and `bytes` are accepted. |
| `format` | `str` | Image format string. Common values: `'jpeg'`, `'png'`, `'webp'`, `'gif'`. Default: `'jpeg'`. |
| `caption` | `str \| None` | Optional caption attached to the image payload. |
| `extra_api_params` | `dict \| None` | Additional parameters forwarded verbatim to the underlying API call. |

---

### `image_from_path`

```python
image_from_path(
    path: str,
    *,
    extra_api_params: dict | None = None,
) -> ImageBase64
```

Loads an image from a local filesystem path, reads its bytes, and base64-encodes them. The image format is inferred from the file extension.

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Absolute or relative path to the image file. |
| `extra_api_params` | `dict \| None` | Additional parameters forwarded to the API. |

This is the most convenient function for working with images on disk — you do not need to manually open the file or encode the bytes.

---

### `image_from_url`

```python
image_from_url(
    url: str,
    *,
    caption: str | None = None,
    extra_api_params: dict | None = None,
) -> ImageURL
```

Creates an `ImageURL` payload referencing a remotely hosted image. **No network request is made** — the URL is passed directly to the LLM API, which fetches the image itself.

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | `str` | A publicly accessible image URL. |
| `caption` | `str \| None` | Optional caption attached to the image payload. |
| `extra_api_params` | `dict \| None` | Additional parameters forwarded to the API. |

> **Note**: When an `ImageURL` is passed to `invoke_llm` or `prompt_llm`, it is internally converted via `kbench_images.from_image_url()` before being sent to the model. This conversion is handled automatically — you do not need to do it manually.

---

## Using Images in Messages

Images can be attached to messages using `ImageMessage` or included as part of multi-part content using `make_message`.

```python
from kagraph.images import image_from_path, image_from_url, image_from_base64
from kagraph.messages import ImageMessage, HumanMessage, make_message

# --- From a local file ---
img = image_from_path('/path/to/photo.jpg')
msg = ImageMessage(img)  # Creates a user message containing the image

# --- From a remote URL ---
img_url = image_from_url(
    'https://example.com/chart.png',
    caption='Sales chart',
)

# --- From raw base64 data ---
import base64
with open('diagram.png', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()
img_b64 = image_from_base64(b64, format='png')
```

---

## Sending Images to LLMs

Pass image payloads to `invoke_llm` using the `image` (single) or `images` (multiple) keyword arguments:

```python
from kagraph.prompts import invoke_llm
from kagraph.images import image_from_path, image_from_url

img1 = image_from_path('/screenshots/before.png')
img2 = image_from_path('/screenshots/after.png')

# --- Single image ---
response = invoke_llm(
    llm,
    prompt='Describe this screenshot.',
    image=img1,
)

# --- Multiple images ---
response = invoke_llm(
    llm,
    prompt='Compare these two screenshots and describe the differences.',
    images=[img1, img2],
)

# --- URL image ---
chart = image_from_url('https://example.com/sales_chart.png', caption='Q3 Sales')
response = invoke_llm(
    llm,
    prompt='Summarize the trends shown in this chart.',
    image=chart,
)
```

`invoke_llm` runs `_coerce_image_inputs` internally to normalise all image types before building the API request. You never need to call this preprocessing step yourself.

---

## Data URI Support in `coerce_messages`

When constructing messages from raw content blocks (e.g. from external APIs or stored conversation histories), KaGraph automatically handles data URI image references. A content block of the form:

```python
{
    'type': 'image_url',
    'image_url': {'url': 'data:image/png;base64,iVBORw0KGgo...'}
}
```

is automatically decoded into an `ImageBase64` object during message coercion. This means you can round-trip images through JSON storage without any manual decode step.

```python
from kagraph.messages import coerce_messages

raw_messages = [
    {
        'role': 'user',
        'content': [
            {'type': 'text', 'text': 'What is in this image?'},
            {
                'type': 'image_url',
                'image_url': {'url': 'data:image/jpeg;base64,/9j/4AAQ...'},
            },
        ],
    }
]

# Data URI is automatically decoded to ImageBase64
messages = coerce_messages(raw_messages)
```

---

## Complete Example: Vision Agent Node

```python
from kagraph.images import image_from_path
from kagraph.prompts import invoke_llm
from kagraph.runtime import Runtime

def vision_node(state: dict, *, runtime: Runtime):
    screenshot_path = state.get('screenshot_path')
    question = state.get('question', 'What do you see?')

    img = image_from_path(screenshot_path)

    response = invoke_llm(
        runtime.chat.llm,
        prompt=question,
        image=img,
    )

    return {'answer': response.content}
```


