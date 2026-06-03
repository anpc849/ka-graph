# KaGraph Studio

KaGraph Studio is a local observability app for inspecting, replaying, and debugging KaGraph benchmark runs. Notebook runs remain the primary execution path; Studio receives trace events from `KaGraphTracer` and stores them for live and post-run analysis.

## Start the backend

```bash
cd E:/kagraph/webapp/backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Start the frontend

```bash
cd E:/kagraph/webapp/frontend
npm run dev
```

Open `http://localhost:3000`.

## Trace a graph

```python
from kagraph.tracing import KaGraphTracer

tracer = KaGraphTracer("http://127.0.0.1:8000")
tracer.attach()

try:
    app.invoke({"messages": [("user", "Solve the benchmark task.")]})
finally:
    tracer.detach()
```

Studio records graph, step, node, tool, message, token, checkpoint, and state-update events. Replay is available only when the trace includes a serialized graph and enough checkpoint/state data.
