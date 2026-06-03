from __future__ import annotations

import contextlib
import asyncio
import inspect
import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Any, Annotated, get_args, get_origin, get_type_hints
from uuid import uuid4

from kaggle_benchmarks import actors, chats, contexts, events
from kaggle_benchmarks.actors import LLMChat
from kaggle_benchmarks.chats import Chat

from kagraph.checkpoint.base import BaseCheckpointer, StateSnapshot
from kagraph.constants import END, START
from kagraph.errors import (
    CycleError,
    GraphRecursionError,
    InvalidGraphError,
    InvalidUpdateError,
    NodeError,
    NodeTimeoutError,
)
from kagraph.graph._branch import BranchSpec
from kagraph.graph._node import PregelNode, StateNodeSpec
from kagraph.graph.visualization import GraphEdge, KaGraphView
from kagraph.runtime import Runtime, runtime_scope
from kagraph.types import CachePolicy, Command, GraphInterrupt, RetryPolicy, Send, TimeoutPolicy

NodeFn = Callable[..., Any]
RouterFn = Callable[..., Hashable | list[Hashable]]

_BRANCH_PREFIX = "__branch_to__:"
_DEFAULT_RECURSION_LIMIT = 100
_USE_CHANNEL_KEYS = object()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Packet:
    node: str
    arg: Any = None


@dataclass(frozen=True)
class _StreamRecord:
    node: str
    update: dict[str, Any]
    state: dict[str, Any]
    step: int = 0


class StateGraph:
    """LangGraph-style graph builder backed by kaggle-benchmarks chats."""

    def __init__(
        self,
        state_schema: type[Any] | None = None,
        context_schema: type[Any] | None = None,
        *,
        input_schema: type[Any] | None = None,
        output_schema: type[Any] | None = None,
    ) -> None:
        self.state_schema = state_schema or dict
        self.context_schema = context_schema
        self.input_schema = input_schema or self.state_schema
        self.output_schema = output_schema or self.state_schema
        self.nodes: dict[str, StateNodeSpec] = {}
        self.edges: set[tuple[str, str]] = set()
        self.waiting_edges: set[tuple[tuple[str, ...], str]] = set()
        self.branches: defaultdict[str, dict[str, BranchSpec]] = defaultdict(dict)
        self.reducers = _get_reducers(self.state_schema)
        self.compiled = False

    def add_node(
        self,
        node: str | NodeFn | LLMChat,
        action: NodeFn | LLMChat | None = None,
        *,
        defer: bool = False,
        metadata: dict[str, Any] | None = None,
        input_schema: type[Any] | None = None,
        retry_policy: RetryPolicy | Sequence[RetryPolicy] | None = None,
        cache_policy: CachePolicy | None = None,
        error_handler: NodeFn | None = None,
        destinations: dict[str, str] | tuple[str, ...] | None = None,
        timeout: float | TimeoutPolicy | None = None,
        **_: Any,
    ) -> "StateGraph":
        if not isinstance(node, str):
            action = node
            name = _get_node_name(action)
        else:
            name = node
        if action is None:
            raise ValueError("Node action must be provided.")
        if name in {START, END}:
            raise InvalidGraphError(f"{name!r} is reserved and cannot be a node.")
        if name in self.nodes:
            raise InvalidGraphError(f"Node {name!r} already exists.")
        if ":" in name:
            raise InvalidGraphError("':' is reserved and cannot be used in node names.")

        handler_name = None
        if error_handler is not None:
            handler_name = f"__error_handler__{name}"
            if handler_name in self.nodes:
                raise InvalidGraphError(f"Node {handler_name!r} already exists.")
            self.nodes[handler_name] = StateNodeSpec(
                runnable=error_handler,
                metadata=None,
                input_schema=input_schema or self.state_schema,
                is_error_handler=True,
            )

        self.nodes[name] = StateNodeSpec(
            runnable=_coerce_node(action),
            metadata=metadata,
            input_schema=input_schema or _infer_input_schema(action) or self.state_schema,
            retry_policy=tuple(retry_policy) if isinstance(retry_policy, Sequence) and not isinstance(retry_policy, RetryPolicy) else retry_policy,
            cache_policy=cache_policy,
            error_handler_node=handler_name,
            ends=destinations or _infer_command_ends(action),
            defer=defer,
            timeout=TimeoutPolicy.coerce(timeout),
        )
        return self

    def add_edge(self, start_key: str | list[str] | tuple[str, ...], end_key: str) -> "StateGraph":
        if isinstance(start_key, str):
            self._validate_edge_endpoint(start_key, is_start=True)
            self._validate_edge_endpoint(end_key, is_start=False)
            self.edges.add((start_key, end_key))
        else:
            if not start_key:
                raise InvalidGraphError("Waiting edge requires at least one start node.")
            for start in start_key:
                self._validate_edge_endpoint(start, is_start=True)
            self._validate_edge_endpoint(end_key, is_start=False)
            self.waiting_edges.add((tuple(start_key), end_key))
        return self

    def add_conditional_edges(
        self,
        source: str,
        path: RouterFn,
        path_map: dict[Hashable, str] | list[str] | None = None,
    ) -> "StateGraph":
        if source != START and source not in self.nodes:
            raise InvalidGraphError(f"Conditional edge source {source!r} is not a node.")
        name = _get_node_name(path) or "condition"
        if name in self.branches[source]:
            raise InvalidGraphError(f"Branch {name!r} already exists for {source!r}.")
        ends = None
        if isinstance(path_map, dict):
            ends = dict(path_map)
        elif isinstance(path_map, list):
            ends = {item: item for item in path_map}
        if ends:
            for dst in ends.values():
                self._validate_edge_endpoint(dst, is_start=False)
        self.branches[source][name] = BranchSpec(
            path=path,
            ends=ends,
            input_schema=_infer_input_schema(path),
            name=name,
        )
        return self

    def set_entry_point(self, key: str) -> "StateGraph":
        return self.add_edge(START, key)

    def set_finish_point(self, key: str) -> "StateGraph":
        return self.add_edge(key, END)

    def add_sequence(self, nodes: Sequence[NodeFn | tuple[str, NodeFn]]) -> "StateGraph":
        previous = None
        for item in nodes:
            if isinstance(item, tuple):
                name, node = item
            else:
                node = item
                name = _get_node_name(node)
            self.add_node(name, node)
            if previous is not None:
                self.add_edge(previous, name)
            previous = name
        return self

    def compile(
        self,
        checkpointer: BaseCheckpointer | None = None,
        *,
        interrupt_before: list[str] | str | None = None,
        interrupt_after: list[str] | str | None = None,
        debug: bool = False,
        name: str | None = None,
        auto_log_to_chat: bool = False,
        **_: Any,
    ) -> "CompiledStateGraph":
        self.validate()
        channel_keys = _collect_schema_keys(
            self.state_schema,
            self.input_schema,
            self.output_schema,
            *(spec.input_schema for spec in self.nodes.values()),
            *(
                branch.input_schema
                for branch_by_name in self.branches.values()
                for branch in branch_by_name.values()
            ),
        )
        reducers = _collect_reducers(
            self.state_schema,
            self.input_schema,
            self.output_schema,
            *(spec.input_schema for spec in self.nodes.values()),
            *(
                branch.input_schema
                for branch_by_name in self.branches.values()
                for branch in branch_by_name.values()
            ),
        )
        nodes = {
            node_name: PregelNode(
                name=node_name,
                bound=spec.runnable,
                triggers=[_BRANCH_PREFIX + node_name],
                channels=_node_channels(spec.input_schema),
                input_schema=spec.input_schema,
                metadata=spec.metadata,
                retry_policy=spec.retry_policy,
                cache_policy=spec.cache_policy,
                is_error_handler=spec.is_error_handler,
                error_handler_node=spec.error_handler_node,
                ends=spec.ends,
                defer=spec.defer,
                timeout=spec.timeout,
            )
            for node_name, spec in self.nodes.items()
        }
        return CompiledStateGraph(
            builder=self,
            nodes=nodes,
            edges=set(self.edges),
            waiting_edges=set(self.waiting_edges),
            branches={key: dict(value) for key, value in self.branches.items()},
            reducers=reducers,
            state_keys=_schema_keys(self.state_schema),
            input_keys=_schema_keys(self.input_schema),
            output_keys=_schema_keys(self.output_schema),
            channel_keys=channel_keys,
            checkpointer=checkpointer,
            interrupt_before=_normalize_interrupts(interrupt_before),
            interrupt_after=_normalize_interrupts(interrupt_after),
            debug=debug,
            name=name or "KaGraph",
            auto_log_to_chat=auto_log_to_chat,
        )

    def validate(self, interrupt: Sequence[str] | None = None) -> "StateGraph":
        if not self.nodes:
            raise InvalidGraphError("Graph must contain at least one node.")
        sources = {src for src, _ in self.edges}
        sources.update(src for srcs, _ in self.waiting_edges for src in srcs)
        sources.update(self.branches)
        sources.update(name for name, spec in self.nodes.items() if spec.ends)
        if START not in sources:
            raise InvalidGraphError("Graph must have an edge from START.")
        for src in sources:
            if src != START and src not in self.nodes:
                raise InvalidGraphError(f"Unknown edge source {src!r}.")
        targets = {dst for _, dst in self.edges}
        targets.update(dst for _, dst in self.waiting_edges)
        for branch_by_name in self.branches.values():
            for branch in branch_by_name.values():
                if branch.ends:
                    targets.update(branch.ends.values())
        for name, spec in self.nodes.items():
            if spec.ends:
                targets.update(spec.ends if isinstance(spec.ends, tuple) else spec.ends.values())
        for dst in targets:
            if dst != END and dst not in self.nodes:
                raise InvalidGraphError(f"Unknown edge destination {dst!r}.")
        for node in interrupt or ():
            if node not in self.nodes:
                raise InvalidGraphError(f"Interrupt node {node!r} not found.")
        self._validate_reachability()
        self._validate_unconditional_cycles()
        return self

    def _validate_edge_endpoint(self, name: str, *, is_start: bool) -> None:
        if is_start and name == END:
            raise InvalidGraphError("END cannot be an edge source.")
        if not is_start and name == START:
            raise InvalidGraphError("START cannot be an edge destination.")
        if name not in {START, END} and name not in self.nodes:
            raise InvalidGraphError(f"Unknown graph node {name!r}.")

    def _validate_reachability(self) -> None:
        adjacency = self._adjacency(include_branches=True)
        seen: set[str] = set()
        queue = deque([START])
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(adjacency[node] - seen)
        unreachable = set(self.nodes) - seen
        if unreachable:
            names = ", ".join(sorted(unreachable))
            raise InvalidGraphError(f"Unreachable node(s): {names}.")
        if END not in seen:
            raise InvalidGraphError("END is not reachable from START.")

    def _validate_unconditional_cycles(self) -> None:
        adjacency = self._adjacency(include_branches=False)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node == END:
                return
            if node in visiting:
                raise CycleError(f"Unconditional cycle detected at {node!r}.")
            if node in visited:
                return
            visiting.add(node)
            for nxt in adjacency[node]:
                visit(nxt)
            visiting.remove(node)
            visited.add(node)

        visit(START)

    def _adjacency(self, *, include_branches: bool) -> defaultdict[str, set[str]]:
        adjacency: defaultdict[str, set[str]] = defaultdict(set)
        for src, dst in self.edges:
            adjacency[src].add(dst)
        for starts, end in self.waiting_edges:
            for start in starts:
                adjacency[start].add(end)
        if include_branches:
            for src, branch_by_name in self.branches.items():
                for branch in branch_by_name.values():
                    if branch.ends:
                        adjacency[src].update(branch.ends.values())
                    else:
                        adjacency[src].update(set(self.nodes) | {END})
            for name, spec in self.nodes.items():
                if spec.ends:
                    adjacency[name].update(spec.ends if isinstance(spec.ends, tuple) else spec.ends.values())
        return adjacency


@dataclass
class CompiledStateGraph:
    builder: StateGraph
    nodes: dict[str, PregelNode]
    edges: set[tuple[str, str]]
    waiting_edges: set[tuple[tuple[str, ...], str]]
    branches: dict[str, dict[str, BranchSpec]]
    reducers: dict[str, Callable[[Any, Any], Any]]
    state_keys: set[str] | None
    input_keys: set[str] | None
    output_keys: set[str] | None
    channel_keys: set[str] | None
    checkpointer: BaseCheckpointer | None = None
    interrupt_before: set[str] | None = None
    interrupt_after: set[str] | None = None
    debug: bool = False
    name: str = "KaGraph"
    default_system_instructions: str | None = None
    auto_log_to_chat: bool = False

    def get_graph(self) -> KaGraphView:
        graph_nodes = []
        graph_edges = []
        
        def _build(g: "CompiledStateGraph", prefix: str):
            expanded_nodes = set()
            
            for n in sorted(g.nodes):
                bound = g.nodes[n].bound
                if hasattr(bound, "__kagraph_subgraph__"):
                    bound = bound.__kagraph_subgraph__
                if isinstance(bound, CompiledStateGraph):
                    expanded_nodes.add(n)
                    _build(bound, f"{prefix}{n}:")
                else:
                    graph_nodes.append(f"{prefix}{n}")
                    
            graph_nodes.append(f"{prefix}{START}")
            graph_nodes.append(f"{prefix}{END}")
            
            def _map_node(n: str, is_target: bool) -> str:
                if n in expanded_nodes:
                    return f"{prefix}{n}:{START}" if is_target else f"{prefix}{n}:{END}"
                return f"{prefix}{n}"
                
            for src, dst in sorted(g.edges, key=lambda item: (item[0], item[1])):
                graph_edges.append(GraphEdge(
                    source=_map_node(src, False),
                    target=_map_node(dst, True)
                ))
            
            for starts, end in sorted(g.waiting_edges, key=lambda item: (item[1], item[0])):
                for start in starts:
                    graph_edges.append(GraphEdge(
                        source=_map_node(start, False),
                        target=_map_node(end, True),
                        label="join",
                        conditional=True
                    ))
                    
            for src, branches in sorted(g.branches.items(), key=lambda item: item[0]):
                for branch in sorted(branches.values(), key=lambda item: item.name):
                    if branch.ends:
                        for label, dst in sorted(branch.ends.items(), key=lambda item: (str(item[1]), str(item[0]))):
                            graph_edges.append(GraphEdge(
                                source=_map_node(src, False),
                                target=_map_node(dst, True),
                                label=str(label),
                                conditional=True
                            ))
                    else:
                        for dst in sorted(g.nodes):
                            if dst != src:
                                graph_edges.append(GraphEdge(
                                    source=_map_node(src, False),
                                    target=_map_node(dst, True),
                                    label=branch.name,
                                    conditional=True
                                ))
                        graph_edges.append(GraphEdge(
                            source=_map_node(src, False),
                            target=_map_node(END, True),
                            label=branch.name,
                            conditional=True
                        ))
                        
        _build(self, "")
        return KaGraphView(nodes=graph_nodes, edges=graph_edges)

    def invoke(
        self,
        input: Any = None,
        config: dict[str, Any] | None = None,
        *,
        initial_state: dict[str, Any] | None = None,
        chat_name: str = "kagraph_run",
        system_instructions: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        context: dict[str, Any] | None = None,
        recursion_limit: int = _DEFAULT_RECURSION_LIMIT,
        **_: Any,
    ) -> dict[str, Any]:
        return self._invoke_internal(
            input=input,
            config=config,
            initial_state=initial_state,
            chat_name=chat_name,
            system_instructions=system_instructions,
            session_id=session_id,
            user_id=user_id,
            context=context,
            recursion_limit=recursion_limit,
            stream_records=None,
        )

    def stream(
        self,
        input: Any = None,
        config: dict[str, Any] | None = None,
        *,
        stream_mode: str | Sequence[str] = "values",
        **kwargs: Any,
    ):
        modes = _normalize_stream_modes(stream_mode)
        if not modes.issubset({"values", "updates", "events"}):
            raise ValueError("stream_mode must be one of: 'values', 'updates', 'events'.")
        if modes == {"events"}:
            yield from self._stream_events_live(input=input, config=config, **kwargs)
            return

        records: list[_StreamRecord] = []
        stream_events: list[dict[str, Any]] = [] if "events" in modes else None
        output = self._invoke_internal(
            input=input,
            config=config,
            stream_records=records,
            stream_events=stream_events,
            **kwargs,
        )
        yielded = False
        for event in stream_events or []:
            yielded = True
            yield _format_stream_payload("events", event, modes)
        for record in records:
            if "updates" in modes and record.update:
                yielded = True
                yield _format_stream_payload("updates", {record.node: record.update}, modes)
            if "values" in modes:
                yielded = True
                yield _format_stream_payload("values", {"chat": output["chat"], **record.state}, modes)
        if not yielded:
            yield output

    async def ainvoke(self, input: Any = None, config: dict[str, Any] | None = None, **kwargs: Any):
        return await self._ainvoke_internal(input=input, config=config, stream_records=None, **kwargs)

    async def astream(self, input: Any = None, config: dict[str, Any] | None = None, **kwargs: Any):
        stream_mode = kwargs.pop("stream_mode", "values")
        modes = _normalize_stream_modes(stream_mode)
        if not modes.issubset({"values", "updates", "events"}):
            raise ValueError("stream_mode must be one of: 'values', 'updates', 'events'.")
        if modes == {"events"}:
            async for event in self._astream_events_live(input=input, config=config, **kwargs):
                yield event
            return
        records: list[_StreamRecord] = []
        stream_events: list[dict[str, Any]] = [] if "events" in modes else None
        output = await self._ainvoke_internal(
            input=input,
            config=config,
            stream_records=records,
            stream_events=stream_events,
            **kwargs,
        )
        yielded = False
        for event in stream_events or []:
            yielded = True
            yield _format_stream_payload("events", event, modes)
        for record in records:
            if "updates" in modes and record.update:
                yielded = True
                yield _format_stream_payload("updates", {record.node: record.update}, modes)
            if "values" in modes:
                yielded = True
                yield _format_stream_payload("values", {"chat": output["chat"], **record.state}, modes)
        if not yielded:
            yield output

    def _stream_events_live(self, input: Any = None, config: dict[str, Any] | None = None, **kwargs: Any):
        import queue
        import threading

        event_queue: queue.Queue[Any] = queue.Queue()
        done = object()

        class LiveEvents(list):
            def append(self, item):
                super().append(item)
                event_queue.put(item)

        def run() -> None:
            try:
                self._invoke_internal(
                    input=input,
                    config=config,
                    stream_records=None,
                    stream_events=LiveEvents(),
                    **kwargs,
                )
            except BaseException as error:
                event_queue.put(error)
            finally:
                event_queue.put(done)

        thread = threading.Thread(target=run, name=f"{self.name}-stream", daemon=True)
        thread.start()
        while True:
            item = event_queue.get()
            if item is done:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
        thread.join()

    async def _astream_events_live(self, input: Any = None, config: dict[str, Any] | None = None, **kwargs: Any):
        event_queue: asyncio.Queue[Any] = asyncio.Queue()
        done = object()

        class LiveEvents(list):
            def append(self, item):
                super().append(item)
                event_queue.put_nowait(item)

        async def run() -> None:
            try:
                await self._ainvoke_internal(
                    input=input,
                    config=config,
                    stream_records=None,
                    stream_events=LiveEvents(),
                    **kwargs,
                )
            except BaseException as error:
                event_queue.put_nowait(error)
            finally:
                event_queue.put_nowait(done)

        task = asyncio.create_task(run())
        while True:
            item = await event_queue.get()
            if item is done:
                break
            if isinstance(item, BaseException):
                await task
                raise item
            yield item
        await task

    async def _ainvoke_internal(
        self,
        *,
        input: Any = None,
        config: dict[str, Any] | None = None,
        initial_state: dict[str, Any] | None = None,
        chat_name: str = "kagraph_run",
        system_instructions: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        context: dict[str, Any] | None = None,
        recursion_limit: int = _DEFAULT_RECURSION_LIMIT,
        stream_records: list[_StreamRecord] | None = None,
        stream_events: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        instructions = self.default_system_instructions if system_instructions is None else system_instructions
        _emit_stream_event(stream_events, "on_graph_start", self.name, {"input": input})
        events.manager.dispatch(
            "kagraph_invoke_start",
            input=input,
            graph=self,
            session_id=session_id,
            user_id=user_id,
            config=config,
            context=context,
            recursion_limit=recursion_limit,
            chat_name=chat_name,
            system_instructions=instructions,
        )
        state = self._initial_state(input, initial_state)
        thread_id = _thread_id(config)
        saved: dict[str, Any] | None = None
        saved_chat: Chat | None = None
        resume_packets: list[_Packet] | None = None
        resume_values: list[Any] | None = None
        if isinstance(input, Command) and input.resume is not None:
            resume_values = input.resume if isinstance(input.resume, list) else [input.resume]
            if input.update:
                self._apply_update(state, input.update)
        if self.checkpointer and thread_id:
            saved = _get_checkpoint_from_saver(self.checkpointer, thread_id, _checkpoint_id(config))
            if saved:
                state.update(saved.get("state", saved))
                if isinstance(input, dict):
                    self._apply_checkpoint_input(state, input)
                if isinstance(input, Command) and input.update:
                    self._apply_update(state, input.update)
                if resume_values is not None or (input is None and saved.get("next")):
                    resume_packets = [_Packet(node, arg) for node, arg in saved.get("next", [])]
                chat_value = saved.get("chat") if isinstance(saved, dict) else None
                if isinstance(chat_value, Chat):
                    saved_chat = chat_value
        listener = _KbenchStreamListener(stream_events, self.name) if stream_events is not None else None
        if listener is not None:
            events.manager.bind(listener)
        try:
            with _enter_graph_chat(chat_name=chat_name, system_instructions=instructions, existing_chat=saved_chat) as chat:
                if input is not None and not isinstance(input, (dict, Command)):
                    actors.user.send(str(input))
                runtime_context = {
                    **dict(config.get("configurable", {}) if config else {}),
                    **dict(context or {}),
                }
                if resume_values is not None:
                    runtime_context["__kagraph_resume_values__"] = resume_values
                    runtime_context["__kagraph_resume_index__"] = 0
                if saved and _should_skip_saved_interrupt(saved, resume_values, input):
                    runtime_context["__kagraph_skip_interrupt__"] = saved.get("interrupt")
                runtime = Runtime(
                    chat=chat,
                    context=runtime_context,
                    config=config or {},
                    writer=_make_runtime_writer(stream_events),
                )
                with runtime_scope(runtime):
                    await self._arun_packets(
                        chat=chat,
                        runtime=runtime,
                        state=state,
                        packets=resume_packets or [_Packet(START)],
                        recursion_limit=recursion_limit,
                        thread_id=thread_id,
                        stream_records=stream_records,
                        stream_events=stream_events,
                        checkpoint_config=config,
                        checkpoint_state=saved if saved else None,
                    )
                self._checkpoint(
                    thread_id,
                    state,
                    chat,
                    [],
                    metadata={"source": "final"},
                    config=config,
                    channel_versions=runtime.context.get("__kagraph_channel_versions__", {}),
                    versions_seen=runtime.context.get("__kagraph_versions_seen__", {}),
                )
                output = {"chat": chat, **self._output_state(state)}
                _emit_stream_event(stream_events, "on_graph_end", self.name, {"output": output})
                events.manager.dispatch("kagraph_invoke_end", input=input, output=output, graph=self, session_id=session_id, user_id=user_id)
                return output
        except BaseException as error:
            _emit_stream_event(stream_events, "on_graph_error", self.name, {"error": error})
            events.manager.dispatch("kagraph_invoke_error", input=input, error=error, graph=self, session_id=session_id, user_id=user_id)
            raise
        finally:
            if listener is not None:
                events.manager.unbind(listener)

    def validate(self) -> None:
        self.builder.validate()

    def get_state(self, config: dict[str, Any]) -> StateSnapshot:
        saved = self._get_checkpoint(config)
        return _to_snapshot(saved, config)

    def get_state_history(self, config: dict[str, Any]) -> list[StateSnapshot]:
        thread_id = _thread_id(config)
        if not self.checkpointer or not thread_id:
            return []
        history_fn = getattr(self.checkpointer, "list", None)
        if history_fn is None:
            saved = self.checkpointer.get(thread_id)
            return [_to_snapshot(saved, config)] if saved else []
        return [_to_snapshot(item, item.get("config", config)) for item in history_fn(thread_id)]

    def update_state(
        self,
        config: dict[str, Any],
        values: dict[str, Any],
        as_node: str | None = None,
    ) -> dict[str, Any]:
        saved = self._get_checkpoint(config)
        state = dict(saved.get("state", saved))
        applied = self._apply_update(state, values)
        next_packets = [_Packet(node, arg) for node, arg in saved.get("next", [])]
        chat = saved.get("chat")
        if not isinstance(chat, Chat):
            chat = Chat(name="checkpoint")
        checkpoint = self._checkpoint(
            _thread_id(config),
            state,
            chat,
            next_packets,
            metadata={"source": "update_state", "as_node": as_node},
            config=config,
            channel_versions=dict(saved.get("channel_versions", {})),
            versions_seen=dict(saved.get("versions_seen", {})),
            pending_writes=[(as_node or "__update_state__", key, value, 0) for key, value in applied.items()],
        )
        return _config_with_checkpoint_id(config, checkpoint)

    def _get_checkpoint(self, config: dict[str, Any]) -> dict[str, Any]:
        thread_id = _thread_id(config)
        if not self.checkpointer or not thread_id:
            raise InvalidGraphError("A checkpointer and config['configurable']['thread_id'] are required.")
        saved = _get_checkpoint_from_saver(self.checkpointer, thread_id, _checkpoint_id(config))
        if not saved:
            raise InvalidGraphError(f"No checkpoint found for thread_id {thread_id!r}.")
        return saved

    def _invoke_internal(
        self,
        *,
        input: Any = None,
        config: dict[str, Any] | None = None,
        initial_state: dict[str, Any] | None = None,
        chat_name: str = "kagraph_run",
        system_instructions: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        context: dict[str, Any] | None = None,
        recursion_limit: int = _DEFAULT_RECURSION_LIMIT,
        stream_records: list[_StreamRecord] | None = None,
        stream_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        instructions = self.default_system_instructions if system_instructions is None else system_instructions
        _emit_stream_event(stream_events, "on_graph_start", self.name, {"input": input})
        events.manager.dispatch(
            "kagraph_invoke_start",
            input=input,
            graph=self,
            session_id=session_id,
            user_id=user_id,
            config=config,
            context=context,
            recursion_limit=recursion_limit,
            chat_name=chat_name,
            system_instructions=instructions,
        )
        state = self._initial_state(input, initial_state)
        thread_id = _thread_id(config)
        saved: dict[str, Any] | None = None
        saved_chat: Chat | None = None
        resume_packets: list[_Packet] | None = None
        resume_values: list[Any] | None = None
        if isinstance(input, Command) and input.resume is not None:
            resume_values = input.resume if isinstance(input.resume, list) else [input.resume]
            if input.update:
                self._apply_update(state, input.update)
        if self.checkpointer and thread_id:
            saved = _get_checkpoint_from_saver(self.checkpointer, thread_id, _checkpoint_id(config))
            if saved:
                state.update(saved.get("state", saved))
                if isinstance(input, dict):
                    self._apply_checkpoint_input(state, input)
                if isinstance(input, Command) and input.update:
                    self._apply_update(state, input.update)
                if resume_values is not None or (input is None and saved.get("next")):
                    resume_packets = [
                        _Packet(node, arg)
                        for node, arg in saved.get("next", [])
                    ]
                chat_value = saved.get("chat") if isinstance(saved, dict) else None
                if isinstance(chat_value, Chat):
                    saved_chat = chat_value
        listener = _KbenchStreamListener(stream_events, self.name) if stream_events is not None else None
        if listener is not None:
            events.manager.bind(listener)
        try:
            with _enter_graph_chat(
                chat_name=chat_name,
                system_instructions=instructions,
                existing_chat=saved_chat,
            ) as chat:
                if input is not None and not isinstance(input, (dict, Command)):
                    actors.user.send(str(input))
                runtime_context = {
                    **dict(config.get("configurable", {}) if config else {}),
                    **dict(context or {}),
                }
                if resume_values is not None:
                    runtime_context["__kagraph_resume_values__"] = resume_values
                    runtime_context["__kagraph_resume_index__"] = 0
                if saved and _should_skip_saved_interrupt(saved, resume_values, input):
                    runtime_context["__kagraph_skip_interrupt__"] = saved.get("interrupt")
                runtime = Runtime(
                    chat=chat,
                    context=runtime_context,
                    config=config or {},
                    writer=_make_runtime_writer(stream_events),
                )
                with runtime_scope(runtime):
                    self._run_packets(
                        chat=chat,
                        runtime=runtime,
                        state=state,
                        packets=resume_packets or [_Packet(START)],
                        recursion_limit=recursion_limit,
                        thread_id=thread_id,
                        stream_records=stream_records,
                        stream_events=stream_events,
                        checkpoint_config=config,
                        checkpoint_state=saved if saved else None,
                    )
                self._checkpoint(
                    thread_id,
                    state,
                    chat,
                    [],
                    metadata={"source": "final"},
                    config=config,
                    channel_versions=runtime.context.get("__kagraph_channel_versions__", {}),
                    versions_seen=runtime.context.get("__kagraph_versions_seen__", {}),
                )
                output = {"chat": chat, **self._output_state(state)}
                _emit_stream_event(stream_events, "on_graph_end", self.name, {"output": output})
                events.manager.dispatch(
                    "kagraph_invoke_end",
                    input=input,
                    output=output,
                    graph=self,
                    session_id=session_id,
                    user_id=user_id,
                )
                return output
        except BaseException as error:
            _emit_stream_event(stream_events, "on_graph_error", self.name, {"error": error})
            events.manager.dispatch(
                "kagraph_invoke_error",
                input=input,
                error=error,
                graph=self,
                session_id=session_id,
                user_id=user_id,
            )
            raise
        finally:
            if listener is not None:
                events.manager.unbind(listener)

    def _initial_state(self, input: Any, initial_state: dict[str, Any] | None) -> dict[str, Any]:
        state = dict(initial_state or {})
        if self.channel_keys is not None:
            state = {key: value for key, value in state.items() if key in self.channel_keys}
        if isinstance(input, dict):
            self._apply_update(state, input, allowed_keys=self.input_keys, warn_unknown=True)
        return state

    def _run_packets(
        self,
        *,
        chat: Chat,
        runtime: Runtime,
        state: dict[str, Any],
        packets: list[_Packet],
        recursion_limit: int,
        thread_id: str | None,
        stream_records: list[_StreamRecord] | None = None,
        stream_events: list[dict[str, Any]] | None = None,
        checkpoint_config: dict[str, Any] | None = None,
        checkpoint_state: dict[str, Any] | None = None,
    ) -> None:
        completed: set[str] = set()
        waiting_seen: defaultdict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
        cache: dict[tuple[str, str | bytes], tuple[float, Any]] = {}
        channel_versions = dict((checkpoint_state or {}).get("channel_versions", {}))
        versions_seen = {
            node: dict(versions)
            for node, versions in (checkpoint_state or {}).get("versions_seen", {}).items()
        }
        step = 0
        while packets:
            if step >= recursion_limit:
                raise GraphRecursionError(f"Recursion limit of {recursion_limit} reached.")
            _emit_stream_event(
                stream_events,
                "on_step_start",
                self.name,
                {"step": step, "next": [(packet.node, packet.arg) for packet in packets]},
                metadata={"step": step},
            )
            events.manager.dispatch(
                "kagraph_step_start",
                graph=self.name,
                step=step,
                next=[(packet.node, packet.arg) for packet in packets],
                state=dict(state),
            )
            next_packets: list[_Packet] = []
            for packet in sorted(packets, key=lambda item: item.node):
                if packet.node == END:
                    completed.add(END)
                    continue
                if packet.node == START:
                    next_packets.extend(_Packet(dst) for src, dst in self.edges if src == START)
                    if START in self.branches:
                        next_packets.extend(self._destinations(START, None, chat, runtime, state))
                    completed.add(START)
                    continue
                if packet.node not in self.nodes:
                    raise InvalidGraphError(f"Unknown node {packet.node!r}.")
                if self.interrupt_before is not None and (
                    not self.interrupt_before or packet.node in self.interrupt_before
                ) and not _consume_interrupt_skip(runtime, packet.node, "before"):
                    interrupt = {"node": packet.node, "when": "before"}
                    self._checkpoint(
                        thread_id,
                        state,
                        chat,
                        [packet],
                        interrupt=interrupt,
                        metadata={"step": step, "source": "interrupt_before"},
                        config=checkpoint_config,
                        channel_versions=channel_versions,
                        versions_seen=versions_seen,
                    )
                    raise GraphInterrupt(interrupt)
                _emit_stream_event(
                    stream_events,
                    "on_node_start",
                    packet.node,
                    {"input": dict(state) if packet.arg is None else packet.arg, "state": dict(state)},
                    metadata={"step": step},
                )
                versions_seen[packet.node] = dict(channel_versions)
                try:
                    result = self._execute_node(packet.node, packet.arg, chat, runtime, state, cache)
                except GraphInterrupt as error:
                    self._checkpoint(
                        thread_id,
                        state,
                        chat,
                        [packet],
                        interrupt=error.value,
                        metadata={"step": step, "source": "interrupt"},
                        config=checkpoint_config,
                        channel_versions=channel_versions,
                        versions_seen=versions_seen,
                    )
                    raise
                pending_writes = _bump_channel_versions(channel_versions, packet.node, result.update)
                _emit_stream_event(
                    stream_events,
                    "on_node_end",
                    packet.node,
                    {"update": dict(result.update), "state": dict(state)},
                    metadata={"step": step},
                )
                if result.update:
                    events.manager.dispatch(
                        "kagraph_node_update",
                        node=packet.node,
                        update=dict(result.update),
                        state=dict(state),
                        channel_versions=dict(channel_versions),
                        step=step,
                    )
                    _emit_stream_event(
                        stream_events,
                        "on_node_update",
                        packet.node,
                        {"update": dict(result.update), "channel_versions": dict(channel_versions)},
                        metadata={"step": step},
                    )
                if stream_records is not None:
                    stream_records.append(
                        _StreamRecord(
                            node=packet.node,
                            update=dict(result.update),
                            state=dict(state),
                            step=step,
                        )
                    )
                completed.add(packet.node)
                destinations = self._destinations(packet.node, result.command, chat, runtime, state)
                next_packets.extend(destinations)
                for starts, end in self.waiting_edges:
                    if packet.node in starts:
                        waiting_seen[(starts, end)].add(packet.node)
                    if set(starts).issubset(waiting_seen[(starts, end)]):
                        next_packets.append(_Packet(end))
                if self.interrupt_after is not None and (
                    not self.interrupt_after or packet.node in self.interrupt_after
                ):
                    interrupt = {"node": packet.node, "when": "after"}
                    self._checkpoint(
                        thread_id,
                        state,
                        chat,
                        next_packets,
                        interrupt=interrupt,
                        metadata={"step": step, "source": "interrupt_after"},
                        config=checkpoint_config,
                        channel_versions=channel_versions,
                        versions_seen=versions_seen,
                        pending_writes=pending_writes,
                    )
                    raise GraphInterrupt(interrupt)
                self._checkpoint(
                    thread_id,
                    state,
                    chat,
                    next_packets,
                    metadata={"step": step, "source": packet.node},
                    config=checkpoint_config,
                    channel_versions=channel_versions,
                    versions_seen=versions_seen,
                    pending_writes=pending_writes,
                )
            packets = _dedupe_packets(next_packets)
            _emit_stream_event(
                stream_events,
                "on_step_end",
                self.name,
                {"step": step, "next": [(packet.node, packet.arg) for packet in packets], "state": dict(state)},
                metadata={"step": step},
            )
            events.manager.dispatch(
                "kagraph_step_end",
                graph=self.name,
                step=step,
                next=[(packet.node, packet.arg) for packet in packets],
                state=dict(state),
            )
            step += 1
        runtime.context["__kagraph_channel_versions__"] = dict(channel_versions)
        runtime.context["__kagraph_versions_seen__"] = {
            node: dict(versions) for node, versions in versions_seen.items()
        }
        if END not in completed:
            raise InvalidGraphError("END is not reachable from START.")

    async def _arun_packets(
        self,
        *,
        chat: Chat,
        runtime: Runtime,
        state: dict[str, Any],
        packets: list[_Packet],
        recursion_limit: int,
        thread_id: str | None,
        stream_records: list[_StreamRecord] | None = None,
        stream_events: list[dict[str, Any]] | None = None,
        checkpoint_config: dict[str, Any] | None = None,
        checkpoint_state: dict[str, Any] | None = None,
    ) -> None:
        completed: set[str] = set()
        waiting_seen: defaultdict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
        cache: dict[tuple[str, str | bytes], tuple[float, Any]] = {}
        channel_versions = dict((checkpoint_state or {}).get("channel_versions", {}))
        versions_seen = {
            node: dict(versions)
            for node, versions in (checkpoint_state or {}).get("versions_seen", {}).items()
        }
        step = 0
        while packets:
            if step >= recursion_limit:
                raise GraphRecursionError(f"Recursion limit of {recursion_limit} reached.")
            _emit_stream_event(
                stream_events,
                "on_step_start",
                self.name,
                {"step": step, "next": [(packet.node, packet.arg) for packet in packets]},
                metadata={"step": step},
            )
            events.manager.dispatch(
                "kagraph_step_start",
                graph=self.name,
                step=step,
                next=[(packet.node, packet.arg) for packet in packets],
                state=dict(state),
            )
            next_packets: list[_Packet] = []
            for packet in sorted(packets, key=lambda item: item.node):
                if packet.node == END:
                    completed.add(END)
                    continue
                if packet.node == START:
                    next_packets.extend(_Packet(dst) for src, dst in self.edges if src == START)
                    if START in self.branches:
                        next_packets.extend(await self._adestinations(START, None, chat, runtime, state))
                    completed.add(START)
                    continue
                if packet.node not in self.nodes:
                    raise InvalidGraphError(f"Unknown node {packet.node!r}.")
                if self.interrupt_before is not None and (
                    not self.interrupt_before or packet.node in self.interrupt_before
                ) and not _consume_interrupt_skip(runtime, packet.node, "before"):
                    interrupt = {"node": packet.node, "when": "before"}
                    self._checkpoint(
                        thread_id,
                        state,
                        chat,
                        [packet],
                        interrupt=interrupt,
                        metadata={"step": step, "source": "interrupt_before"},
                        config=checkpoint_config,
                        channel_versions=channel_versions,
                        versions_seen=versions_seen,
                    )
                    raise GraphInterrupt(interrupt)
                _emit_stream_event(
                    stream_events,
                    "on_node_start",
                    packet.node,
                    {"input": dict(state) if packet.arg is None else packet.arg, "state": dict(state)},
                    metadata={"step": step},
                )
                versions_seen[packet.node] = dict(channel_versions)
                try:
                    result = await self._aexecute_node(packet.node, packet.arg, chat, runtime, state, cache)
                except GraphInterrupt as error:
                    self._checkpoint(
                        thread_id,
                        state,
                        chat,
                        [packet],
                        interrupt=error.value,
                        metadata={"step": step, "source": "interrupt"},
                        config=checkpoint_config,
                        channel_versions=channel_versions,
                        versions_seen=versions_seen,
                    )
                    raise
                pending_writes = _bump_channel_versions(channel_versions, packet.node, result.update)
                _emit_stream_event(
                    stream_events,
                    "on_node_end",
                    packet.node,
                    {"update": dict(result.update), "state": dict(state)},
                    metadata={"step": step},
                )
                if result.update:
                    events.manager.dispatch(
                        "kagraph_node_update",
                        node=packet.node,
                        update=dict(result.update),
                        state=dict(state),
                        channel_versions=dict(channel_versions),
                        step=step,
                    )
                    _emit_stream_event(
                        stream_events,
                        "on_node_update",
                        packet.node,
                        {"update": dict(result.update), "channel_versions": dict(channel_versions)},
                        metadata={"step": step},
                    )
                if stream_records is not None:
                    stream_records.append(
                        _StreamRecord(node=packet.node, update=dict(result.update), state=dict(state), step=step)
                    )
                completed.add(packet.node)
                destinations = await self._adestinations(packet.node, result.command, chat, runtime, state)
                next_packets.extend(destinations)
                for starts, end in self.waiting_edges:
                    if packet.node in starts:
                        waiting_seen[(starts, end)].add(packet.node)
                    if set(starts).issubset(waiting_seen[(starts, end)]):
                        next_packets.append(_Packet(end))
                if self.interrupt_after is not None and (
                    not self.interrupt_after or packet.node in self.interrupt_after
                ):
                    interrupt = {"node": packet.node, "when": "after"}
                    self._checkpoint(
                        thread_id,
                        state,
                        chat,
                        next_packets,
                        interrupt=interrupt,
                        metadata={"step": step, "source": "interrupt_after"},
                        config=checkpoint_config,
                        channel_versions=channel_versions,
                        versions_seen=versions_seen,
                        pending_writes=pending_writes,
                    )
                    raise GraphInterrupt(interrupt)
                self._checkpoint(
                    thread_id,
                    state,
                    chat,
                    next_packets,
                    metadata={"step": step, "source": packet.node},
                    config=checkpoint_config,
                    channel_versions=channel_versions,
                    versions_seen=versions_seen,
                    pending_writes=pending_writes,
                )
            packets = _dedupe_packets(next_packets)
            _emit_stream_event(
                stream_events,
                "on_step_end",
                self.name,
                {"step": step, "next": [(packet.node, packet.arg) for packet in packets], "state": dict(state)},
                metadata={"step": step},
            )
            events.manager.dispatch(
                "kagraph_step_end",
                graph=self.name,
                step=step,
                next=[(packet.node, packet.arg) for packet in packets],
                state=dict(state),
            )
            step += 1
        runtime.context["__kagraph_channel_versions__"] = dict(channel_versions)
        runtime.context["__kagraph_versions_seen__"] = {
            node: dict(versions) for node, versions in versions_seen.items()
        }
        if END not in completed:
            raise InvalidGraphError("END is not reachable from START.")

    def _checkpoint(
        self,
        thread_id: str | None,
        state: dict[str, Any],
        chat: Chat,
        next_packets: list[_Packet],
        interrupt: Any | None = None,
        metadata: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        channel_versions: dict[str, int] | None = None,
        versions_seen: dict[str, dict[str, int]] | None = None,
        pending_writes: list[tuple[str, str, Any, int]] | None = None,
    ) -> dict[str, Any] | None:
        if self.checkpointer and thread_id:
            saved = self.checkpointer.put(
                thread_id,
                {
                    "state": dict(state),
                    "chat": chat,
                    "next": [(packet.node, packet.arg) for packet in next_packets],
                    "interrupt": interrupt,
                    "metadata": dict(metadata or {}),
                    "config": dict(config or {"configurable": {"thread_id": thread_id}}),
                    "created_at": time.time(),
                    "channel_versions": dict(channel_versions or {}),
                    "versions_seen": {
                        node: dict(versions)
                        for node, versions in (versions_seen or {}).items()
                    },
                    "pending_writes": list(pending_writes or []),
                },
            )
            events.manager.dispatch(
                "kagraph_checkpoint",
                thread_id=thread_id,
                checkpoint=saved,
                state=dict(state),
                next=[(packet.node, packet.arg) for packet in next_packets],
                interrupt=interrupt,
                metadata=dict(metadata or {}),
            )
            return saved
        return None

    def _execute_node(
        self,
        name: str,
        arg: Any,
        chat: Chat,
        runtime: Runtime,
        state: dict[str, Any],
        cache: dict[tuple[str, str | bytes], tuple[float, Any]],
    ) -> "_NodeResult":
        node = self.nodes[name]
        node_input = self._node_input(node, state, arg)
        events.manager.dispatch("kagraph_node_start", node=name, chat=chat, state=dict(state))
        try:
            raw = self._call_with_policy(name, node, node_input, chat, runtime, cache)
            command, update = self._apply_node_result(state, raw)
            if self.auto_log_to_chat and not node.is_error_handler and not name.startswith("__") and isinstance(update, dict):
                import json
                from kagraph.messages import SystemMessage
                log_data = {k: v for k, v in update.items() if k != "chat"}
                try:
                    log_str = json.dumps(log_data, default=str, ensure_ascii=False, indent=2)
                except Exception as e:
                    log_str = f"<Unserializable result: {e}>"
                chat.append(SystemMessage(f"[System - Node {name}]: Execution completed.\n```json\n{log_str}\n```"))

            if "chat" in update:
                chat_msgs = update.pop("chat")
                if not isinstance(chat_msgs, list):
                    chat_msgs = [chat_msgs]
                for msg in chat_msgs:
                    chat.append(msg)
        except BaseException as error:
            events.manager.dispatch("kagraph_node_end", node=name, chat=chat, state=dict(state), error=error)
            if isinstance(error, GraphInterrupt):
                raise
            if node.error_handler_node:
                handler = self.nodes[node.error_handler_node]
                handler_input = {"error": error, "state": dict(state), "node": name}
                raw = _invoke_callable(handler.bound, handler_input, chat, runtime)
                command, update = self._apply_node_result(state, raw)
                if self.auto_log_to_chat and not name.startswith("__") and isinstance(update, dict):
                    import json
                    from kagraph.messages import SystemMessage
                    log_data = {k: v for k, v in update.items() if k != "chat"}
                    try:
                        log_str = json.dumps(log_data, default=str, ensure_ascii=False, indent=2)
                    except Exception as e:
                        log_str = f"<Unserializable result: {e}>"
                    chat.append(SystemMessage(f"[System - Node {name}_error_handler]: Execution completed.\n```json\n{log_str}\n```"))
                if "chat" in update:
                    chat_msgs = update.pop("chat")
                    if not isinstance(chat_msgs, list):
                        chat_msgs = [chat_msgs]
                    for msg in chat_msgs:
                        chat.append(msg)
                return _NodeResult(command=command, update=update)
            if isinstance(error, (NodeError, InvalidUpdateError, InvalidGraphError)):
                raise
            raise NodeError(f"Node {name!r} failed: {error}") from error
        events.manager.dispatch("kagraph_node_end", node=name, chat=chat, state=dict(state), error=None)
        return _NodeResult(command=command, update=update)

    async def _aexecute_node(
        self,
        name: str,
        arg: Any,
        chat: Chat,
        runtime: Runtime,
        state: dict[str, Any],
        cache: dict[tuple[str, str | bytes], tuple[float, Any]],
    ) -> "_NodeResult":
        node = self.nodes[name]
        node_input = self._node_input(node, state, arg)
        events.manager.dispatch("kagraph_node_start", node=name, chat=chat, state=dict(state))
        try:
            raw = await self._acall_with_policy(name, node, node_input, chat, runtime, cache)
            command, update = self._apply_node_result(state, raw)
            if self.auto_log_to_chat and not node.is_error_handler and not name.startswith("__") and isinstance(update, dict):
                import json
                from kagraph.messages import SystemMessage
                log_data = {k: v for k, v in update.items() if k != "chat"}
                try:
                    log_str = json.dumps(log_data, default=str, ensure_ascii=False, indent=2)
                except Exception as e:
                    log_str = f"<Unserializable result: {e}>"
                chat.append(SystemMessage(f"[System - Node {name}]: Execution completed.\n```json\n{log_str}\n```"))
            if "chat" in update:
                chat_msgs = update.pop("chat")
                if not isinstance(chat_msgs, list):
                    chat_msgs = [chat_msgs]
                for msg in chat_msgs:
                    chat.append(msg)
        except BaseException as error:
            events.manager.dispatch("kagraph_node_end", node=name, chat=chat, state=dict(state), error=error)
            if isinstance(error, GraphInterrupt):
                raise
            if node.error_handler_node:
                handler = self.nodes[node.error_handler_node]
                handler_input = {"error": error, "state": dict(state), "node": name}
                raw = await _ainvoke_callable(handler.bound, handler_input, chat, runtime)
                command, update = self._apply_node_result(state, raw)
                if self.auto_log_to_chat and not name.startswith("__") and isinstance(update, dict):
                    import json
                    from kagraph.messages import SystemMessage
                    log_data = {k: v for k, v in update.items() if k != "chat"}
                    try:
                        log_str = json.dumps(log_data, default=str, ensure_ascii=False, indent=2)
                    except Exception as e:
                        log_str = f"<Unserializable result: {e}>"
                    chat.append(SystemMessage(f"[System - Node {name}_error_handler]: Execution completed.\n```json\n{log_str}\n```"))
                if "chat" in update:
                    chat_msgs = update.pop("chat")
                    if not isinstance(chat_msgs, list):
                        chat_msgs = [chat_msgs]
                    for msg in chat_msgs:
                        chat.append(msg)
                return _NodeResult(command=command, update=update)
            if isinstance(error, (NodeError, InvalidUpdateError, InvalidGraphError)):
                raise
            raise NodeError(f"Node {name!r} failed: {error}") from error
        events.manager.dispatch("kagraph_node_end", node=name, chat=chat, state=dict(state), error=None)
        return _NodeResult(command=command, update=update)

    def _call_with_policy(
        self,
        name: str,
        node: PregelNode,
        node_input: Any,
        chat: Chat,
        runtime: Runtime,
        cache: dict[tuple[str, str | bytes], tuple[float, Any]],
    ) -> Any:
        policy = node.cache_policy
        cache_key = None
        if policy is not None:
            cache_key = (name, policy.key_func(node_input))
            found = cache.get(cache_key)
            if found and (policy.ttl is None or time.time() - found[0] <= policy.ttl):
                return found[1]
        retry_policy = _first_retry_policy(node.retry_policy)
        attempts = retry_policy.max_attempts if retry_policy else 1
        delay = retry_policy.initial_interval if retry_policy else 0
        last_error = None
        for attempt in range(attempts):
            try:
                started = time.monotonic()
                result = _invoke_callable(node.bound, node_input, chat, runtime)
                if node.timeout and node.timeout.run_timeout is not None:
                    elapsed = time.monotonic() - started
                    if elapsed > node.timeout.run_timeout:
                        raise NodeTimeoutError(
                            f"Node {name!r} exceeded timeout "
                            f"{node.timeout.run_timeout:.3f}s after {elapsed:.3f}s."
                        )
                if cache_key is not None:
                    cache[cache_key] = (time.time(), result)
                return result
            except BaseException as error:
                last_error = error
                if not retry_policy or not isinstance(error, retry_policy.retry_on) or attempt == attempts - 1:
                    raise
                time.sleep(min(delay, retry_policy.max_interval))
                delay *= retry_policy.backoff_factor
        raise last_error or NodeError(f"Node {name!r} failed.")

    async def _acall_with_policy(
        self,
        name: str,
        node: PregelNode,
        node_input: Any,
        chat: Chat,
        runtime: Runtime,
        cache: dict[tuple[str, str | bytes], tuple[float, Any]],
    ) -> Any:
        policy = node.cache_policy
        cache_key = None
        if policy is not None:
            cache_key = (name, policy.key_func(node_input))
            found = cache.get(cache_key)
            if found and (policy.ttl is None or time.time() - found[0] <= policy.ttl):
                return found[1]
        retry_policy = _first_retry_policy(node.retry_policy)
        attempts = retry_policy.max_attempts if retry_policy else 1
        delay = retry_policy.initial_interval if retry_policy else 0
        last_error = None
        for attempt in range(attempts):
            try:
                started = time.monotonic()
                coro = _ainvoke_callable(node.bound, node_input, chat, runtime)
                if node.timeout and node.timeout.run_timeout is not None:
                    result = await asyncio.wait_for(coro, timeout=node.timeout.run_timeout)
                else:
                    result = await coro
                if node.timeout and node.timeout.run_timeout is not None:
                    elapsed = time.monotonic() - started
                    if elapsed > node.timeout.run_timeout:
                        raise NodeTimeoutError(
                            f"Node {name!r} exceeded timeout "
                            f"{node.timeout.run_timeout:.3f}s after {elapsed:.3f}s."
                        )
                if cache_key is not None:
                    cache[cache_key] = (time.time(), result)
                return result
            except asyncio.TimeoutError as error:
                last_error = NodeTimeoutError(f"Node {name!r} exceeded timeout {node.timeout.run_timeout:.3f}s.")
                if not retry_policy or attempt == attempts - 1:
                    raise last_error from error
                await asyncio.sleep(min(delay, retry_policy.max_interval))
                delay *= retry_policy.backoff_factor
            except BaseException as error:
                last_error = error
                if not retry_policy or not isinstance(error, retry_policy.retry_on) or attempt == attempts - 1:
                    raise
                await asyncio.sleep(min(delay, retry_policy.max_interval))
                delay *= retry_policy.backoff_factor
        raise last_error or NodeError(f"Node {name!r} failed.")

    def _apply_node_result(self, state: dict[str, Any], raw: Any) -> tuple[Command | None, dict[str, Any]]:
        if isinstance(raw, Command):
            update = raw.update or {}
            applied = {}
            if update:
                applied = self._apply_update(state, update)
            return raw, applied
        if isinstance(raw, dict):
            applied = self._apply_update(state, raw)
            return None, applied
        if isinstance(raw, list) and all(isinstance(item, Send) for item in raw):
            return Command(goto=raw), {}
        if raw is None:
            return None, {}
        raise InvalidUpdateError(f"Expected dict, Command, Send list, or None; got {raw!r}.")

    def _apply_update(
        self,
        state: dict[str, Any],
        update: dict[str, Any],
        *,
        allowed_keys: set[str] | None | object = _USE_CHANNEL_KEYS,
        warn_unknown: bool = False,
    ) -> dict[str, Any]:
        if allowed_keys is _USE_CHANNEL_KEYS:
            allowed_keys = self.channel_keys
        applied: dict[str, Any] = {}
        for key, value in update.items():
            if allowed_keys is not None and key not in allowed_keys:
                if warn_unknown:
                    logger.warning("Input channel %s not found in %s", key, sorted(allowed_keys))
                continue
            if key in self.reducers:
                if key in state:
                    state[key] = self.reducers[key](state[key], value)
                else:
                    try:
                        state[key] = self.reducers[key](None, value)
                    except Exception:
                        state[key] = value
            else:
                state[key] = value
            applied[key] = state[key]
        return applied

    def _apply_checkpoint_input(self, state: dict[str, Any], update: dict[str, Any]) -> None:
        checkpoint_update = {
            key: value
            for key, value in update.items()
            if (self.input_keys is None or key in self.input_keys)
            and (key in self.reducers or key not in state)
        }
        self._apply_update(state, checkpoint_update, allowed_keys=self.input_keys, warn_unknown=True)

    def _node_input(self, node: PregelNode, state: dict[str, Any], arg: Any) -> Any:
        if arg is not None:
            return arg
        return _project_state(state, node.channels)

    def _branch_input(self, branch: BranchSpec, node: PregelNode | None, state: dict[str, Any]) -> dict[str, Any]:
        channels = _node_channels(branch.input_schema) if branch.input_schema is not None else (node.channels if node else "__root__")
        return _project_state(state, channels)

    def _output_state(self, state: dict[str, Any]) -> dict[str, Any]:
        if self.output_keys is None:
            return dict(state)
        return {key: state[key] for key in self.output_keys if key in state}

    def _destinations(
        self,
        name: str,
        command: Command | None,
        chat: Chat,
        runtime: Runtime,
        state: dict[str, Any],
    ) -> list[_Packet]:
        if command and command.goto is not None:
            return _coerce_destinations(command.goto)
        if name in self.branches:
            packets: list[_Packet] = []
            for branch in self.branches[name].values():
                routed = _invoke_callable(branch.path, self._branch_input(branch, self.nodes.get(name), state), chat, runtime)
                route_values = routed if isinstance(routed, list) else [routed]
                for route in route_values:
                    if isinstance(route, Send):
                        packets.append(_Packet(str(route.node), route.arg))
                    else:
                        try:
                            target = branch.ends[route] if branch.ends else str(route)
                        except KeyError as error:
                            labels = ", ".join(str(label) for label in sorted(branch.ends, key=str))
                            raise InvalidGraphError(
                                f"Router for {name!r} returned {route!r}; expected one of: {labels}."
                            ) from error
                        packets.append(_Packet(target))
            return packets
        return [_Packet(dst) for src, dst in self.edges if src == name]

    async def _adestinations(
        self,
        name: str,
        command: Command | None,
        chat: Chat,
        runtime: Runtime,
        state: dict[str, Any],
    ) -> list[_Packet]:
        if command and command.goto is not None:
            return _coerce_destinations(command.goto)
        if name in self.branches:
            packets: list[_Packet] = []
            for branch in self.branches[name].values():
                routed = await _ainvoke_callable(branch.path, self._branch_input(branch, self.nodes.get(name), state), chat, runtime)
                route_values = routed if isinstance(routed, list) else [routed]
                for route in route_values:
                    if isinstance(route, Send):
                        packets.append(_Packet(str(route.node), route.arg))
                    else:
                        try:
                            target = branch.ends[route] if branch.ends else str(route)
                        except KeyError as error:
                            labels = ", ".join(str(label) for label in sorted(branch.ends, key=str))
                            raise InvalidGraphError(
                                f"Router for {name!r} returned {route!r}; expected one of: {labels}."
                            ) from error
                        packets.append(_Packet(target))
            return packets
        return [_Packet(dst) for src, dst in self.edges if src == name]


@dataclass(frozen=True)
class _NodeResult:
    command: Command | None
    update: dict[str, Any]


def _coerce_destinations(value: str | Send | list[str | Send]) -> list[_Packet]:
    values = value if isinstance(value, list) else [value]
    packets = []
    for item in values:
        if isinstance(item, Send):
            packets.append(_Packet(str(item.node), item.arg))
        else:
            packets.append(_Packet(str(item)))
    return packets


def _dedupe_packets(packets: list[_Packet]) -> list[_Packet]:
    seen: set[tuple[str, str]] = set()
    deduped = []
    for packet in packets:
        marker = (packet.node, repr(packet.arg))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(packet)
    return deduped


def _invoke_callable(fn: Callable[..., Any], state: Any, chat: Chat, runtime: Runtime) -> Any:
    signature = inspect.signature(fn)
    kwargs = {}
    if "runtime" in signature.parameters:
        kwargs["runtime"] = runtime
    if "config" in signature.parameters:
        kwargs["config"] = runtime.config or {}
    required_positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        and parameter.default is parameter.empty
    ]
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(required_positional) > 2:
        raise TypeError(
            "KaGraph node callables must accept state and optional config arguments. "
            "Use kagraph.get_chat() or kagraph.get_runtime() for runtime access."
        )
    if len(required_positional) == 0:
        if positional and positional[0].name in {"state", "input"}:
            return fn(state, **kwargs)
        return fn(**kwargs)
    if len(required_positional) == 2:
        second = required_positional[1].name
        if second == "runtime":
            return fn(state, **kwargs)
        if second == "config":
            kwargs.pop("config", None)
            return fn(state, runtime.config or {}, **kwargs)
        raise TypeError(
            "KaGraph node callables must accept state and optional config/runtime arguments. "
            "Use `config` for the second positional argument or `runtime` for runtime access."
        )
    return fn(state, **kwargs)


async def _ainvoke_callable(fn: Callable[..., Any], state: Any, chat: Chat, runtime: Runtime) -> Any:
    result = _invoke_callable(fn, state, chat, runtime)
    if inspect.isawaitable(result):
        return await result
    return result


def _coerce_node(fn: NodeFn | LLMChat) -> NodeFn:
    if isinstance(fn, LLMChat):
        llm = fn

        def respond_node(state: dict[str, Any]) -> dict[str, Any]:
            response = llm.respond()
            return {"answer": response.content}

        return respond_node
    if isinstance(fn, CompiledStateGraph):
        subgraph = fn

        def subgraph_node(state: dict[str, Any]) -> dict[str, Any]:
            result = subgraph.invoke(dict(state))
            return {key: value for key, value in result.items() if key != "chat"}

        subgraph_node.__kagraph_subgraph__ = fn
        return subgraph_node
    return fn


def _get_node_name(node: Any) -> str:
    if isinstance(node, LLMChat):
        return node.name
    return getattr(node, "__name__", node.__class__.__name__)


def _infer_input_schema(node: Any) -> type[Any] | None:
    try:
        params = inspect.signature(node).parameters
        if params:
            first = next(iter(params))
            try:
                hints = get_type_hints(node)
            except Exception:
                hints = {}
            hint = hints.get(first, params[first].annotation)
            if hint is not inspect.Signature.empty and not isinstance(hint, str):
                return hint
    except Exception:
        return None
    return None


def _infer_command_ends(node: Any) -> tuple[str, ...]:
    return ()


def _schema_keys(schema: type[Any] | None) -> set[str] | None:
    if schema is None:
        return None
    model_fields = getattr(schema, "model_fields", None)
    if isinstance(model_fields, dict):
        return set(model_fields)
    legacy_fields = getattr(schema, "__fields__", None)
    if isinstance(legacy_fields, dict):
        return set(legacy_fields)
    try:
        hints = get_type_hints(schema, include_extras=True)
    except Exception:
        hints = getattr(schema, "__annotations__", {}) or {}
    if not hints:
        return None
    return {key for key in hints if key != "__slots__"}


def _collect_schema_keys(*schemas: type[Any] | None) -> set[str] | None:
    if not schemas:
        return None
    first_keys = _schema_keys(schemas[0])
    if first_keys is None:
        return None
    keys = set(first_keys)
    for schema in schemas[1:]:
        schema_keys = _schema_keys(schema)
        if schema_keys is not None:
            keys.update(schema_keys)
    return keys


def _node_channels(schema: type[Any] | None) -> list[str] | str:
    keys = _schema_keys(schema)
    if keys is None:
        return "__root__"
    return sorted(keys)


def _project_state(state: dict[str, Any], channels: list[str] | str) -> dict[str, Any]:
    if channels == "__root__":
        return dict(state)
    return {key: state[key] for key in channels if key in state}


def _collect_reducers(*schemas: type[Any] | None) -> dict[str, Callable[[Any, Any], Any]]:
    reducers: dict[str, Callable[[Any, Any], Any]] = {}
    for schema in schemas:
        reducers.update(_get_reducers(schema))
    return reducers


def _get_reducers(schema: type[Any] | None) -> dict[str, Callable[[Any, Any], Any]]:
    reducers: dict[str, Callable[[Any, Any], Any]] = {}
    if schema is None:
        return reducers
    try:
        hints = get_type_hints(schema, include_extras=True)
    except Exception:
        return reducers
    for key, hint in hints.items():
        if get_origin(hint) is Annotated:
            args = get_args(hint)
            for metadata in args[1:]:
                if callable(metadata):
                    reducers[key] = metadata
                    break
    return reducers


def _normalize_interrupts(value: list[str] | str | None) -> set[str] | None:
    if value is None:
        return None
    if value == "*":
        return set()
    return set(value)


def _thread_id(config: dict[str, Any] | None) -> str | None:
    if not config:
        return None
    configurable = config.get("configurable", {})
    return configurable.get("thread_id")


def _checkpoint_id(config: dict[str, Any] | None) -> str | None:
    if not config:
        return None
    configurable = config.get("configurable", {})
    return configurable.get("checkpoint_id")


def _get_checkpoint_from_saver(
    checkpointer: BaseCheckpointer | None,
    thread_id: str,
    checkpoint_id: str | None = None,
) -> dict[str, Any] | None:
    if checkpointer is None:
        return None
    try:
        return checkpointer.get(thread_id, checkpoint_id)
    except TypeError:
        if checkpoint_id is not None:
            history_fn = getattr(checkpointer, "list", None)
            if history_fn is not None:
                for checkpoint in reversed(history_fn(thread_id)):
                    if checkpoint.get("checkpoint_id") == checkpoint_id:
                        return checkpoint
            return None
        return checkpointer.get(thread_id)


def _config_with_checkpoint_id(
    config: dict[str, Any],
    checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    if not checkpoint or not checkpoint.get("checkpoint_id"):
        return config
    updated = dict(config)
    configurable = dict(updated.get("configurable", {}))
    configurable["checkpoint_id"] = checkpoint["checkpoint_id"]
    updated["configurable"] = configurable
    return updated


def _normalize_stream_modes(stream_mode: str | Sequence[str]) -> set[str]:
    if isinstance(stream_mode, str):
        return {stream_mode}
    return {str(mode) for mode in stream_mode}


def _format_stream_payload(mode: str, payload: Any, modes: set[str]) -> Any:
    if len(modes) == 1:
        return payload
    return mode, payload


def _make_runtime_writer(stream_events: list[dict[str, Any]] | None):
    if stream_events is None:
        return None

    def write(value: Any) -> None:
        _emit_stream_event(stream_events, "on_custom_event", "writer", {"chunk": value})

    return write


def _emit_stream_event(
    stream_events: list[dict[str, Any]] | None,
    event: str,
    name: str,
    data: dict[str, Any] | None = None,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    if stream_events is None:
        return
    stream_events.append(
        {
            "event": event,
            "name": name,
            "run_id": str(uuid4()),
            "parent_ids": [],
            "tags": [],
            "metadata": dict(metadata or {}),
            "data": dict(data or {}),
        }
    )


class _KbenchStreamListener:
    def __init__(self, stream_events: list[dict[str, Any]], graph_name: str) -> None:
        self.stream_events = stream_events
        self.graph_name = graph_name

    def start_streaming(self, message: Any) -> None:
        _emit_stream_event(
            self.stream_events,
            "on_chat_model_start",
            getattr(getattr(message, "sender", None), "name", self.graph_name),
            {"message": message},
        )

    def new_chunk(self, message: Any, chunk: Any) -> None:
        chunk_text = chunk if isinstance(chunk, str) else getattr(chunk, "content", "")
        _emit_stream_event(
            self.stream_events,
            "on_chat_model_stream",
            getattr(getattr(message, "sender", None), "name", self.graph_name),
            {"chunk": chunk, "content": chunk_text, "message": message},
        )

    def new_tool_call(self, message: Any, chunk: Any) -> None:
        _emit_stream_event(
            self.stream_events,
            "on_tool_stream",
            getattr(getattr(message, "sender", None), "name", self.graph_name),
            {"chunk": chunk, "message": message},
        )


def _bump_channel_versions(
    channel_versions: dict[str, int],
    node: str,
    update: dict[str, Any],
) -> list[tuple[str, str, Any, int]]:
    pending_writes: list[tuple[str, str, Any, int]] = []
    for key, value in update.items():
        channel_versions[key] = channel_versions.get(key, 0) + 1
        pending_writes.append((node, key, value, channel_versions[key]))
    return pending_writes


def _consume_interrupt_skip(runtime: Runtime, node: str, when: str) -> bool:
    interrupt = runtime.context.get("__kagraph_skip_interrupt__")
    if not isinstance(interrupt, dict):
        return False
    if interrupt.get("node") != node or interrupt.get("when") != when:
        return False
    runtime.context.pop("__kagraph_skip_interrupt__", None)
    return True


def _should_skip_saved_interrupt(
    saved: dict[str, Any],
    resume_values: list[Any] | None,
    input: Any,
) -> bool:
    interrupt = saved.get("interrupt")
    return isinstance(interrupt, dict) and (resume_values is not None or input is None)


def _enter_graph_chat(
    *,
    chat_name: str,
    system_instructions: str | None,
    existing_chat: Chat | None,
):
    if existing_chat is not None:
        return _enter_existing_chat(existing_chat)
    return chats.new(chat_name, system_instructions=system_instructions)


@contextlib.contextmanager
def _enter_existing_chat(chat: Chat):
    with contexts.enter(chat=chat):
        yield chat


def _first_retry_policy(policy: RetryPolicy | Sequence[RetryPolicy] | None) -> RetryPolicy | None:
    if policy is None:
        return None
    if isinstance(policy, RetryPolicy):
        return policy
    return next(iter(policy), None)


def _to_snapshot(saved: dict[str, Any] | None, config: dict[str, Any]) -> StateSnapshot:
    if not saved:
        return StateSnapshot(values={}, config=config)
    return StateSnapshot(
        values=dict(saved.get("state", saved)),
        next=tuple(saved.get("next", ())),
        config=_config_with_checkpoint_id(dict(saved.get("config", config)), saved),
        metadata=dict(saved.get("metadata", {})) | {"interrupt": saved.get("interrupt")},
        created_at=str(saved.get("created_at", "")),
        checkpoint_id=saved.get("checkpoint_id"),
        parent_checkpoint_id=saved.get("parent_checkpoint_id"),
        channel_versions=dict(saved.get("channel_versions", {})),
        versions_seen={
            node: dict(versions)
            for node, versions in saved.get("versions_seen", {}).items()
        },
        pending_writes=tuple(saved.get("pending_writes", ())),
    )


CompiledKaGraph = CompiledStateGraph
