# KaGraph

KaGraph is a [LangGraph](https://github.com/langchain-ai/langgraph)-compatible agent orchestration framework built on top of [Kaggle Benchmarks](https://github.com/Kaggle/kaggle-benchmarks) conversation primitives. It helps you build stateful, multi-node agent workflows with traceable execution, message-aware state updates, checkpointing, and a built-in web studio for inspecting graph behavior.

**If you find this project helpful and would like to support its development, consider buying me a coffee!**

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/huangan)

[▶ Introduction to KaGraph](https://www.youtube.com/watch?v=uHcmUN9Tv4Y)

[Example kbench task](https://www.kaggle.com/benchmarks/tasks/anhoangvo/kagraph-example-task)

## Features

- **Native `kaggle_benchmarks` Integration**: Preserves message roles and objects directly without flattening context.
- **Stateful Workflows**: Define your state schema and update it incrementally through independent graph nodes.
- **Advanced Control Flow**: Supports conditional routing, fan-out/fan-in, timeouts, and retry policies.
- **Built-in Checkpointing**: Pause, inspect, and resume graph execution for human-in-the-loop workflows.
- **KaTrace Studio**: Visualize graph paths, inspect trace events, review LLM calls, compare messages, and replay past runs.

## Prerequisites

KaGraph relies on the `kaggle_benchmarks` library. In Kaggle notebooks, it is already available. In a local environment, install and configure it before running KaGraph.

For local development, configure your model proxy credentials:

```bash
cp .env.example .env
```

Then fill in `MODEL_PROXY_API_KEY` and `MODEL_PROXY_URL`.

## Installation

```bash
pip install kagraphx
```

For local development from source:

```bash
git clone https://github.com/anpc849/ka-graph.git
cd ka-graph
pip install -e ".[dev]"
```

## Quick Start

Start KaTrace Studio first so traces from your graph run are captured and visible:

```bash
# Local machine
kagraph-studio --mode local

# Kaggle notebook or remote environment
!kagraph-studio --mode localtunnel
```

Open `http://127.0.0.1:3000` in local mode, or use the printed LocalTunnel URL in tunnel mode.

Once Studio is running, execute a traced graph:

```python
from kagraph import START, END, StateGraph, MessagesState
from kagraph.llms import load_llm
from kagraph.prompts import invoke_llm
from kagraph.messages import HumanMessage
from kagraph.tracing import trace

llm = load_llm("qwen/qwen3-235b-a22b-instruct-2507")

def agent(state: MessagesState):
    response = invoke_llm(llm, messages=state["messages"], prompt="Answer the user.")
    return {"messages": [response]}

graph = StateGraph(MessagesState)
graph.add_node("agent", agent)
graph.add_edge(START, "agent")
graph.add_edge("agent", END)
app = graph.compile()

with trace("MyFirstAgent"):
    result = app.invoke({"messages": [HumanMessage("Hello!")]})

print(result["messages"][-1].content)
```

## Learn More

Use the docs for API details and the tutorial notebooks for runnable workflows:

- [Overview & Getting Started](docs/index.md)
- [Building Graphs (`StateGraph`)](docs/state_graph.md)
- [Running Graphs (`CompiledStateGraph`)](docs/compiled_graph.md)
- [Prebuilt Nodes & Utilities](docs/prebuilt.md)
- [KaTrace Studio & Tracing](docs/tracing.md)
- [Web Application Guide](docs/webapp/overview.md)
- [Tutorial notebooks](tutorials/)

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Acknowledgements

Special thanks to [Codex](https://openai.com/codex/) and [Antigravity](https://antigravity.google/) for helping accelerate the development of KaGraph. Their support made it easier to prototype, refine, and ship the framework faster. 😍
