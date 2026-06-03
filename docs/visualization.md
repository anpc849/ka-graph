# Graph Visualization — `kagraph.graph.visualization`

## Source Map

| File | Contents |
|------|----------|
| `src/kagraph/graph/visualization.py` | `KaGraphView`, `GraphNode`, `GraphEdge`, ASCII canvas renderer, PNG renderer, Sugiyama layout wrapper |
| `src/kagraph/graph/__init__.py` | Re-exports `KaGraphView`, `GraphNode`, `GraphEdge` |
| `src/kagraph/__init__.py` | Re-exports `KaGraphView` |
| `src/kagraph/graph/state.py` | `CompiledStateGraph.get_graph()` method |

---

## Overview

KaGraph can render compiled graphs as human-readable **ASCII diagrams** or publication-quality **PNG images**. Both renderers use a Sugiyama hierarchical layout algorithm to place nodes in layers, route edges cleanly between them, and group subgraph clusters visually.

Visualization is accessed through the `KaGraphView` object returned by `compiled_graph.get_graph()`.

```python
app = graph.compile()
view = app.get_graph()
```

---

## Installation

The visualization features have optional dependencies:

```bash
# Required for layout engine (needed by both ASCII and PNG rendering)
pip install grandalf

# Required additionally for PNG rendering
pip install pillow

# Or install everything at once via the extras group
pip install 'kagraph[viz]'
```

If `grandalf` is not installed, calling `print_ascii()`, `draw_ascii()`, or `draw_png()` will raise an `ImportError` with a helpful install message.

---

## Data Classes

### `GraphNode`

```python
@dataclass
class GraphNode:
    id: str
    name: str | None = None
    metadata: dict | None = None
```

Represents a single node in the visualization graph.

| Field | Description |
|-------|-------------|
| `id` | Full node ID. For nodes inside subgraphs, this may include a prefix, e.g. `'subgraph:node_name'`. |
| `name` | Display name — the portion of `id` after the last `':'`. Used as the label in diagrams. |
| `metadata` | Optional metadata dict. The key `'cluster'` is used to group nodes into subgraph boxes, e.g. `{'cluster': 'my_subgraph'}`. |

---

### `GraphEdge`

```python
@dataclass
class GraphEdge:
    source: str
    target: str
    label: str | None = None
    conditional: bool = False
```

Represents a directed edge between two nodes.

| Field | Description |
|-------|-------------|
| `source` | ID of the source node. |
| `target` | ID of the target node. |
| `label` | Optional edge label, e.g. the name of the routing condition. |
| `conditional` | If `True`, the edge is rendered as a dashed line (`.` characters in ASCII; dashed stroke in PNG), indicating a conditional/optional path. |

---

## `KaGraphView`

The main visualization object. Obtained via `compiled_graph.get_graph()`.

```python
view: KaGraphView = app.get_graph()

# Inspect the graph structure
print(view.nodes)   # tuple[GraphNode, ...]
print(view.edges)   # tuple[GraphEdge, ...]
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `nodes` | `tuple[GraphNode, ...]` | All nodes in the visualization graph. |
| `edges` | `tuple[GraphEdge, ...]` | All directed edges. |

---

### Methods

#### `print_ascii() -> str`

Prints a LangGraph-style ASCII diagram to stdout and also returns it as a string. Useful for quick inspection in terminals and notebooks.

```python
view.print_ascii()
# Output:
# +-------+      +--------+      +-----+
# | START | *--> | my_node| *--> | END |
# +-------+      +--------+      +-----+
```

#### `draw_ascii() -> str`

Returns the ASCII diagram as a string **without** printing it. Use this when you want to capture the output programmatically.

```python
diagram = view.draw_ascii()
with open('graph.txt', 'w') as f:
    f.write(diagram)
```

#### `draw_png(path=None, *, return_bytes=False)`

Renders the graph to a PNG image using the Sugiyama layout engine and Pillow.

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str \| None` | If provided, saves the PNG to this file path. |
| `return_bytes` | `bool` | If `True`, returns raw PNG bytes instead of an `Image` object. Default: `False`. |

**Return value:**

- In a **Jupyter notebook** (with IPython available): returns an `IPython.display.Image` object that renders inline.
- With `path` set: saves the file and returns `None`.
- With `return_bytes=True`: returns `bytes`.

```python
# Display inline in Jupyter
display(view.draw_png())

# Save to file
view.draw_png('/tmp/my_graph.png')

# Get raw bytes (e.g. for serving over HTTP)
png_bytes = view.draw_png(return_bytes=True)
```

#### `to_json() -> dict`

Returns a JSON-serialisable dict describing the graph structure. Useful for custom rendering pipelines or exporting to frontend visualization libraries.

```python
graph_json = view.to_json()
# {
#   'nodes': [{'id': '__start__'}, {'id': 'my_node'}, {'id': '__end__'}],
#   'edges': [
#     {'source': '__start__', 'target': 'my_node'},
#     {'source': 'my_node', 'target': '__end__'}
#   ]
# }
```

Edge dicts include `'label'` and `'conditional'` keys only when they are set.

---

## Subgraph Expansion

When a node's callable is itself a `CompiledStateGraph`, `get_graph()` can expand it inline so the full nested structure is visible in a single diagram.

```python
# Automatic expansion for CompiledStateGraph nodes
subgraph = subgraph_builder.compile()
main_graph.add_node('step', subgraph)

# The visualization expands 'step' to show all of subgraph's internal nodes
view = main_graph.compile().get_graph()
```

For wrapper functions around compiled subgraphs, set the `__kagraph_subgraph__` attribute to opt into expansion:

```python
compiled_sub = inner_graph.compile()

def my_wrapper(state):
    return compiled_sub.invoke(state)

my_wrapper.__kagraph_subgraph__ = compiled_sub

main_graph.add_node('step', my_wrapper)
```

Expanded subgraph nodes are visually grouped with:
- A **yellow background cluster box** in PNG output.
- A `metadata={'cluster': 'subgraph_name'}` annotation on each `GraphNode`.

---

## PNG Rendering Details

The PNG renderer produces polished, publication-quality output:

| Feature | Detail |
|---------|--------|
| **Layout algorithm** | Sugiyama layered layout (via `grandalf`) |
| **Edge curves** | Smooth Bézier curves via Chaikin's corner-cutting algorithm (4 iterations) |
| **Parallel edges** | Spread horizontally to avoid overlap |
| **Arrowheads** | Filled triangular arrowheads on all edges |
| **Drop shadows** | Subtle shadows on node boxes |
| **Node shapes** | Regular nodes: white with blue outline, rounded rectangle |
| **`START` node** | Emerald green circle |
| **`END` node** | Orange circle |
| **Conditional edges** | Dashed stroke |
| **Subgraph clusters** | Yellow background rectangle |

---

## Full Examples

### ASCII Diagram

```python
from kagraph import StateGraph, START, END
from kagraph.messages import MessagesState

def agent(state): ...
def tools(state): ...

graph = StateGraph(MessagesState)
graph.add_node('agent', agent)
graph.add_node('tools', tools)
graph.add_edge(START, 'agent')
graph.add_conditional_edges('agent', lambda s: 'tools', {'tools': 'tools', '__end__': END})
graph.add_edge('tools', 'agent')

app = graph.compile()
view = app.get_graph()
view.print_ascii()
```

### PNG in Jupyter

```python
from IPython.display import display

view = app.get_graph()
display(view.draw_png())
```

### PNG to File

```python
view.draw_png('outputs/agent_graph.png')
```

### Raw PNG Bytes (e.g. for a web server)

```python
from flask import Flask, Response
app_server = Flask(__name__)

@app_server.route('/graph.png')
def graph_image():
    png_bytes = view.draw_png(return_bytes=True)
    return Response(png_bytes, mimetype='image/png')
```

### JSON Export for Custom Rendering

```python
import json

graph_data = view.to_json()
print(json.dumps(graph_data, indent=2))
# {
#   "nodes": [
#     {"id": "__start__"},
#     {"id": "agent"},
#     {"id": "tools"},
#     {"id": "__end__"}
#   ],
#   "edges": [
#     {"source": "__start__", "target": "agent"},
#     {"source": "agent", "target": "tools", "conditional": true},
#     {"source": "agent", "target": "__end__", "conditional": true},
#     {"source": "tools", "target": "agent"}
#   ]
# }
```


