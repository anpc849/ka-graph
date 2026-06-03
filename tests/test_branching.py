import pytest

from kagraph import END, START, InvalidGraphError, StateGraph


def test_conditional_edges_route_by_state():
    def start(state):
        return {"choice": "right"}

    def right(state):
        return {"answer": "routed"}

    graph = StateGraph()
    graph.add_node("start", start)
    graph.add_node("right", right)
    graph.add_edge(START, "start")
    graph.add_conditional_edges(
        "start",
        lambda state: state["choice"],
        {"right": "right", "end": END},
    )
    graph.add_edge("right", END)

    assert graph.compile().invoke()["answer"] == "routed"


def test_unknown_route_label_is_invalid():
    graph = StateGraph()
    graph.add_node("start", lambda state: None)
    graph.add_edge(START, "start")
    graph.add_conditional_edges("start", lambda state: "missing", {"end": END})

    with pytest.raises(InvalidGraphError, match="missing"):
        graph.compile().invoke()
