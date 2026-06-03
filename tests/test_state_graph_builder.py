import pytest

from kagraph import END, START, CycleError, InvalidGraphError, StateGraph


def noop(state):
    return None


def test_state_graph_compiles_minimal_graph():
    graph = StateGraph()
    graph.add_node("step", noop)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)

    compiled = graph.compile()

    assert compiled.nodes["step"].bound is noop
    assert (START, "step") in compiled.edges


def test_compile_rejects_unreachable_node():
    graph = StateGraph()
    graph.add_node("step", noop)
    graph.add_node("orphan", noop)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)

    with pytest.raises(InvalidGraphError, match="Unreachable"):
        graph.compile()


def test_compile_rejects_unconditional_cycle():
    graph = StateGraph()
    graph.add_node("a", noop)
    graph.add_node("b", noop)
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", "a")
    graph.add_edge("b", END)

    with pytest.raises(CycleError):
        graph.compile()


def test_conditional_cycle_is_allowed_for_agent_loops():
    graph = StateGraph()
    graph.add_node("agent", noop)
    graph.add_node("tools", noop)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", lambda state: "tools", {"tools": "tools", "end": END})
    graph.add_edge("tools", "agent")

    assert graph.compile()
