from __future__ import annotations

import inspect
from typing import Callable

from kaggle_benchmarks import actors, events
from kaggle_benchmarks.messages import Message
from kaggle_benchmarks.tools import ToolInvocation, ToolInvocationResult

from kagraph.runtime import get_chat, get_runtime


def tools_condition(state: dict) -> str:
    from kagraph.constants import END

    messages = state.get("messages") or []
    if not messages:
        return END
    return "tools" if _extract_tool_calls(messages[-1]) else END


class ToolNode:
    def __init__(self, tools: list[Callable]):
        self.tools = {_tool_name(tool): tool for tool in tools}
        self._fallbacks: list[Callable] = []
        self._exception_key = "error"

    def __call__(self, state: dict | None = None) -> dict | None:
        state = state or {}
        try:
            return self._run(state)
        except Exception as error:
            config = state.get("_config", {})
            try:
                runtime = get_runtime()
                config = runtime.config or config
            except RuntimeError:
                pass
            return self._handle_error(error, state, config)

    def _run(self, state: dict) -> dict | None:
        try:
            chat = get_chat()
            runtime = get_runtime()
        except RuntimeError:
            chat = None
            runtime = None
        run_config = runtime.config if runtime else state.get("_config", {})
        messages = state.get("messages") or (chat.messages if chat is not None else [])
        if not messages:
            return None

        last_msg = messages[-1]
        tool_calls = _extract_tool_calls(last_msg)
        results = []
        tool_messages = []

        for call_data in tool_calls:
            invocation = _coerce_tool_invocation(call_data)
            events.manager.dispatch("kagraph_tool_start", invocation=invocation, chat=chat, state=state)
            result = _invoke_tool(invocation, self.tools, run_config)
            if result.error and self._fallbacks:
                raise RuntimeError(result.error)
            tool_actor = actors.Tool(name=invocation.name)
            tool_message = Message(result, sender=tool_actor, _meta={"tool_call_id": invocation.call_id})
            if chat is not None:
                chat.append(tool_message)
            events.manager.dispatch("kagraph_tool_end", invocation=invocation, result=result, chat=chat, state=state)
            results.append(result)
            tool_messages.append(tool_message)

        if results:
            return {"tool_results": results, "messages": tool_messages}
        return None

    def invoke(self, state: dict | None = None, config: dict | None = None) -> dict | None:
        state = dict(state or {})
        state["_config"] = config or {}
        try:
            return self(state)
        except Exception as error:
            return self._handle_error(error, state, config or {})

    def with_fallbacks(
        self,
        fallbacks: list[Callable],
        *,
        exception_key: str = "error",
    ) -> "ToolNode":
        clone = type(self)(list(self.tools.values()))
        clone._fallbacks = list(fallbacks)
        clone._exception_key = exception_key
        return clone

    def _handle_error(self, error: Exception, state: dict, config: dict) -> dict | None:
        if not self._fallbacks:
            raise error
        fallback_input = dict(state)
        fallback_input[self._exception_key] = error
        for fallback in self._fallbacks:
            try:
                return _call_fallback(fallback, fallback_input, config)
            except Exception as next_error:
                error = next_error
        raise error


def _extract_tool_calls(message) -> list:
    calls = getattr(message, "tool_calls", None)
    if calls:
        return list(calls)
    meta = getattr(message, "_meta", {}) or {}
    return list(meta.get("tool_calls") or [])


def _coerce_tool_invocation(call_data) -> ToolInvocation:
    if isinstance(call_data, ToolInvocation):
        return call_data
    if isinstance(call_data, dict) and "function" in call_data:
        return ToolInvocation.from_api_dict(call_data)
    if isinstance(call_data, dict) and "name" in call_data:
        return ToolInvocation(
            name=call_data["name"],
            arguments=call_data.get("args") or call_data.get("arguments") or {},
            call_id=call_data.get("id") or call_data.get("call_id"),
        )
    if hasattr(call_data, "name"):
        return ToolInvocation(
            name=call_data.name,
            arguments=getattr(call_data, "args", None) or getattr(call_data, "arguments", {}) or {},
            call_id=getattr(call_data, "id", None) or getattr(call_data, "call_id", None),
        )
    raise ValueError(f"Unsupported tool call: {call_data!r}")


def _invoke_tool(invocation: ToolInvocation, tools: dict[str, Callable], config: dict) -> ToolInvocationResult:
    tool = tools.get(invocation.name)
    clean_arguments = _strip_infrastructure_arguments(invocation.arguments)
    if tool is None:
        return ToolInvocationResult(invocation.name, clean_arguments, invocation.call_id, error=f"Error: Tool '{invocation.name}' not found.")
    try:
        signature = inspect.signature(tool)
        args = dict(clean_arguments or {})
        if not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            args = {key: value for key, value in args.items() if key in signature.parameters}
        if "config" in signature.parameters:
            args["config"] = config
        return ToolInvocationResult(
            name=invocation.name,
            arguments=clean_arguments,
            call_id=invocation.call_id,
            output=tool(**args),
        )
    except Exception as error:
        return ToolInvocationResult(
            name=invocation.name,
            arguments=clean_arguments,
            call_id=invocation.call_id,
            error=f"Error invoking tool '{invocation.name}': {error}",
        )


def _tool_name(tool: Callable) -> str:
    return getattr(tool, "name", None) or getattr(tool, "__name__", tool.__class__.__name__)


def _strip_infrastructure_arguments(arguments: dict | None) -> dict:
    return {key: value for key, value in dict(arguments or {}).items() if key != "signature"}


def _call_fallback(fallback: Callable, state: dict, config: dict):
    signature = inspect.signature(fallback)
    if "config" in signature.parameters:
        return fallback(state, config)
    return fallback(state)
