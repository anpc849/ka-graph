from __future__ import annotations

import base64
import io
import json
import logging
import os
import queue
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, get_args, get_origin, get_type_hints

import requests
from kaggle_benchmarks import events

from kagraph._studio_config import DEFAULT_BACKEND_URL

logger = logging.getLogger(__name__)


class KaGraphTracer:
    """Forward kbench and KaGraph runtime events into the KaTrace Studio backend."""

    def __init__(
        self,
        backend_url: str = DEFAULT_BACKEND_URL,
        *,
        trace_name: str = "kagraph_run",
        include_state: bool = True,
        include_messages: bool = True,
        include_checkpoints: bool = True,
        include_agent_binary: bool = False,
        include_tool_signatures: bool = False,
        agent_factory: str | dict[str, Any] | None = None,
        max_payload_bytes: int = 250_000,
        batch_size: int = 25,
        request_timeout: float = 10.0,
        max_retries: int = 2,
    ):
        self.backend_url = backend_url.rstrip("/")
        self.trace_name = trace_name
        self.include_state = include_state
        self.include_messages = include_messages
        self.include_checkpoints = include_checkpoints
        self.include_agent_binary = include_agent_binary
        self.include_tool_signatures = include_tool_signatures
        self.agent_factory = agent_factory
        self.max_payload_bytes = max_payload_bytes
        self.batch_size = max(1, batch_size)
        self.request_timeout = max(0.1, request_timeout)
        self.max_retries = max(0, max_retries)
        self.trace_id: str | None = None
        self._span_stack: list[dict[str, Any]] = []
        self._attached = False
        self._sequence = 0
        self._event_buffer: list[dict[str, Any]] = []
        self._graph_run_id: str | None = None
        self._current_step: int | None = None
        self._current_step_id: str | None = None
        self._step_ids: dict[int, str] = {}
        self._last_node_run_by_name: dict[str, str] = {}
        self._graph_stack: list[dict[str, Any]] = []
        self._transport_failures = 0

        self._queue: queue.Queue[tuple[str, str, dict[str, Any]] | None] = queue.Queue()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def attach(self) -> None:
        if not self._attached:
            events.manager.bind(self)
            self._attached = True

    def detach(self) -> None:
        if self._attached:
            events.manager.unbind(self)
            self._attached = False
        self.flush()

    def flush(self) -> None:
        self._flush_event_batch()
        self._queue.join()

    def _worker(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                break
            endpoint, method, payload = task
            ok = False
            last_error: Exception | None = None
            try:
                url = f"{self.backend_url}{endpoint}"
                for attempt in range(self.max_retries + 1):
                    try:
                        if method == "POST":
                            response = requests.post(url, json=payload, timeout=self.request_timeout)
                        elif method == "PUT":
                            response = requests.put(url, json=payload, timeout=self.request_timeout)
                        else:
                            ok = True
                            break
                        response.raise_for_status()
                        ok = True
                        break
                    except Exception as error:
                        last_error = error
                        if attempt < self.max_retries:
                            time.sleep(min(0.25 * (2**attempt), 2.0))
                if not ok and last_error is not None:
                    self._transport_failures += 1
                    if self._transport_failures <= 3 or self._transport_failures % 10 == 0:
                        logger.warning(
                            "Failed to send tracing event to %s after %s attempt(s): %s",
                            endpoint,
                            self.max_retries + 1,
                            last_error,
                        )
            finally:
                self._queue.task_done()

    def _dispatch(self, endpoint: str, method: str, payload: dict[str, Any]) -> None:
        self._queue.put((endpoint, method, _make_request_payload_serializable(endpoint, payload, self.max_payload_bytes)))

    def _emit_event(
        self,
        event: str,
        name: str | None = None,
        *,
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        node: str | None = None,
        checkpoint_id: str | None = None,
        parent_ids: list[str] | None = None,
        flush: bool = False,
    ) -> None:
        if not self.trace_id:
            return
        payload_data = dict(data or {})
        event_metadata = dict(metadata or {})
        if self._current_step is not None:
            event_metadata.setdefault("step", self._current_step)
            if self._current_step_id:
                event_metadata.setdefault("step_run_id", self._current_step_id)
        if self._span_stack:
            event_metadata.setdefault("parent_run_id", self._span_stack[-1]["id"])
        elif self._current_step_id:
            event_metadata.setdefault("parent_run_id", self._current_step_id)
        elif self._graph_run_id:
            event_metadata.setdefault("parent_run_id", self._graph_run_id)
        if not self.include_state:
            payload_data.pop("state", None)
            payload_data.pop("input", None)
            payload_data.pop("output", None)
        if not self.include_messages:
            payload_data.pop("message", None)
            payload_data.pop("messages", None)
        if not self.include_checkpoints:
            checkpoint_id = None
            payload_data.pop("checkpoint", None)
            payload_data.pop("channel_versions", None)
            payload_data.pop("pending_writes", None)

        self._sequence += 1
        self._event_buffer.append(
            {
                "trace_id": self.trace_id,
                "sequence": self._sequence,
                "timestamp": _now_iso(),
                "event": event,
                "name": name,
                "node": node,
                "checkpoint_id": checkpoint_id,
                "parent_ids": parent_ids if parent_ids is not None else self._default_parent_ids(),
                "data": _make_serializable(
                    payload_data,
                    self.max_payload_bytes,
                    include_tool_signatures=self.include_tool_signatures,
                ),
                "metadata_json": _make_serializable(
                    event_metadata,
                    self.max_payload_bytes,
                    include_tool_signatures=self.include_tool_signatures,
                ),
            }
        )
        if flush or len(self._event_buffer) >= self.batch_size:
            self._flush_event_batch()

    def _store_current_graph_context(self) -> None:
        if not self._graph_stack:
            return
        self._graph_stack[-1]["current_step"] = self._current_step
        self._graph_stack[-1]["current_step_id"] = self._current_step_id
        self._graph_stack[-1]["step_ids"] = dict(self._step_ids)
        self._graph_stack[-1]["last_node_run_by_name"] = dict(self._last_node_run_by_name)

    def _activate_graph_context(self, frame: dict[str, Any] | None) -> None:
        if frame is None:
            self._graph_run_id = None
            self._current_step = None
            self._current_step_id = None
            self._step_ids = {}
            self._last_node_run_by_name = {}
            return
        self._graph_run_id = frame["run_id"]
        self._current_step = frame.get("current_step")
        self._current_step_id = frame.get("current_step_id")
        self._step_ids = dict(frame.get("step_ids") or {})
        self._last_node_run_by_name = dict(frame.get("last_node_run_by_name") or {})

    def _push_graph_context(self, frame: dict[str, Any]) -> None:
        self._store_current_graph_context()
        self._graph_stack.append(frame)
        self._activate_graph_context(frame)

    def _pop_graph_context(self) -> dict[str, Any] | None:
        self._store_current_graph_context()
        frame = self._graph_stack.pop() if self._graph_stack else None
        self._activate_graph_context(self._graph_stack[-1] if self._graph_stack else None)
        return frame

    def _default_parent_ids(self) -> list[str]:
        if self._span_stack:
            return [self._span_stack[-1]["id"]]
        if self._current_step_id:
            return [self._current_step_id]
        if self._graph_run_id:
            return [self._graph_run_id]
        return []

    def _flush_event_batch(self) -> None:
        if not self._event_buffer:
            return
        events_to_send = self._event_buffer
        self._event_buffer = []
        self._dispatch("/api/events/batch", "POST", {"events": events_to_send})

    def kagraph_invoke_start(self, **payload: Any) -> None:
        graph_obj = payload.get("graph")
        graph_name = getattr(graph_obj, "name", None) or "KaGraph"
        parent_id = self._span_stack[-1]["id"] if self._span_stack else self._current_step_id or self._graph_run_id
        is_root = self.trace_id is None
        if not is_root:
            frame = {
                "run_id": str(uuid.uuid4()),
                "name": graph_name,
                "is_root": False,
                "parent_id": parent_id,
                "current_step": None,
                "current_step_id": None,
                "step_ids": {},
                "last_node_run_by_name": {},
            }
            self._push_graph_context(frame)
            self._emit_event(
                "on_subgraph_start",
                graph_name,
                data={"input": payload.get("input")},
                metadata={
                    "run_id": frame["run_id"],
                    "parent_run_id": parent_id,
                    "graph_depth": len(self._graph_stack) - 1,
                    "is_root": False,
                },
                parent_ids=[parent_id] if parent_id else [],
            )
            return

        self.trace_id = str(uuid.uuid4())
        self._span_stack.clear()
        self._sequence = 0
        self._event_buffer = []
        self._graph_stack = []
        if not hasattr(self, "_seen_messages"):
            self._seen_messages = set()
        self._seen_messages.clear()
        root_run_id = str(uuid.uuid4())
        self._current_step = None
        self._current_step_id = None
        self._step_ids = {}
        self._last_node_run_by_name = {}
        self._push_graph_context(
            {
                "run_id": root_run_id,
                "name": graph_name,
                "is_root": True,
                "parent_id": None,
                "current_step": None,
                "current_step_id": None,
                "step_ids": {},
                "last_node_run_by_name": {},
            }
        )

        metadata_json: dict[str, Any] = {
            "invoke": _make_serializable(
                {
                    "input": payload.get("input"),
                    "config": payload.get("config"),
                    "context": payload.get("context"),
                    "recursion_limit": payload.get("recursion_limit"),
                    "chat_name": payload.get("chat_name"),
                    "system_instructions": payload.get("system_instructions"),
                    "session_id": payload.get("session_id"),
                    "user_id": payload.get("user_id"),
                },
                self.max_payload_bytes,
            ),
            "python": _make_serializable(
                {
                    "cwd": os.getcwd(),
                    "sys_path": [path for path in sys.path if path],
                },
                self.max_payload_bytes,
            ),
        }
        if self.agent_factory:
            metadata_json["agent_factory"] = _make_serializable(self.agent_factory, self.max_payload_bytes)
        agent_binary = None
        if graph_obj and hasattr(graph_obj, "get_graph"):
            try:
                gview = graph_obj.get_graph()
                builder = getattr(graph_obj, "builder", None)
                metadata_json["graph"] = {
                    "nodes": gview.nodes,
                    "edges": [
                        {
                            "source": edge.source,
                            "target": edge.target,
                            "label": getattr(edge, "label", None),
                            "conditional": getattr(edge, "conditional", False),
                        }
                        for edge in gview.edges
                    ],
                }
                metadata_json["schema"] = {
                    "input": _schema_fields(getattr(builder, "input_schema", None)),
                    "context": _schema_fields(getattr(builder, "context_schema", None)),
                }
                if self.include_agent_binary:
                    agent_binary = base64.b64encode(_dump_replayable_agent(graph_obj)).decode("utf-8")
            except Exception as error:
                logger.warning("Failed to extract or serialize graph: %s", error)

        self._dispatch(
            "/api/trace",
            "POST",
            {
                "id": self.trace_id,
                "name": self.trace_name,
                "input": payload.get("input"),
                "session_id": payload.get("session_id"),
                "user_id": payload.get("user_id"),
                "start_time": _now_iso(),
                "metadata_json": metadata_json or None,
                "agent_binary": agent_binary,
            },
        )
        self._emit_event(
            "on_graph_start",
            graph_name,
            data={"input": payload.get("input")},
            metadata={"run_id": self._graph_run_id, "graph_depth": 0, "is_root": True},
            parent_ids=[],
        )

    def kagraph_invoke_end(self, **payload: Any) -> None:
        if not self.trace_id:
            return
        frame = self._graph_stack[-1] if self._graph_stack else {"run_id": self._graph_run_id, "is_root": True, "parent_id": None}
        is_root = bool(frame.get("is_root", True))
        graph_run_id = frame.get("run_id") or self._graph_run_id
        parent_id = frame.get("parent_id")
        output = _summarize_output(payload.get("output"))
        self._emit_event(
            "on_graph_end" if is_root else "on_subgraph_end",
            frame.get("name") or "KaGraph",
            data={"output": output},
            metadata={
                "run_id": graph_run_id,
                "parent_run_id": graph_run_id if is_root else parent_id,
                "graph_depth": max(len(self._graph_stack) - 1, 0),
                "is_root": is_root,
            },
            parent_ids=[graph_run_id] if is_root and graph_run_id else [parent_id] if parent_id else [],
            flush=is_root,
        )
        self._pop_graph_context()
        if not is_root:
            return
        self._dispatch(
            f"/api/trace/{self.trace_id}",
            "PUT",
            {"output": output, "status": "SUCCESS", "end_time": _now_iso()},
        )
        self.trace_id = None
        self._span_stack.clear()
        self._graph_stack = []
        self._activate_graph_context(None)

    def kagraph_invoke_error(self, **payload: Any) -> None:
        if not self.trace_id:
            return
        frame = self._graph_stack[-1] if self._graph_stack else {"run_id": self._graph_run_id, "is_root": True, "parent_id": None}
        is_root = bool(frame.get("is_root", True))
        graph_run_id = frame.get("run_id") or self._graph_run_id
        parent_id = frame.get("parent_id")
        error = str(payload.get("error"))
        self._emit_event(
            "on_graph_error" if is_root else "on_subgraph_error",
            frame.get("name") or "KaGraph",
            data={"error": error},
            metadata={
                "run_id": graph_run_id,
                "parent_run_id": graph_run_id if is_root else parent_id,
                "graph_depth": max(len(self._graph_stack) - 1, 0),
                "is_root": is_root,
            },
            parent_ids=[graph_run_id] if is_root and graph_run_id else [parent_id] if parent_id else [],
            flush=is_root,
        )
        self._pop_graph_context()
        if not is_root:
            return
        self._dispatch(
            f"/api/trace/{self.trace_id}",
            "PUT",
            {"status": "ERROR", "error": error, "end_time": _now_iso()},
        )
        self.trace_id = None
        self._span_stack.clear()
        self._graph_stack = []
        self._activate_graph_context(None)

    def kagraph_step_start(self, graph: str, step: int, next: list, state: dict, **_: Any) -> None:
        step_id = str(uuid.uuid4())
        self._current_step = step
        self._current_step_id = step_id
        self._step_ids[step] = step_id
        self._emit_event(
            "on_step_start",
            graph,
            data={"step": step, "next": next, "state": state},
            metadata={"step": step, "run_id": step_id, "parent_run_id": self._graph_run_id},
            parent_ids=[self._graph_run_id] if self._graph_run_id else [],
        )

    def kagraph_step_end(self, graph: str, step: int, next: list, state: dict, **_: Any) -> None:
        step_id = self._step_ids.get(step) or self._current_step_id
        self._emit_event(
            "on_step_end",
            graph,
            data={"step": step, "next": next, "state": state},
            metadata={"step": step, "run_id": step_id, "parent_run_id": self._graph_run_id},
            parent_ids=[step_id] if step_id else [],
        )
        if self._current_step == step:
            self._current_step = None
            self._current_step_id = None

    def kagraph_node_start(self, node: str, state: dict, **_: Any) -> None:
        if not self.trace_id:
            return
        span_id = str(uuid.uuid4())
        parent_id = self._current_step_id or self._graph_run_id
        self._span_stack.append({"id": span_id, "name": node, "type": "NODE", "step": self._current_step})
        self._dispatch(
            "/api/span",
            "POST",
            {
                "id": span_id,
                "trace_id": self.trace_id,
                "parent_id": parent_id,
                "name": node,
                "span_type": "NODE",
                "input": state,
                "metadata_json": {"step": self._current_step, "run_id": span_id, "parent_run_id": parent_id},
            },
        )
        self._emit_event(
            "on_node_start",
            node,
            data={"state": state},
            metadata={"step": self._current_step, "run_id": span_id, "parent_run_id": parent_id},
            node=node,
            parent_ids=[parent_id] if parent_id else [],
        )

    def kagraph_node_update(self, node: str, update: dict, state: dict, channel_versions: dict, step: int, **_: Any) -> None:
        node_run_id = self._span_stack[-1]["id"] if self._span_stack else self._last_node_run_by_name.get(node)
        self._emit_event(
            "on_node_update",
            node,
            data={"update": update, "state": state, "channel_versions": channel_versions},
            metadata={"step": step, "run_id": node_run_id, "parent_run_id": node_run_id},
            node=node,
            parent_ids=[node_run_id] if node_run_id else None,
        )

    def kagraph_node_end(self, node: str | None = None, state: dict | None = None, error: BaseException | None = None, **_: Any) -> None:
        span = self._span_stack.pop() if self._span_stack else None
        node_name = node or (span or {}).get("name")
        node_run_id = (span or {}).get("id")
        if node_name and node_run_id:
            self._last_node_run_by_name[node_name] = node_run_id
        if span:
            self._dispatch(
                f"/api/span/{span['id']}",
                "PUT",
                {
                    "output": state if error is None else None,
                    "end_time": _now_iso(),
                    "status": "SUCCESS" if error is None else "ERROR",
                    "error": None if error is None else str(error),
                },
            )
        self._emit_event(
            "on_node_end",
            node_name,
            data={"state": state or {}, "error": None if error is None else str(error)},
            metadata={"step": (span or {}).get("step", self._current_step), "run_id": node_run_id, "parent_run_id": node_run_id},
            node=node_name,
            parent_ids=[node_run_id] if node_run_id else None,
        )

    def kagraph_checkpoint(self, thread_id: str, checkpoint: dict, state: dict, next: list, interrupt: Any, metadata: dict, **_: Any) -> None:
        checkpoint_id = checkpoint.get("checkpoint_id")
        source = metadata.get("source")
        parent_id = None
        if source and source not in {"checkpoint", "interrupt", "interrupt_before", "interrupt_after"}:
            parent_id = self._last_node_run_by_name.get(source)
        parent_id = parent_id or self._current_step_id or self._graph_run_id
        self._emit_event(
            "on_checkpoint",
            metadata.get("source") or "checkpoint",
            data={
                "thread_id": thread_id,
                "checkpoint": checkpoint,
                "state": state,
                "next": next,
                "interrupt": interrupt,
                "channel_versions": checkpoint.get("channel_versions"),
                "versions_seen": checkpoint.get("versions_seen"),
                "pending_writes": checkpoint.get("pending_writes"),
            },
            metadata={**metadata, "parent_run_id": parent_id},
            checkpoint_id=checkpoint_id,
            parent_ids=[parent_id] if parent_id else [],
        )

    def kagraph_tool_start(self, invocation, **_: Any) -> None:
        if not self.trace_id:
            return
        span_id = str(uuid.uuid4())
        parent_id = self._span_stack[-1]["id"] if self._span_stack else None
        arguments = _sanitize_tool_payload(
            getattr(invocation, "arguments", None),
            include_tool_signatures=self.include_tool_signatures,
        )
        self._span_stack.append({"id": span_id, "name": invocation.name, "type": "TOOL", "step": self._current_step})
        self._dispatch(
            "/api/span",
            "POST",
            {
                "id": span_id,
                "trace_id": self.trace_id,
                "parent_id": parent_id,
                "name": invocation.name,
                "span_type": "TOOL",
                "input": arguments,
                "metadata_json": {
                    "call_id": getattr(invocation, "call_id", None),
                    "step": self._current_step,
                    "run_id": span_id,
                    "parent_run_id": parent_id,
                },
            },
        )
        self._emit_event(
            "on_tool_start",
            invocation.name,
            data={"arguments": arguments, "call_id": getattr(invocation, "call_id", None)},
            metadata={"run_id": span_id, "parent_run_id": parent_id, "step": self._current_step},
            parent_ids=[parent_id] if parent_id else [],
        )

    def kagraph_tool_end(self, result, **_: Any) -> None:
        span = self._span_stack.pop() if self._span_stack else None
        output = getattr(result, "text", None) or getattr(result, "output", None) or str(result)
        error = getattr(result, "error", None)
        if span:
            self._dispatch(
                f"/api/span/{span['id']}",
                "PUT",
                {
                    "output": output,
                    "end_time": _now_iso(),
                    "status": "SUCCESS" if not error else "ERROR",
                    "error": error,
                },
            )
        self._emit_event(
            "on_tool_end",
            (span or {}).get("name"),
            data={"output": output, "error": error},
            metadata={"run_id": (span or {}).get("id"), "parent_run_id": (span or {}).get("id"), "step": (span or {}).get("step", self._current_step)},
            parent_ids=[(span or {}).get("id")] if (span or {}).get("id") else None,
        )

    def new_message(self, chat, message) -> None:
        if not self.trace_id:
            return
        role = getattr(getattr(message, "sender", None), "role", None)
        
        if role == "assistant":
            msg_id = getattr(message, "id", id(message))
            if not hasattr(self, "_seen_messages"):
                self._seen_messages = set()
            if msg_id not in self._seen_messages:
                self._seen_messages.add(msg_id)
                self._create_generation(chat, message)
                
        self._emit_event(
            "on_message",
            getattr(getattr(message, "sender", None), "name", role or "message"),
            data={"message": message, "messages": getattr(chat, "messages", [])},
            metadata={"role": role},
        )

    def start_streaming(self, message) -> None:
        self._emit_event(
            "on_chat_model_start",
            getattr(getattr(message, "sender", None), "name", "assistant"),
            data={"message": message},
        )

    def new_chunk(self, message, chunk) -> None:
        chunk_text = chunk if isinstance(chunk, str) else getattr(chunk, "content", "")
        self._emit_event(
            "on_chat_model_stream",
            getattr(getattr(message, "sender", None), "name", "assistant"),
            data={"message": message, "chunk": chunk, "content": chunk_text},
        )

    def new_tool_call(self, message, chunk) -> None:
        self._emit_event(
            "on_tool_stream",
            getattr(getattr(message, "sender", None), "name", "assistant"),
            data={"message": message, "chunk": chunk},
        )

    def _create_generation(self, chat, message) -> None:
        usage = _usage_dict(getattr(message, "usage", None))
        metadata: dict[str, Any] = {}
        meta = getattr(message, "_meta", {}) or {}
        if meta.get("reasoning_traces"):
            metadata["reasoning_traces"] = meta["reasoning_traces"]
        metadata["usage"] = usage
        gen_id = str(uuid.uuid4())
        parent_id = self._span_stack[-1]["id"] if self._span_stack else None
        input_messages = list(getattr(chat, "messages", []))
        if input_messages and input_messages[-1] is message:
            input_messages = input_messages[:-1]
        self._dispatch(
            "/api/generation",
            "POST",
            {
                "id": gen_id,
                "trace_id": self.trace_id,
                "parent_id": parent_id,
                "name": getattr(message.sender, "name", "assistant"),
                "model": getattr(message.sender, "name", None),
                "input": [getattr(item, "payload", str(item)) for item in input_messages],
                "usage_input_tokens": usage["input_tokens"],
                "usage_output_tokens": usage["output_tokens"],
                "cost_total": usage["total_cost_nanodollars"] / 1e9 if usage.get("total_cost_nanodollars") else None,
                "metadata_json": metadata,
            },
        )
        self._dispatch(
            f"/api/generation/{gen_id}",
            "PUT",
            {"output": getattr(message, "payload", str(message)), "end_time": _now_iso()},
        )
        self._emit_event(
            "on_chat_model_end",
            getattr(message.sender, "name", "assistant"),
            data={
                "message": message,
                "output": getattr(message, "payload", str(message)),
                "usage": usage,
            },
            metadata=metadata,
        )


def _make_serializable(
    obj: Any,
    max_payload_bytes: int = 250_000,
    *,
    include_tool_signatures: bool = False,
) -> Any:
    value = _to_plain(obj, include_tool_signatures=include_tool_signatures)
    try:
        encoded = json.dumps(value, default=str)
    except Exception:
        return str(value)
    if len(encoded.encode("utf-8")) <= max_payload_bytes:
        return value
    return {"__truncated__": True, "type": type(obj).__name__, "bytes": len(encoded.encode("utf-8"))}


def _make_request_payload_serializable(
    endpoint: str,
    payload: dict[str, Any],
    max_payload_bytes: int = 250_000,
) -> dict[str, Any]:
    """Serialize an HTTP payload without replacing required API envelopes.

    Event batches can contain image/video/audio payloads. The individual event
    fields are already serialized before batching, so replacing the whole
    ``{"events": [...]}`` envelope with a truncation marker makes the backend
    reject the request with 422.
    """

    if isinstance(payload.get("events"), list):
        return {
            "events": [
                _make_event_payload_serializable(event, max_payload_bytes)
                for event in payload["events"]
            ]
        }
    if endpoint == "/api/generation":
        return _make_generation_create_payload_serializable(payload, max_payload_bytes)
    if endpoint.startswith("/api/generation/"):
        return _make_generation_update_payload_serializable(payload, max_payload_bytes)
    return _make_serializable(payload, max_payload_bytes)


def _make_generation_create_payload_serializable(
    payload: dict[str, Any],
    max_payload_bytes: int = 250_000,
) -> dict[str, Any]:
    serialized = dict(payload)
    if "input" in serialized:
        serialized["input"] = _make_serializable(serialized.get("input"), max_payload_bytes)
    if "metadata_json" in serialized:
        serialized["metadata_json"] = _as_json_object(
            _make_serializable(serialized.get("metadata_json"), max_payload_bytes)
        )
    for key in ("usage_input_tokens", "usage_output_tokens"):
        serialized[key] = _optional_int(serialized.get(key))
    if serialized.get("cost_total") is not None:
        try:
            serialized["cost_total"] = float(serialized["cost_total"])
        except Exception:
            serialized["cost_total"] = None
    return serialized


def _make_generation_update_payload_serializable(
    payload: dict[str, Any],
    max_payload_bytes: int = 250_000,
) -> dict[str, Any]:
    serialized = dict(payload)
    if "output" in serialized:
        serialized["output"] = _make_serializable(serialized.get("output"), max_payload_bytes)
    return serialized


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _make_event_payload_serializable(event: Any, max_payload_bytes: int = 250_000) -> Any:
    if not isinstance(event, dict):
        return _make_serializable(event, max_payload_bytes)
    serialized = dict(event)
    if "data" in serialized:
        serialized["data"] = _as_json_object(
            _make_serializable(serialized.get("data"), max_payload_bytes)
        )
    if "metadata_json" in serialized:
        serialized["metadata_json"] = _as_json_object(
            _make_serializable(serialized.get("metadata_json"), max_payload_bytes)
        )
    return serialized


def _as_json_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return {"value": value}


def _to_plain(obj: Any, *, include_tool_signatures: bool = False) -> Any:
    if isinstance(obj, (int, float, str, bool, type(None))):
        if isinstance(obj, str) and not include_tool_signatures:
            return _sanitize_tool_json_string(obj)
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, type):
        return _class_payload(obj)
    if isinstance(obj, dict):
        return {
            str(k): _to_plain(v, include_tool_signatures=include_tool_signatures)
            for k, v in obj.items()
            if k != "chat" and (include_tool_signatures or str(k) != "signature")
        }
    if isinstance(obj, (list, tuple, set)):
        return [_to_plain(v, include_tool_signatures=include_tool_signatures) for v in obj]
    if _looks_like_tool_result(obj):
        return _tool_result_payload(obj, include_tool_signatures=include_tool_signatures)
    if _looks_like_tool_invocation(obj):
        return _tool_invocation_payload(obj, include_tool_signatures=include_tool_signatures)
    if _looks_like_media_content(obj):
        return _media_content_payload(obj)
    if hasattr(obj, "sender") and hasattr(obj, "content"):
        sender = getattr(obj, "sender", None)
        meta = getattr(obj, "_meta", {}) or {}
        content = getattr(obj, "content", "")
        if _looks_like_tool_result(content):
            content_payload = getattr(content, "text", None) or getattr(content, "output", None) or getattr(content, "error", None)
        elif _looks_like_media_content(content):
            content_payload = _media_content_payload(content)
        else:
            content_payload = getattr(obj, "payload", getattr(obj, "text", content))
        sanitized_meta = _sanitize_tool_payload(meta, include_tool_signatures=include_tool_signatures)
        tool_calls = sanitized_meta.get("tool_calls") or []
        metadata = {key: value for key, value in sanitized_meta.items() if key != "tool_calls"}
        return {
            "role": getattr(sender, "role", None),
            "sender_name": getattr(sender, "name", None),
            "sender_id": getattr(sender, "id", None),
            "content": content_payload,
            "tool_calls": _to_plain(tool_calls, include_tool_signatures=include_tool_signatures),
            "metadata": _to_plain(metadata, include_tool_signatures=include_tool_signatures),
        }
    if hasattr(obj, "to_dict"):
        return _to_plain(obj.to_dict(), include_tool_signatures=include_tool_signatures)
    if hasattr(obj, "model_dump"):
        return _to_plain(obj.model_dump(), include_tool_signatures=include_tool_signatures)
    if hasattr(obj, "__dict__"):
        try:
            return {str(k): _to_plain(v, include_tool_signatures=include_tool_signatures) for k, v in vars(obj).items() if not str(k).startswith("_")}
        except Exception:
            return str(obj)
    return str(obj)


def _class_payload(cls: type[Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "class",
        "module": getattr(cls, "__module__", None),
        "name": getattr(cls, "__qualname__", getattr(cls, "__name__", str(cls))),
    }
    if hasattr(cls, "model_json_schema"):
        try:
            schema = cls.model_json_schema()
            payload["schema"] = {
                "title": schema.get("title"),
                "properties": sorted((schema.get("properties") or {}).keys()),
                "required": schema.get("required", []),
            }
        except Exception:
            pass
    return payload


def _sanitize_tool_payload(value: Any, *, include_tool_signatures: bool = False) -> Any:
    """Remove provider infrastructure fields from captured tool payloads."""

    if include_tool_signatures:
        return value
    if isinstance(value, dict):
        return {
            str(key): _sanitize_tool_payload(
                item,
                include_tool_signatures=include_tool_signatures,
            )
            for key, item in value.items()
            if str(key) != "signature"
        }
    if isinstance(value, list):
        return [
            _sanitize_tool_payload(
                item,
                include_tool_signatures=include_tool_signatures,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _sanitize_tool_payload(
                item,
                include_tool_signatures=include_tool_signatures,
            )
            for item in value
        )
    if isinstance(value, str):
        return _sanitize_tool_json_string(value)
    return value


def _sanitize_tool_json_string(value: str) -> str:
    try:
        parsed = json.loads(value)
    except Exception:
        return value
    if not isinstance(parsed, (dict, list)):
        return value
    sanitized = _sanitize_tool_payload(parsed)
    if sanitized == parsed:
        return value
    return json.dumps(sanitized, separators=(",", ":"))


def _looks_like_tool_invocation(obj: Any) -> bool:
    return all(hasattr(obj, field) for field in ("name", "arguments")) and hasattr(obj, "call_id")


def _looks_like_media_content(obj: Any) -> bool:
    return hasattr(obj, "url") and hasattr(obj, "mime_type")


def _media_content_payload(obj: Any) -> list[dict[str, Any]]:
    url = getattr(obj, "url", "")
    mime_type = getattr(obj, "mime_type", "") or ""
    if mime_type.startswith("audio/"):
        return [{"type": "audio_url", "audio_url": {"url": url}, "mime_type": mime_type}]
    if mime_type.startswith("video/"):
        return [{"type": "video_url", "video_url": {"url": url}, "mime_type": mime_type}]
    return [{"type": "image_url", "image_url": {"url": url}, "mime_type": mime_type}]


def _looks_like_tool_result(obj: Any) -> bool:
    return _looks_like_tool_invocation(obj) and (hasattr(obj, "output") or hasattr(obj, "error"))


def _tool_invocation_payload(obj: Any, *, include_tool_signatures: bool = False) -> dict[str, Any]:
    return {
        "name": getattr(obj, "name", None),
        "arguments": _to_plain(
            _sanitize_tool_payload(
                getattr(obj, "arguments", None),
                include_tool_signatures=include_tool_signatures,
            ),
            include_tool_signatures=include_tool_signatures,
        ),
        "call_id": getattr(obj, "call_id", None),
    }


def _tool_result_payload(obj: Any, *, include_tool_signatures: bool = False) -> dict[str, Any]:
    payload = _tool_invocation_payload(obj, include_tool_signatures=include_tool_signatures)
    payload.update(
        {
            "output": _to_plain(getattr(obj, "output", None), include_tool_signatures=include_tool_signatures),
            "error": getattr(obj, "error", None),
        }
    )
    return payload


def _summarize_output(output: Any) -> Any:
    if not isinstance(output, dict):
        return str(output)
    return _make_serializable({key: value for key, value in output.items() if key != "chat"})


def _usage_dict(usage: Any) -> dict[str, int | None]:
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "input_tokens_cost_nanodollars": getattr(usage, "input_tokens_cost_nanodollars", None),
        "output_tokens_cost_nanodollars": getattr(usage, "output_tokens_cost_nanodollars", None),
        "total_cost_nanodollars": getattr(usage, "total_cost_nanodollars", None),
    }


def _dump_replayable_agent(agent: Any) -> bytes:
    try:
        import cloudpickle

        return cloudpickle.dumps(agent)
    except Exception:
        return _dump_with_kbench_llm_reducers(agent)


def _dump_with_kbench_llm_reducers(agent: Any) -> bytes:
    import cloudpickle

    buffer = io.BytesIO()

    class KbenchPickler(cloudpickle.CloudPickler):
        def reducer_override(self, obj):
            if _is_kbench_llm_chat(obj):
                model_id = getattr(obj, "model", None) or getattr(obj, "name", None)
                return (_restore_kbench_llm, (model_id,))
            return super().reducer_override(obj)

    KbenchPickler(buffer).dump(agent)
    return buffer.getvalue()


def _is_kbench_llm_chat(obj: Any) -> bool:
    try:
        from kaggle_benchmarks.actors import LLMChat

        return isinstance(obj, LLMChat) and (
            hasattr(obj, "model") or hasattr(obj, "client")
        )
    except Exception:
        return False


def _restore_kbench_llm(model_id: str | None):
    from kagraph.llms import load_default_llm, load_llm

    if model_id:
        return load_llm(model_id)
    return load_default_llm()


def _schema_fields(schema_obj: Any) -> list[dict[str, Any]]:
    if not schema_obj or schema_obj is dict:
        return []
    try:
        hints = get_type_hints(schema_obj, include_extras=True)
    except Exception:
        hints = getattr(schema_obj, "__annotations__", {}) or {}
    if not hints:
        return []
    required_keys = set(getattr(schema_obj, "__required_keys__", set()))
    total = bool(getattr(schema_obj, "__total__", True))
    return [
        {
            "name": str(name),
            "type": _schema_type_name(hint),
            "required": str(name) in required_keys if required_keys else total,
        }
        for name, hint in hints.items()
    ]


def _schema_type_name(hint: Any) -> str:
    origin = get_origin(hint)
    if origin is not None and str(origin).endswith("Annotated"):
        args = get_args(hint)
        return _schema_type_name(args[0]) if args else "any"
    if origin is not None:
        name = getattr(origin, "__name__", str(origin))
        args = get_args(hint)
        if args:
            return f"{name}[{', '.join(_schema_type_name(arg) for arg in args[:2])}]"
        return name
    return getattr(hint, "__name__", str(hint).replace("typing.", ""))


def _now_iso() -> str:
    return datetime.utcnow().isoformat()
