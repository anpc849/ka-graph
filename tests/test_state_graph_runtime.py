from __future__ import annotations

import operator
import time
from typing import Annotated, Literal, TypedDict

from kaggle_benchmarks.actors import LLMChat
from kaggle_benchmarks.actors.llms import LLMResponse

from kagraph import (
    END,
    START,
    Command,
    GraphInterrupt,
    InMemorySaver,
    MessagesState,
    NodeTimeoutError,
    RetryPolicy,
    StateGraph,
    interrupt,
)
from kagraph.runtime import get_runtime


def replace_latest_value(_left, right):
    return right


def test_state_only_node_signature_is_preferred():
    class State(TypedDict):
        answer: str

    def answer(state: State):
        return {"answer": "ok"}

    graph = StateGraph(State)
    graph.add_node(answer)
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)

    assert graph.compile().invoke({})["answer"] == "ok"


def test_graph_input_schema_does_not_pollute_state_schema():
    class ReflectionInput(TypedDict):
        input: str

    class ReflectionState(MessagesState, total=False):
        next_step: Literal["reflect", "end"]
        route_reason: str

    def generation_node(state: ReflectionState):
        return {
            "next_step": "end",
            "route_reason": "input visible" if "input" in state else "input hidden",
        }

    graph = StateGraph(ReflectionState, input_schema=ReflectionInput)
    graph.add_node("generation", generation_node)
    graph.add_edge(START, "generation")
    graph.add_edge("generation", END)

    result = graph.compile().invoke({"input": "Write an essay."})

    assert result["route_reason"] == "input hidden"
    assert "input" not in result


def test_node_input_schema_can_read_public_input_without_persisting_it():
    class ReflectionInput(TypedDict):
        input: str

    class ReflectionState(MessagesState, total=False):
        next_step: Literal["reflect", "end"]
        route_reason: str

    class GenerationInput(ReflectionState, ReflectionInput, total=False):
        pass

    def generation_node(state: GenerationInput):
        messages = state.get("messages", [])
        if not messages:
            messages = [("user", state["input"])]
        return {"messages": messages, "next_step": "end"}

    graph = StateGraph(ReflectionState, input_schema=ReflectionInput)
    graph.add_node("generation", generation_node, input_schema=GenerationInput)
    graph.add_edge(START, "generation")
    graph.add_edge("generation", END)

    result = graph.compile().invoke({"input": "Write an essay."})

    assert [message.text for message in result["messages"]] == ["Write an essay."]
    assert "input" not in result


def test_private_node_channels_do_not_leak_to_final_output():
    class State(TypedDict, total=False):
        answer: str

    class PrivateState(TypedDict):
        scratch: str

    def first(state: State):
        return {"scratch": "private", "ignored": "not declared"}

    def second(state: PrivateState):
        return {"answer": state["scratch"], "ignored": "still not declared"}

    graph = StateGraph(State)
    graph.add_node("first", first)
    graph.add_node("second", second, input_schema=PrivateState)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    result = graph.compile().invoke({})

    assert result["answer"] == "private"
    assert "scratch" not in result
    assert "ignored" not in result


def test_annotated_reducer_merges_parallel_fanout_updates():
    class State(TypedDict):
        items: Annotated[list[str], operator.add]

    graph = StateGraph(State)
    graph.add_node("split", lambda state: {})
    graph.add_node("b", lambda state: {"items": ["b"]})
    graph.add_node("c", lambda state: {"items": ["c"]})
    graph.add_edge(START, "split")
    graph.add_edge("split", "b")
    graph.add_edge("split", "c")
    graph.add_edge("b", END)
    graph.add_edge("c", END)

    assert graph.compile().invoke({})["items"] == ["b", "c"]


def test_retry_policy_retries_node_failures():
    attempts = {"count": 0}

    def flaky(state):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("transient")
        return {"answer": "recovered"}

    graph = StateGraph()
    graph.add_node("flaky", flaky, retry_policy=RetryPolicy(max_attempts=2, initial_interval=0))
    graph.add_edge(START, "flaky")
    graph.add_edge("flaky", END)

    assert graph.compile().invoke()["answer"] == "recovered"
    assert attempts["count"] == 2


def test_in_memory_saver_restores_thread_state():
    saver = InMemorySaver()

    def inc(state):
        return {"x": state["x"] + 1}

    graph = StateGraph()
    graph.add_node("inc", inc)
    graph.add_edge(START, "inc")
    graph.add_edge("inc", END)
    compiled = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "thread-1"}}

    assert compiled.invoke({"x": 1}, config=config)["x"] == 2
    assert compiled.invoke({"x": 100}, config=config)["x"] == 3


def test_in_memory_saver_restores_chat_history_for_multi_turn_agent():
    class EchoHistoryLLM(LLMChat):
        def __init__(self):
            super().__init__(name="history-llm")
            self.visible_history: list[list[str]] = []

        def invoke(self, messages, system=None, **kwargs):
            self.visible_history.append([message.text for message in messages])
            return LLMResponse(content=f"turn-{len(self.visible_history)}")

    llm = EchoHistoryLLM()
    saver = InMemorySaver()

    def agent(state):
        return {"answer": llm.respond().content}

    graph = StateGraph()
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    compiled = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "conversation-1"}}

    first = compiled.invoke("hello", config=config)
    second = compiled.invoke("follow up", config=config)

    assert first["answer"] == "turn-1"
    assert second["answer"] == "turn-2"
    assert llm.visible_history[0] == ["hello"]
    assert llm.visible_history[1] == ["hello", "turn-1", "follow up"]
    assert [message.text for message in second["chat"].messages] == [
        "hello",
        "turn-1",
        "follow up",
        "turn-2",
    ]


def test_stream_updates_yields_node_updates():
    graph = StateGraph()
    graph.add_node("first", lambda state: {"x": 1})
    graph.add_node("second", lambda state: Command(update={"x": state["x"] + 1}))
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    assert list(graph.compile().stream(stream_mode="updates")) == [
        {"first": {"x": 1}},
        {"second": {"x": 2}},
    ]


def test_stream_values_yields_state_snapshots():
    graph = StateGraph()
    graph.add_node("first", lambda state: {"x": 1})
    graph.add_node("second", lambda state: {"y": state["x"] + 1})
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    snapshots = list(graph.compile().stream(stream_mode="values"))

    assert [snapshot["x"] for snapshot in snapshots] == [1, 1]
    assert snapshots[-1]["y"] == 2


def test_stream_events_include_node_custom_and_token_chunks():
    def speak(state):
        runtime = get_runtime()
        runtime.write({"phase": "custom"})
        runtime.chat.messages[-1].sender.stream(["he", "llo"])
        return {"answer": "done"}

    graph = StateGraph()
    graph.add_node("speak", speak)
    graph.add_edge(START, "speak")
    graph.add_edge("speak", END)

    events = list(graph.compile().stream("start", stream_mode="events"))
    names = [event["event"] for event in events]

    assert names[0] == "on_graph_start"
    assert "on_node_start" in names
    assert "on_custom_event" in names
    assert "on_chat_model_stream" in names
    assert names[-1] == "on_graph_end"
    assert all({"event", "name", "run_id", "data", "metadata"} <= set(event) for event in events)
    assert [event["data"]["content"] for event in events if event["event"] == "on_chat_model_stream"] == ["he", "llo"]


def test_timeout_policy_reports_sync_node_overrun():
    def slow(state):
        time.sleep(0.02)
        return {"answer": "late"}

    graph = StateGraph()
    graph.add_node("slow", slow, timeout=0.001, retry_policy=RetryPolicy(max_attempts=1))
    graph.add_edge(START, "slow")
    graph.add_edge("slow", END)

    try:
        graph.compile().invoke()
    except NodeTimeoutError as error:
        assert "exceeded timeout" in str(error)
    else:
        raise AssertionError("Expected NodeTimeoutError")


def test_compiled_subgraph_can_run_as_node():
    child = StateGraph()
    child.add_node("derive", lambda state: {"y": state["x"] + 1})
    child.add_edge(START, "derive")
    child.add_edge("derive", END)

    parent = StateGraph()
    parent.add_node("child", child.compile())
    parent.add_node("finish", lambda state: {"z": state["y"] * 2})
    parent.add_edge(START, "child")
    parent.add_edge("child", "finish")
    parent.add_edge("finish", END)

    result = parent.compile().invoke({"x": 2})

    assert result["y"] == 3
    assert result["z"] == 6


def test_interrupt_can_resume_from_checkpointed_node():
    saver = InMemorySaver()

    def approval(state):
        approved = interrupt("approve?")
        return {"approved": approved}

    graph = StateGraph()
    graph.add_node("approval", approval)
    graph.add_edge(START, "approval")
    graph.add_edge("approval", END)
    compiled = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "approval-thread"}}

    try:
        compiled.invoke({"request": "deploy"}, config=config)
    except GraphInterrupt as error:
        assert error.value.value == "approve?"
    else:
        raise AssertionError("Expected GraphInterrupt")

    saved = saver.get("approval-thread")
    assert saved is not None
    assert saved["next"] == [("approval", None)]
    assert saved["interrupt"].value == "approve?"

    resumed = compiled.invoke(Command(resume=True), config=config)

    assert resumed["request"] == "deploy"
    assert resumed["approved"] is True


def test_interrupt_before_can_resume_without_reinterrupting():
    saver = InMemorySaver()

    graph = StateGraph()
    graph.add_node("review", lambda state: {"reviewed": True})
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    compiled = graph.compile(checkpointer=saver, interrupt_before=["review"])
    config = {"configurable": {"thread_id": "before-thread"}}

    try:
        compiled.invoke({"request": "deploy"}, config=config)
    except GraphInterrupt as error:
        assert error.value == {"node": "review", "when": "before"}
    else:
        raise AssertionError("Expected GraphInterrupt")

    resumed = compiled.invoke(None, config=config)

    assert resumed["reviewed"] is True


def test_checkpoint_history_supports_checkpoint_id_time_travel_and_versions():
    saver = InMemorySaver()

    graph = StateGraph()
    graph.add_node("a", lambda state: {"x": 1})
    graph.add_node("b", lambda state: {"y": state["x"] + 1})
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    compiled = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "history-thread"}}

    result = compiled.invoke({}, config=config)
    history = compiled.get_state_history(config)
    after_a = next(snapshot for snapshot in history if snapshot.metadata.get("source") == "a")
    current = compiled.get_state(config)

    assert result["y"] == 2
    assert current.checkpoint_id is not None
    assert current.config["configurable"]["checkpoint_id"] == current.checkpoint_id
    assert current.parent_checkpoint_id is not None
    assert current.channel_versions["x"] == 1
    assert current.channel_versions["y"] == 1
    assert current.versions_seen["b"]["x"] == 1
    assert after_a.next == (("b", None),)

    time_travel_config = {
        "configurable": {
            "thread_id": "history-thread",
            "checkpoint_id": after_a.checkpoint_id,
        }
    }
    snapshot = compiled.get_state(time_travel_config)

    assert snapshot.values == {"x": 1}
    assert snapshot.next == (("b", None),)

    resumed = compiled.invoke(None, time_travel_config)

    assert resumed["y"] == 2


def test_checkpointed_input_channel_can_replace_latest_turn_value():
    class Input(TypedDict):
        message: Annotated[str, replace_latest_value]

    class State(TypedDict, total=False):
        message: Annotated[str, replace_latest_value]
        messages: Annotated[list[str], operator.add]

    def prepare(state: Input):
        return {"messages": [state["message"]]}

    graph = StateGraph(State, input_schema=Input)
    graph.add_node("prepare", prepare, input_schema=Input)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "chat-thread"}}

    assert compiled.invoke({"message": "Hi"}, config=config)["messages"] == ["Hi"]
    second = compiled.invoke({"message": "About movie"}, config=config)

    assert second["message"] == "About movie"
    assert second["messages"] == ["Hi", "About movie"]
