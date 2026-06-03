from __future__ import annotations

import importlib
import sys
from pathlib import Path
import base64

from fastapi.testclient import TestClient


def _load_backend(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1] / "webapp" / "backend"
    monkeypatch.setenv("KATRACE_DB_URL", f"sqlite:///{tmp_path / 'studio.db'}")
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    for name in ["main", "models", "database"]:
        sys.modules.pop(name, None)
    return importlib.import_module("main")


def test_backend_default_db_path_is_backend_local(monkeypatch, tmp_path):
    backend_dir = Path(__file__).resolve().parents[1] / "webapp" / "backend"
    monkeypatch.delenv("KATRACE_DB_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    sys.modules.pop("database", None)

    database = importlib.import_module("database")

    assert database.DATABASE_URL == f"sqlite:///{(backend_dir / 'traces.db').as_posix()}"


def test_backend_ingests_ordered_events_and_checkpoints(tmp_path, monkeypatch):
    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)

    trace_id = "trace-events"
    assert client.post("/api/trace", json={"id": trace_id, "name": "run"}).status_code == 200

    response = client.post(
        "/api/events/batch",
        json={
            "events": [
                {
                    "trace_id": trace_id,
                    "event": "on_node_update",
                    "name": "draft",
                    "node": "draft",
                    "checkpoint_id": "ckpt-1",
                    "data": {"update": {"answer": "yes"}},
                    "metadata_json": {"step": 1},
                },
                {
                    "trace_id": trace_id,
                    "event": "on_chat_model_stream",
                    "name": "llm",
                    "data": {"content": "token"},
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["sequences"] == [1, 2]

    events = client.get(f"/api/traces/{trace_id}/events").json()
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[0]["checkpoint_id"] == "ckpt-1"

    checkpoints = client.get(f"/api/traces/{trace_id}/checkpoints").json()
    assert checkpoints[0]["checkpoint_id"] == "ckpt-1"


def test_backend_deletes_trace_and_related_records(tmp_path, monkeypatch):
    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)
    trace_id = "trace-delete"

    client.post("/api/trace", json={"id": trace_id, "name": "run"})
    client.post("/api/span", json={"id": "span-delete", "trace_id": trace_id, "name": "node", "span_type": "NODE"})
    client.post("/api/generation", json={"id": "gen-delete", "trace_id": trace_id, "name": "llm"})
    client.post("/api/events", json={"trace_id": trace_id, "event": "on_node_start", "name": "node"})

    response = client.delete(f"/api/traces/{trace_id}")

    assert response.status_code == 200
    assert response.json()["deleted"]["events"] == 1
    assert client.get(f"/api/traces/{trace_id}").status_code == 404
    assert client.get("/api/traces").json() == []


def test_backend_replay_reports_non_replayable_trace(tmp_path, monkeypatch):
    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)
    trace_id = "trace-no-agent"

    client.post("/api/trace", json={"id": trace_id, "name": "run"})

    response = client.post(f"/api/traces/{trace_id}/replay", json={"input": "hello"})

    assert response.status_code == 400
    assert "not replayable" in response.json()["detail"]


def test_backend_in_memory_message_payload_dedupes_tool_calls(tmp_path, monkeypatch):
    from kaggle_benchmarks.actors import LLMChat
    from kaggle_benchmarks.messages import Message

    main = _load_backend(tmp_path, monkeypatch)
    llm = LLMChat(name="model")
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
        },
    )

    payload = main._message_payload(message)

    assert payload["tool_calls"][0]["function"]["arguments"] == '{"expression":"(17 * 23) + 19"}'
    assert "tool_calls" not in payload["metadata"]
    assert payload["metadata"]["input_tokens"] == 143


def test_backend_message_payload_preserves_image_content(tmp_path, monkeypatch):
    from kaggle_benchmarks import actors
    from kaggle_benchmarks.messages import Message
    from kagraph import image_from_base64

    main = _load_backend(tmp_path, monkeypatch)
    message = Message(image_from_base64("aGVsbG8=", format="png"), sender=actors.user)

    payload = main._message_payload(message)

    assert payload["role"] == "user"
    assert payload["content"] == [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aGVsbG8="},
            "mime_type": "image/png",
        }
    ]


def test_backend_generation_accepts_multimodal_input_and_loose_usage(tmp_path, monkeypatch):
    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)
    trace_id = "trace-generation-media"

    client.post("/api/trace", json={"id": trace_id, "name": "run"})
    response = client.post(
        "/api/generation",
        json={
            "id": "gen-media",
            "trace_id": trace_id,
            "name": "model",
            "input": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + ("a" * 5000)},
                }
            ],
            "usage_input_tokens": 1.0,
            "usage_output_tokens": "2",
            "metadata_json": "metadata",
        },
    )

    assert response.status_code == 200


def test_backend_json_safe_strips_signature_json_strings(tmp_path, monkeypatch):
    main = _load_backend(tmp_path, monkeypatch)

    payload = main._make_json_safe(
        {
            "chunk": {
                "signature": "opaque",
                "function": {"arguments": '{"x":1,"signature":"opaque"}'},
            }
        }
    )

    assert payload == {"chunk": {"function": {"arguments": '{"x":1}'}}}


def test_backend_graph_playground_replays_serialized_agent_without_new_trace(tmp_path, monkeypatch):
    import cloudpickle
    from typing_extensions import TypedDict
    from kagraph import END, START, StateGraph

    class Input(TypedDict):
        problem: str

    class State(Input, total=False):
        answer: str

    class Context(TypedDict, total=False):
        k: int
        threshold: float

    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)
    trace_id = "trace-agent"

    graph = StateGraph(State, context_schema=Context, input_schema=Input)
    graph.add_node("solve", lambda state, *, runtime: {"answer": f"{state['problem']}:{runtime.context['k']}"})
    graph.add_edge(START, "solve")
    graph.add_edge("solve", END)
    app = graph.compile()
    agent_binary = base64.b64encode(cloudpickle.dumps(app)).decode("utf-8")

    response = client.post(
        "/api/trace",
        json={
            "id": trace_id,
            "name": "tot",
            "input": {"problem": "12 1 5 7"},
            "metadata_json": {
                "invoke": {
                    "input": {"problem": "12 1 5 7"},
                    "context": {"k": 5},
                    "config": {"configurable": {"thread_id": "original"}},
                    "recursion_limit": 80,
                    "chat_name": "tot original",
                }
            },
            "agent_binary": agent_binary,
        },
    )
    assert response.status_code == 200

    defaults = client.get(f"/api/traces/{trace_id}/playground").json()["defaults"]
    playground = client.get(f"/api/traces/{trace_id}/playground").json()
    defaults = playground["defaults"]
    assert defaults["input"] == {"problem": "12 1 5 7"}
    assert defaults["context"] == {"k": 5}
    assert defaults["recursion_limit"] == 80
    assert playground["schema"]["input"] == [{"name": "problem", "type": "str", "required": True}]
    assert {"name": "k", "type": "int", "required": False} in playground["schema"]["context"]
    assert {"name": "threshold", "type": "float", "required": False} in playground["schema"]["context"]

    replay = client.post(
        f"/api/traces/{trace_id}/replay",
        json={
            "input": {"problem": "1 2 3 4"},
            "context": {"k": 3},
            "config": {"configurable": {"thread_id": "should-not-resume"}},
        },
    )
    assert replay.status_code == 200
    body = replay.json()
    assert body["output"]["answer"] == "1 2 3 4:3"
    assert body["config"]["configurable"]["thread_id"].startswith("studio-playground-")
    event_names = [event["event"] for event in body["events"]]
    assert event_names[0] == "on_graph_start"
    assert event_names[-1] == "on_graph_end"
    assert "on_node_start" in event_names
    assert "on_node_update" in event_names
    updates = [event for event in body["events"] if event["event"] == "on_node_update"]
    assert updates[0]["node"] == "solve"
    assert updates[0]["data"]["update"] == {"answer": "1 2 3 4:3"}

    traces = client.get("/api/traces").json()
    assert [item["id"] for item in traces] == [trace_id]


def test_backend_playground_schema_prefers_declared_input_schema(tmp_path, monkeypatch):
    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)
    trace_id = "trace-declared-schema"

    response = client.post(
        "/api/trace",
        json={
            "id": trace_id,
            "name": "declared",
            "input": {"question": "old", "message": "legacy"},
            "metadata_json": {
                "schema": {
                    "input": [{"name": "question", "type": "any", "required": True}],
                    "context": [],
                    "config": [],
                },
                "invoke": {"input": {"question": "old", "message": "legacy"}},
            },
        },
    )
    assert response.status_code == 200

    playground = client.get(f"/api/traces/{trace_id}/playground").json()

    assert playground["schema"]["input"] == [{"name": "question", "type": "str", "required": True}]


def test_backend_replay_adds_trace_python_paths_before_unpickle(tmp_path, monkeypatch):
    import cloudpickle

    helper_dir = tmp_path / "helpers"
    helper_dir.mkdir()
    (helper_dir / "helper_mod.py").write_text("VALUE = 'from-helper'\n", encoding="utf-8")
    sys.path.insert(0, str(helper_dir))
    try:
        from kagraph import END, START, StateGraph
        import helper_mod

        graph = StateGraph()
        graph.add_node("solve", lambda state: {"answer": helper_mod.VALUE})
        graph.add_edge(START, "solve")
        graph.add_edge("solve", END)
        agent_binary = base64.b64encode(cloudpickle.dumps(graph.compile())).decode("utf-8")
    finally:
        sys.path.remove(str(helper_dir))
        sys.modules.pop("helper_mod", None)

    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)
    trace_id = "trace-python-path"
    client.post(
        "/api/trace",
        json={
            "id": trace_id,
            "name": "python-path",
            "metadata_json": {"python": {"sys_path": [str(helper_dir)]}},
            "agent_binary": agent_binary,
        },
    )

    replay = client.post(f"/api/traces/{trace_id}/replay", json={"input": {}})

    assert replay.status_code == 200
    assert replay.json()["output"]["answer"] == "from-helper"


def test_backend_replay_returns_in_memory_nested_graph_events(tmp_path, monkeypatch):
    import cloudpickle
    from kagraph import END, START, StateGraph

    child = StateGraph()
    child.add_node("agent", lambda state: {"child_answer": f"child:{state['task']}"})
    child.add_edge(START, "agent")
    child.add_edge("agent", END)
    child_app = child.compile(name="child_agent")

    parent = StateGraph()

    def execute(state):
        child_result = child_app.invoke({"task": state["task"]})
        return {"answer": child_result["child_answer"]}

    parent.add_node("execute", execute)
    parent.add_edge(START, "execute")
    parent.add_edge("execute", END)
    agent_binary = base64.b64encode(cloudpickle.dumps(parent.compile(name="parent_graph"))).decode("utf-8")

    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)
    trace_id = "trace-nested-replay"
    client.post(
        "/api/trace",
        json={
            "id": trace_id,
            "name": "nested",
            "input": {"task": "demo"},
            "agent_binary": agent_binary,
        },
    )

    replay = client.post(f"/api/traces/{trace_id}/replay", json={"input": {"task": "demo"}})

    assert replay.status_code == 200
    body = replay.json()
    assert body["output"]["answer"] == "child:demo"
    event_names = [event["event"] for event in body["events"]]
    assert event_names.count("on_graph_start") == 1
    assert event_names.count("on_graph_end") == 1
    assert "on_subgraph_start" in event_names
    assert "on_subgraph_end" in event_names


def test_backend_graph_playground_replays_agent_factory(tmp_path, monkeypatch):
    main = _load_backend(tmp_path, monkeypatch)
    client = TestClient(main.app)
    trace_id = "trace-factory"
    factory_path = tmp_path / "factory_agent.py"
    factory_path.write_text(
        """
from typing_extensions import TypedDict
from kagraph import END, START, StateGraph

class Input(TypedDict):
    problem: str

class State(Input, total=False):
    answer: str

class Context(TypedDict, total=False):
    k: int

def build_graph_for_studio(model_id=None):
    graph = StateGraph(State, context_schema=Context, input_schema=Input)
    graph.add_node("solve", lambda state, *, runtime: {"answer": f"{state['problem']}:{runtime.context.get('k')}:{model_id}"})
    graph.add_edge(START, "solve")
    graph.add_edge("solve", END)
    return graph.compile(name="factory_agent")
""",
        encoding="utf-8",
    )

    client.post(
        "/api/trace",
        json={
            "id": trace_id,
            "name": "factory",
            "input": {"problem": "12 1 5 7"},
            "metadata_json": {
                "agent_factory": {"module_path": str(factory_path), "function": "build_graph_for_studio"},
                "invoke": {
                    "input": {"problem": "12 1 5 7"},
                    "context": {"k": 5},
                },
            },
        },
    )

    playground = client.get(f"/api/traces/{trace_id}/playground").json()
    assert playground["replayable"] is True
    assert playground["schema"]["input"] == [{"name": "problem", "type": "str", "required": True}]
    assert playground["schema"]["context"] == [{"name": "k", "type": "int", "required": False}]

    replay = client.post(
        f"/api/traces/{trace_id}/replay",
        json={
            "input": {"problem": "1 2 3 4"},
            "context": {"k": 3},
            "model_id": "model-x",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["output"]["answer"] == "1 2 3 4:3:model-x"
