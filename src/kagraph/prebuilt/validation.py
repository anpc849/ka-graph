from __future__ import annotations

from typing import Any

from kaggle_benchmarks import actors
from kaggle_benchmarks.messages import Message

from kagraph.prebuilt.tool_node import _coerce_tool_invocation, _tool_name


class ValidationNode:
    def __init__(self, tools: list[Any], format_error=None):
        self.tools = {_tool_name(tool): tool for tool in tools}
        self.format_error = format_error

    def __call__(self, state: dict) -> dict:
        messages = state.get("messages") or []
        if not messages:
            return {}
        tool_calls = getattr(messages[-1], "tool_calls", None) or getattr(messages[-1], "_meta", {}).get("tool_calls") or []
        output = []
        for call_data in tool_calls:
            invocation = _coerce_tool_invocation(call_data)
            tool = self.tools.get(invocation.name)
            error = None
            if tool is None:
                error = f"Unknown tool: {invocation.name}"
            else:
                try:
                    if hasattr(tool, "model_validate"):
                        tool.model_validate(invocation.arguments)
                    elif hasattr(tool, "parse_obj"):
                        tool.parse_obj(invocation.arguments)
                except Exception as exc:
                    error = self.format_error(exc, call_data, tool) if self.format_error else str(exc)
            if error:
                output.append(
                    Message(
                        error,
                        sender=actors.system,
                        _meta={"is_error": True, "tool_call_id": invocation.call_id},
                    )
                )
            else:
                output.append(
                    Message(
                        f"Validated tool call: {invocation.name}",
                        sender=actors.system,
                        _meta={"is_error": False, "tool_call_id": invocation.call_id},
                    )
                )
        return {"messages": output} if output else {}

    def invoke(self, state: dict, config: dict | None = None) -> dict:
        return self(state)
