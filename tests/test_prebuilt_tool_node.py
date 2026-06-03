from kaggle_benchmarks import actors
from kaggle_benchmarks.messages import Message

from kagraph import END, START, StateGraph, get_chat
from kagraph.prebuilt import ToolNode


def add(a: int, b: int) -> int:
    return a + b


def test_tool_node_executes_last_message_tool_calls():
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
    }

    def seed_message(state):
        assistant = actors.Actor(name="assistant", role="assistant")
        get_chat().append(Message("", sender=assistant, _meta={"tool_calls": [call]}))
        return {}

    graph = StateGraph()
    graph.add_node("seed", seed_message)
    graph.add_node("tools", ToolNode([add]))
    graph.add_edge(START, "seed")
    graph.add_edge("seed", "tools")
    graph.add_edge("tools", END)

    state = graph.compile().invoke()

    assert state["tool_results"][0].output == 5
    assert state["chat"].messages[-1].sender.role == "tool"
    assert state["chat"].messages[-1].content.output == 5
