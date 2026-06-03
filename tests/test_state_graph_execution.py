import pytest

from kaggle_benchmarks import actors

from kagraph import END, START, Command, InvalidGraphError, NodeError, StateGraph


def test_invoke_runs_nodes_in_order_and_returns_chat():
    def first(state):
        actors.system.send("first", is_visible_to_llm=False)
        return {"seen": ["first"]}

    def second(state):
        state["seen"].append("second")
        return {"answer": ",".join(state["seen"])}

    graph = StateGraph()
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    result = graph.compile().invoke("hello")

    assert result["answer"] == "first,second"
    assert [message.text for message in result["chat"].messages] == ["hello", "first"]


def test_command_goto_routes_to_named_node():
    def choose(state):
        return Command(goto="done", update={"route": "done"})

    def done(state):
        return {"answer": state["route"]}

    graph = StateGraph()
    graph.add_node("choose", choose)
    graph.add_node("done", done)
    graph.add_edge(START, "choose")
    graph.add_edge("choose", "done")
    graph.add_edge("done", END)

    assert graph.compile().invoke()["answer"] == "done"


def test_node_errors_are_wrapped():
    def boom(state):
        raise ValueError("bad node")

    graph = StateGraph()
    graph.add_node("boom", boom)
    graph.add_edge(START, "boom")
    graph.add_edge("boom", END)

    with pytest.raises(NodeError, match="bad node"):
        graph.compile().invoke()


def test_multiple_unconditional_edges_run_as_parallel_fanout():
    def node(state):
        return {"seen": [state.get("name", "node")]}

    def b(state):
        return {"b": True}

    def c(state):
        return {"c": True}

    graph = StateGraph()
    graph.add_node("a", node)
    graph.add_node("b", b)
    graph.add_node("c", c)
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("a", "c")
    graph.add_edge("b", END)
    graph.add_edge("c", END)

    result = graph.compile().invoke()

    assert result["b"] is True
    assert result["c"] is True
