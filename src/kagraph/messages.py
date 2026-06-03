from __future__ import annotations

import re
from typing import Annotated, Any, Literal, TypedDict

from kaggle_benchmarks import actors
from kaggle_benchmarks.content_types.images import ImageContent
from kaggle_benchmarks.actors.llms import LLMResponse
from kaggle_benchmarks.messages import Message

from kagraph.images import image_from_base64, image_from_url

AnyMessage = Message
REMOVE_ALL_MESSAGES = "__remove_all__"

_ROLE_ACTORS = {
    "user": actors.user,
    "human": actors.user,
    "assistant": actors.Actor(name="assistant", role="assistant"),
    "ai": actors.Actor(name="assistant", role="assistant"),
    "system": actors.system,
    "developer": actors.Actor(name="developer", role="developer"),
    "tool": actors.Tool(),
}


def add_messages(left: Any = None, right: Any = None) -> list[AnyMessage]:
    """Merge message updates using kaggle-benchmarks Message objects.

    The default behavior is append-only. If a right-hand message has the same
    ``id`` as an existing message, it replaces the existing message, matching
    LangGraph's practical message-state behavior while staying kbench-native.
    """

    if right == REMOVE_ALL_MESSAGES:
        return []
    merged = [_decorate_message(msg) for msg in _coerce_messages(left)]
    index_by_id = {
        message_id: index
        for index, message in enumerate(merged)
        if (message_id := _message_id(message)) is not None
    }
    for message in [_decorate_message(msg) for msg in _coerce_messages(right)]:
        message_id = _message_id(message)
        if message_id is not None and message_id in index_by_id:
            merged[index_by_id[message_id]] = message
        else:
            if message_id is not None:
                index_by_id[message_id] = len(merged)
            merged.append(message)
    return merged


class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def coerce_message(value: Any) -> AnyMessage:
    messages = _coerce_messages(value)
    if len(messages) != 1:
        raise ValueError(f"Expected exactly one message, got {len(messages)}.")
    return messages[0]


def coerce_messages(value: Any) -> list[AnyMessage]:
    return [_decorate_message(msg) for msg in _coerce_messages(value)]


def make_message(
    role: Literal["user", "assistant", "system", "developer", "tool"],
    content: Any,
    *,
    name: str | None = None,
    id: str | None = None,
    tool_calls: list[Any] | None = None,
    tool_call_id: str | None = None,
    additional_kwargs: dict[str, Any] | None = None,
) -> AnyMessage:
    actor = actors.Actor(name=name or role, role=role) if name else _ROLE_ACTORS[role]
    meta = dict(additional_kwargs or {})
    if id is not None:
        meta["id"] = id
    if tool_calls is not None:
        meta["tool_calls"] = tool_calls
    if tool_call_id is not None:
        meta["tool_call_id"] = tool_call_id
    message = Message(content=content, sender=actor, _meta=meta)
    if id is not None:
        message.id = id
    return _decorate_message(message)


def HumanMessage(content: Any, **kwargs: Any) -> AnyMessage:
    return make_message("user", content, **kwargs)


def AIMessage(content: Any, **kwargs: Any) -> AnyMessage:
    return make_message("assistant", content, **kwargs)


def SystemMessage(content: Any, **kwargs: Any) -> AnyMessage:
    return make_message("system", content, **kwargs)


def DeveloperMessage(content: Any, **kwargs: Any) -> AnyMessage:
    return make_message("developer", content, **kwargs)


def ToolMessage(content: Any, **kwargs: Any) -> AnyMessage:
    return make_message("tool", content, **kwargs)


def ImageMessage(image: ImageContent, **kwargs: Any) -> AnyMessage:
    return make_message("user", image, **kwargs)


def _coerce_messages(value: Any) -> list[AnyMessage]:
    if value is None:
        return []
    if isinstance(value, Message):
        return [value]
    if isinstance(value, LLMResponse):
        return [
            make_message(
                "assistant",
                value.content,
                tool_calls=value.tool_calls,
                additional_kwargs={**value.meta, "reasoning_traces": value.reasoning_traces},
            )
        ]
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        role, content = value
        return [make_message(_normalize_role(role), content)]
    if isinstance(value, dict) and "role" in value and "content" in value:
        if isinstance(value["content"], list):
            return _coerce_content_blocks(
                _normalize_role(value["role"]),
                value["content"],
                name=value.get("name"),
                additional_kwargs=value.get("additional_kwargs") or {},
            )
        return [
            make_message(
                _normalize_role(value["role"]),
                value["content"],
                id=value.get("id"),
                name=value.get("name"),
                tool_calls=value.get("tool_calls"),
                tool_call_id=value.get("tool_call_id"),
                additional_kwargs=value.get("additional_kwargs") or {},
            )
        ]
    if isinstance(value, list):
        messages: list[AnyMessage] = []
        for item in value:
            messages.extend(_coerce_messages(item))
        return messages
    if hasattr(value, "content"):
        role = _normalize_role(getattr(value, "role", None) or getattr(value, "type", "assistant"))
        tool_calls = getattr(value, "tool_calls", None)
        meta = getattr(value, "additional_kwargs", None) or {}
        return [
            make_message(
                role,
                getattr(value, "content"),
                name=getattr(value, "name", None),
                tool_calls=tool_calls,
                additional_kwargs=meta,
            )
        ]
    return [make_message("user", str(value))]


def _coerce_content_blocks(
    role: Literal["user", "assistant", "system", "developer", "tool"],
    blocks: list[Any],
    *,
    name: str | None = None,
    additional_kwargs: dict[str, Any] | None = None,
) -> list[AnyMessage]:
    messages: list[AnyMessage] = []
    for block in blocks:
        if isinstance(block, ImageContent):
            messages.append(make_message(role, block, name=name, additional_kwargs=additional_kwargs))
            continue
        if not isinstance(block, dict):
            messages.append(make_message(role, block, name=name, additional_kwargs=additional_kwargs))
            continue

        block_type = block.get("type")
        if block_type == "text":
            messages.append(
                make_message(
                    role,
                    block.get("text", ""),
                    name=name,
                    additional_kwargs=additional_kwargs,
                )
            )
            continue

        if block_type == "image_url":
            messages.append(
                make_message(
                    role,
                    _image_from_content_block(block),
                    name=name,
                    additional_kwargs=additional_kwargs,
                )
            )
            continue

        messages.append(make_message(role, block, name=name, additional_kwargs=additional_kwargs))
    return messages


def _image_from_content_block(block: dict[str, Any]) -> ImageContent:
    image_url = block.get("image_url")
    url = image_url.get("url") if isinstance(image_url, dict) else image_url
    if not isinstance(url, str) or not url:
        raise ValueError(f"Invalid image_url content block: {block!r}")

    data_uri_match = re.match(r"^data:image/([^;,]+);base64,(.+)$", url, flags=re.DOTALL)
    if data_uri_match:
        image_format, data = data_uri_match.groups()
        return image_from_base64(data, format=image_format, caption=block.get("caption"))
    return image_from_url(url, caption=block.get("caption"))


def _normalize_role(role: str) -> Literal["user", "assistant", "system", "developer", "tool"]:
    role = role.lower()
    if role in {"human", "user"}:
        return "user"
    if role in {"ai", "assistant"}:
        return "assistant"
    if role == "system":
        return "system"
    if role == "developer":
        return "developer"
    if role == "tool":
        return "tool"
    return "assistant"


def _decorate_message(message: AnyMessage) -> AnyMessage:
    role = getattr(message.sender, "role", "assistant")
    type_name = "human" if role == "user" else "ai" if role == "assistant" else role
    try:
        message.type = type_name
        message.role = role
        message.additional_kwargs = message._meta
        if "id" in message._meta and not hasattr(message, "id"):
            message.id = message._meta["id"]
        if "tool_call_id" in message._meta:
            message.tool_call_id = message._meta["tool_call_id"]
        if not hasattr(message, "pretty_print"):
            message.pretty_print = lambda: print(message)
    except Exception:
        pass
    return message


def _message_id(message: AnyMessage) -> str | None:
    return getattr(message, "id", None) or (getattr(message, "_meta", {}) or {}).get("id")
