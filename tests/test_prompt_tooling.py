from __future__ import annotations

from kaggle_benchmarks.actors import LLMChat
from kaggle_benchmarks.actors.llms import LLMResponse

from kagraph import END, START, ChatPrompt, MessagesPlaceholder, MessagesState, StateGraph
from kagraph.prebuilt import ToolNode, tools_condition


class ScriptedToolLLM(LLMChat):
    def __init__(self):
        super().__init__(name="scripted")
        self.seen_messages: list[list[tuple[str, str]]] = []
        self.seen_tools: list[list] = []

    def invoke(self, messages, system=None, tools=None, **kwargs):
        self.seen_messages.append([(message.sender.role, message.text) for message in messages])
        self.seen_tools.append(list(tools or []))
        return LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "add", "arguments": {"a": 4, "b": 5}},
                }
            ],
        )


def add(a: int, b: int) -> int:
    return a + b


def test_prompt_invocation_preserves_messages_and_forwards_tools():
    llm = ScriptedToolLLM()
    prompt = ChatPrompt.from_messages(
        [
            ("system", "You are a calculator."),
            MessagesPlaceholder("messages"),
            ("user", "Return the tool call only."),
        ]
    )

    response = prompt.invoke(
        llm,
        {"messages": [("user", "what is 4 + 5?")]},
        tools=[add],
        chat_name="tool prompt",
    )

    assert response.tool_calls[0]["function"]["name"] == "add"
    assert llm.seen_messages == [
        [
            ("system", "You are a calculator."),
            ("user", "what is 4 + 5?"),
            ("user", "Return the tool call only."),
        ]
    ]
    assert llm.seen_tools == [[add]]


def test_current_tool_loop_pattern_uses_messages_state_and_tool_node():
    llm = ScriptedToolLLM()

    def agent(state):
        response = ChatPrompt.from_messages([MessagesPlaceholder("messages")]).invoke(
            llm,
            {"messages": state["messages"]},
            tools=[add],
            chat_name="tool loop",
        )
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode([add]))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", END)

    result = graph.compile().invoke({"messages": [("user", "what is 4 + 5?")]})

    assert "tool_results" not in result
    assert [message.sender.role for message in result["messages"]] == ["user", "assistant", "tool"]
    assert result["messages"][-1].content.output == 9
