from kaggle_benchmarks.actors import LLMChat
from kaggle_benchmarks.actors.llms import LLMResponse
from kaggle_benchmarks.messages import Message
from kaggle_benchmarks.tools import ToolInvocation, ToolInvocationResult
from pydantic import BaseModel
import pytest
import time
import queue

from kagraph import END, START, StateGraph
from kagraph.tracing.katrace import KaGraphTracer, _dump_replayable_agent, _make_request_payload_serializable, _make_serializable


class OneShotLLM(LLMChat):
    def __init__(self):
        super().__init__(name="mock-model")

    def invoke(self, messages, system=None, **kwargs):
        return LLMResponse(
            content="hello",
            meta={
                "input_tokens": 2,
                "output_tokens": 1,
                "reasoning_traces": "trace",
            },
        )

class MockTracer(KaGraphTracer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dispatched_events = []
        
    def _dispatch(self, endpoint: str, method: str, payload: dict):
        self.dispatched_events.append((endpoint, method, payload))

def test_katrace_dispatches_http_requests():
    tracer = MockTracer()
    tracer.attach()

    llm = OneShotLLM()
    graph = StateGraph()
    graph.add_node("llm", lambda state: {"answer": llm.respond().content})
    graph.add_edge(START, "llm")
    graph.add_edge("llm", END)

    try:
        graph.compile().invoke("hello")
    finally:
        tracer.detach()

    # Verify events
    events = tracer.dispatched_events
    starts = [(endpoint, method, payload) for endpoint, method, payload in events if method == "POST"]
    
    assert any(endpoint == "/api/trace" and payload["name"] == "kagraph_run" for endpoint, method, payload in starts)
    assert any(endpoint == "/api/span" and payload["name"] == "llm" for endpoint, method, payload in starts)
    assert any(endpoint == "/api/generation" and payload["model"] == "mock-model" for endpoint, method, payload in starts)


def test_katrace_generation_input_matches_messages_sent_to_llm():
    class RecorderLLM(LLMChat):
        def __init__(self):
            super().__init__(name="recorder")
            self.seen_messages = []

        def invoke(self, messages, system=None, **kwargs):
            self.seen_messages.append([message.text for message in messages])
            return LLMResponse(content="reply")

    tracer = MockTracer()
    tracer.attach()
    llm = RecorderLLM()
    graph = StateGraph()
    graph.add_node("llm", lambda state: {"answer": llm.respond().text})
    graph.add_edge(START, "llm")
    graph.add_edge("llm", END)

    try:
        graph.compile().invoke("hello")
    finally:
        tracer.detach()

    generation = next(
        payload
        for endpoint, method, payload in tracer.dispatched_events
        if endpoint == "/api/generation" and method == "POST"
    )
    assert llm.seen_messages == [["hello"]]
    assert generation["input"] == ["hello"]


def test_katrace_accepts_custom_trace_name():
    tracer = MockTracer(trace_name="PlanAndExecute")
    tracer.attach()

    graph = StateGraph()
    graph.add_node("solve", lambda state: {"answer": "ok"})
    graph.add_edge(START, "solve")
    graph.add_edge("solve", END)

    try:
        graph.compile().invoke("hello")
    finally:
        tracer.detach()

    starts = [(endpoint, method, payload) for endpoint, method, payload in tracer.dispatched_events if method == "POST"]
    assert any(endpoint == "/api/trace" and payload["name"] == "PlanAndExecute" for endpoint, method, payload in starts)


def test_trace_context_attaches_and_detaches(monkeypatch):
    import kagraph.tracing as tracing

    calls = []

    class DummyTracer:
        def __init__(self, backend_url="http://127.0.0.1:8000", *, trace_name="kagraph_run", **kwargs):
            self.backend_url = backend_url
            self.trace_name = trace_name
            self.kwargs = kwargs

        def attach(self):
            calls.append(("attach", self.trace_name, self.backend_url))

        def detach(self):
            calls.append(("detach", self.trace_name, self.backend_url))

    monkeypatch.setattr(tracing, "KaGraphTracer", DummyTracer)

    with tracing.trace("Reflection", backend_url="http://studio", batch_size=3) as tracer:
        assert tracer.trace_name == "Reflection"
        assert tracer.kwargs["batch_size"] == 3

    assert calls == [
        ("attach", "Reflection", "http://studio"),
        ("detach", "Reflection", "http://studio"),
    ]


def test_katrace_transport_retries_timeout(monkeypatch):
    import requests
    import kagraph.tracing.katrace as katrace

    attempts = []

    class Response:
        def raise_for_status(self):
            return None

    def flaky_post(url, json, timeout):
        attempts.append((url, timeout))
        if len(attempts) == 1:
            raise requests.Timeout("slow backend")
        return Response()

    monkeypatch.setattr(katrace.requests, "post", flaky_post)
    monkeypatch.setattr(katrace.time, "sleep", lambda _: None)

    tracer = KaGraphTracer(request_timeout=7.0, max_retries=1)
    tracer._dispatch("/api/test", "POST", {"ok": True})
    tracer.flush()

    assert len(attempts) == 2
    assert attempts[0][1] == 7.0
    assert tracer._transport_failures == 0


def test_katrace_strips_tool_signature_from_tool_events():
    tracer = MockTracer(batch_size=1)
    tracer.trace_id = "trace-1"
    invocation = ToolInvocation(
        name="lookup",
        arguments={"query": "kagraph", "signature": {"opaque": "large"}},
        call_id="call-1",
    )

    tracer.kagraph_tool_start(invocation)
    tracer.kagraph_tool_end(ToolInvocationResult("lookup", invocation.arguments, "call-1", output="ok"))
    tracer.flush()

    span_payload = next(
        payload
        for endpoint, method, payload in tracer.dispatched_events
        if endpoint == "/api/span" and method == "POST"
    )
    assert span_payload["input"] == {"query": "kagraph"}

    event_batches = [
        payload["events"]
        for endpoint, method, payload in tracer.dispatched_events
        if endpoint == "/api/events/batch" and method == "POST"
    ]
    flat_events = [event for batch in event_batches for event in batch]
    tool_start = next(event for event in flat_events if event["event"] == "on_tool_start")
    assert tool_start["data"]["arguments"] == {"query": "kagraph"}


def test_katrace_strips_tool_signature_from_serialized_tool_results():
    result = ToolInvocationResult(
        "lookup",
        {"query": "kagraph", "signature": {"opaque": "large"}},
        "call-1",
        output="ok",
    )

    payload = _make_serializable({"result": result})

    assert payload["result"]["arguments"] == {"query": "kagraph"}


def test_katrace_message_payload_dedupes_tool_calls_and_strips_json_signature():
    llm = OneShotLLM()
    message = Message(
        "",
        sender=llm,
        _meta={
            "tool_calls": [
                {
                    "id": "calculate",
                    "type": "function",
                    "function": {
                        "name": "calculate",
                        "arguments": '{"expression":"(17 * 23) + 19","signature":"opaque"}',
                    },
                }
            ],
            "input_tokens": 143,
            "output_tokens": 25,
        },
    )

    payload = _make_serializable(message)

    assert payload["tool_calls"] == [
        {
            "id": "calculate",
            "type": "function",
            "function": {
                "name": "calculate",
                "arguments": '{"expression":"(17 * 23) + 19"}',
            },
        }
    ]
    assert "tool_calls" not in payload["metadata"]
    assert payload["metadata"]["input_tokens"] == 143
    assert payload["metadata"]["output_tokens"] == 25


def test_katrace_preserves_tool_signatures_when_explicitly_enabled():
    payload = _make_serializable(
        {"arguments": '{"expression":"1+1","signature":"opaque"}'},
        include_tool_signatures=True,
    )

    assert payload["arguments"] == '{"expression":"1+1","signature":"opaque"}'


def test_katrace_generic_payload_strips_signature_keys_and_json_strings():
    payload = _make_serializable(
        {
            "chunk": {
                "signature": "opaque",
                "function": {"arguments": '{"x":1,"signature":"opaque"}'},
            }
        }
    )

    assert payload == {"chunk": {"function": {"arguments": '{"x":1}'}}}


def test_katrace_event_batch_serialization_preserves_required_envelope():
    payload = {
        "events": [
            {
                "trace_id": "trace-1",
                "sequence": 1,
                "event": "on_message",
                "data": {
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64," + ("a" * 5000)},
                            }
                        ],
                    }
                },
                "metadata_json": {},
            }
        ]
    }

    serialized = _make_request_payload_serializable("/api/events/batch", payload, max_payload_bytes=200)

    assert "events" in serialized
    assert isinstance(serialized["events"], list)
    assert serialized["events"][0]["trace_id"] == "trace-1"


def test_katrace_event_batch_wraps_non_object_data_and_metadata():
    payload = {
        "events": [
            {
                "trace_id": "trace-1",
                "sequence": 1,
                "event": "on_message",
                "data": ["not", "an", "object"],
                "metadata_json": "meta",
            }
        ]
    }

    serialized = _make_request_payload_serializable("/api/events/batch", payload)

    assert serialized["events"][0]["data"] == {"value": ["not", "an", "object"]}
    assert serialized["events"][0]["metadata_json"] == {"value": "meta"}


def test_katrace_generation_serialization_preserves_required_fields():
    payload = {
        "id": "gen-1",
        "trace_id": "trace-1",
        "name": "model",
        "model": "model",
        "input": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + ("a" * 5000)}}],
        "usage_input_tokens": 1.0,
        "usage_output_tokens": "2",
        "metadata_json": "metadata",
    }

    serialized = _make_request_payload_serializable("/api/generation", payload, max_payload_bytes=200)

    assert serialized["id"] == "gen-1"
    assert serialized["trace_id"] == "trace-1"
    assert serialized["name"] == "model"
    assert serialized["usage_input_tokens"] == 1
    assert serialized["usage_output_tokens"] == 2
    assert serialized["metadata_json"] == {"value": "metadata"}


def test_katrace_serializes_pydantic_schema_classes_without_model_dump_error():
    class Decision(BaseModel):
        route: str

    payload = _make_serializable({"schema": Decision})

    assert payload["schema"]["type"] == "class"
    assert payload["schema"]["name"].endswith("Decision")
    assert payload["schema"]["schema"]["properties"] == ["route"]


def test_katrace_inlines_nested_graph_invocations():
    child = StateGraph()
    child.add_node("agent", lambda state: {"child_answer": f"child:{state['task']}"})
    child.add_edge(START, "agent")
    child.add_edge("agent", END)
    child_app = child.compile(name="child_agent")

    parent = StateGraph()

    def call_child(state):
        child_result = child_app.invoke({"task": state["task"]})
        return {"answer": child_result["child_answer"]}

    parent.add_node("execute", call_child)
    parent.add_edge(START, "execute")
    parent.add_edge("execute", END)

    tracer = MockTracer(trace_name="PlanAndExecute")
    tracer.attach()
    try:
        parent.compile(name="parent_graph").invoke({"task": "demo"})
    finally:
        tracer.detach()

    trace_posts = [
        payload
        for endpoint, method, payload in tracer.dispatched_events
        if endpoint == "/api/trace" and method == "POST"
    ]
    assert len(trace_posts) == 1
    assert trace_posts[0]["name"] == "PlanAndExecute"
    assert trace_posts[0]["start_time"]

    event_batches = [
        payload["events"]
        for endpoint, method, payload in tracer.dispatched_events
        if endpoint == "/api/events/batch" and method == "POST"
    ]
    flat_events = [event for batch in event_batches for event in batch]
    event_names = [event["event"] for event in flat_events]
    assert "on_subgraph_start" in event_names
    assert "on_subgraph_end" in event_names
    assert event_names.count("on_graph_start") == 1
    assert event_names.count("on_graph_end") == 1

    subgraph_start = next(event for event in flat_events if event["event"] == "on_subgraph_start")
    execute_start = next(event for event in flat_events if event["event"] == "on_node_start" and event["name"] == "execute")
    assert subgraph_start["parent_ids"] == [execute_start["metadata_json"]["run_id"]]

    nested_agent = next(event for event in flat_events if event["event"] == "on_node_start" and event["name"] == "agent")
    assert nested_agent["metadata_json"]["parent_run_id"] != execute_start["metadata_json"]["run_id"]


def test_replayable_agent_dump_reduces_non_picklable_kbench_llm(monkeypatch):
    import pickle
    import threading
    import kagraph.tracing.katrace as katrace

    class LockedLLM(LLMChat):
        def __init__(self):
            super().__init__(name="locked")
            self.model = "provider/model"
            self.lock = threading.RLock()

        def invoke(self, messages, system=None, **kwargs):
            return LLMResponse(content="locked")

    llm = LockedLLM()
    graph = StateGraph()

    def call_llm(state):
        return {"answer": llm.respond().text}

    graph.add_node("llm", call_llm)
    graph.add_edge(START, "llm")
    graph.add_edge("llm", END)
    app = graph.compile()

    with pytest.raises(Exception):
        pickle.dumps(app)

    assert _dump_replayable_agent(app)
