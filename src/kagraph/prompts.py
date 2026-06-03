from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from kaggle_benchmarks import chats
from kaggle_benchmarks.content_types import images as kbench_images
from kaggle_benchmarks.content_types.images import ImageContent, ImageURL

from kagraph.messages import AnyMessage, coerce_messages, make_message


@dataclass(frozen=True)
class MessagesPlaceholder:
    variable_name: str = "messages"
    optional: bool = False


class ChatPrompt:
    """Small kbench-native equivalent of LangChain's message prompt templates."""

    def __init__(self, messages: Iterable[Any]):
        self.messages = list(messages)

    @classmethod
    def from_messages(cls, messages: Iterable[Any]) -> "ChatPrompt":
        return cls(messages)

    def format_messages(self, values: dict[str, Any] | None = None, **kwargs: Any) -> list[AnyMessage]:
        context = {**(values or {}), **kwargs}
        rendered: list[AnyMessage] = []
        for item in self.messages:
            if isinstance(item, MessagesPlaceholder):
                if item.variable_name not in context:
                    if item.optional:
                        continue
                    raise KeyError(f"Missing messages placeholder value: {item.variable_name!r}")
                rendered.extend(coerce_messages(context[item.variable_name]))
                continue
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
                role, template = item
                content = template.format(**context) if isinstance(template, str) else template
                rendered.append(make_message(role, content))
                continue
            rendered.extend(coerce_messages(item))
        return rendered

    def invoke(
        self,
        llm: Any,
        values: dict[str, Any] | None = None,
        *,
        schema: type = str,
        tools: list[Any] | None = None,
        chat_name: str = "KaGraph prompt",
        **kwargs: Any,
    ):
        return invoke_llm(
            llm,
            messages=self.format_messages(values),
            schema=schema,
            tools=tools,
            chat_name=chat_name,
            **kwargs,
        )


def invoke_llm(
    llm: Any,
    *,
    messages: Any = None,
    prompt: Any = None,
    system: str | None = None,
    schema: type = str,
    tools: list[Any] | None = None,
    image: ImageContent | None = None,
    images: Iterable[ImageContent] | None = None,
    chat_name: str = "KaGraph LLM",
    **kwargs: Any,
):
    """Invoke a kbench LLM with structured message history.

    This preserves message roles instead of flattening graph state into a
    single prompt string. A temporary orphan chat is used so the model sees
    exactly the provided messages while the parent graph chat does not
    accumulate duplicate history.
    """

    with chats.new(chat_name, system_instructions=system, orphan=False) as chat:
        for message in coerce_messages(messages):
            chat.append(message)
        for image_content in _coerce_image_inputs(image=image, images=images):
            chat.append(make_message("user", image_content))
        if prompt is not None:
            prompt_messages = [("user", prompt)] if isinstance(prompt, str) else prompt
            for message in coerce_messages(prompt_messages):
                chat.append(message)
        return llm.respond(schema=schema, tools=tools, **kwargs)


def prompt_llm(
    llm: Any,
    prompt: Any,
    *,
    messages: Any = None,
    system: str | None = None,
    schema: type = str,
    tools: list[Any] | None = None,
    image: ImageContent | None = None,
    images: Iterable[ImageContent] | None = None,
    chat_name: str = "KaGraph LLM",
    **kwargs: Any,
) -> Any:
    """Invoke a kbench LLM and return the parsed response content.

    Use this when a node only needs the model output value. Use
    ``invoke_llm`` when the caller needs the full kbench message metadata.
    """

    response = invoke_llm(
        llm,
        messages=messages,
        prompt=prompt,
        system=system,
        schema=schema,
        tools=tools,
        image=image,
        images=images,
        chat_name=chat_name,
        **kwargs,
    )
    return response.text if schema is str else response.content


def _coerce_image_inputs(
    *,
    image: ImageContent | None = None,
    images: Iterable[ImageContent] | None = None,
) -> list[ImageContent]:
    values: list[ImageContent] = []
    if image is not None:
        values.append(image)
    if images is not None:
        values.extend(images)

    prepared: list[ImageContent] = []
    for value in values:
        if isinstance(value, ImageURL):
            prepared.append(kbench_images.from_image_url(value))
        elif isinstance(value, ImageContent):
            prepared.append(value)
        else:
            raise ValueError(f"Unsupported image input: {type(value)!r}")
    return prepared
