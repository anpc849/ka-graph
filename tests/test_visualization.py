from kagraph import END, START, StateGraph


def noop(state):
    return None


def test_get_graph_print_ascii_draws_terminal_graph_boxes(capsys):
    graph = StateGraph()
    graph.add_node("agent", noop)
    graph.add_node("tools", noop)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        lambda state: "tools",
        {"tools": "tools", "end": END},
    )
    graph.add_edge("tools", "agent")

    text = graph.compile().get_graph().print_ascii()

    assert text == capsys.readouterr().out.rstrip()
    assert "|__start__|" in text
    assert "|agent|" in text
    assert "|tools|" in text
    assert "|__end__|" in text
    assert "*" in text
    assert "." in text


def test_get_graph_draw_png_returns_png_bytes():
    graph = StateGraph()
    graph.add_node("step", noop)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)

    png = graph.compile().get_graph().draw_png(return_bytes=True)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_get_graph_draw_png_handles_conditional_self_loop():
    graph = StateGraph()
    graph.add_node("info", noop)
    graph.add_node("add_tool_message", noop)
    graph.add_node("prompt", noop)
    graph.add_edge(START, "info")
    graph.add_conditional_edges("info", lambda state: "info", ["add_tool_message", "info", END])
    graph.add_edge("add_tool_message", "prompt")
    graph.add_edge("prompt", END)

    png = graph.compile().get_graph().draw_png(return_bytes=True)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 2500
