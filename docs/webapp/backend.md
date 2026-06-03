# Backend API Reference — `webapp/backend`

## Source Map

| Source File | Description |
|---|---|
| `webapp/backend/main.py` | All endpoint implementations, serialization helpers, `_InMemoryReplayEventCollector`, SSE streaming logic, replay mechanism |
| `webapp/backend/models.py` | All SQLAlchemy ORM models: `Trace`, `Span`, `Generation`, `TraceEvent`, `ReplaySession` |
| `webapp/backend/database.py` | SQLite engine configuration, `SessionLocal` factory, `Base` declarative base, `get_db()` dependency |

---

## Overview

The KaTrace Studio backend is a **FastAPI** application that serves two roles:

1. **Ingestion target** — `KaGraphTracer` POSTs events, spans, and generations here during a graph run.
2. **Data source** — the Next.js frontend GETs traces, events, and aggregated metrics from here.

All data is persisted in a local **SQLite** database (`traces.db`) managed by **SQLAlchemy**. By default, `kagraph-studio` creates this file in the directory where the user ran the command, not inside the installed package. The backend starts on port `8000` by default.

```bash
cd webapp/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

To choose a database location manually, set either:

```bash
export KATRACE_DB_PATH=/path/to/traces.db
# or
export KATRACE_DB_URL=sqlite:////path/to/traces.db
```

---

## Database Models (`models.py`)

Five SQLAlchemy ORM models map to five database tables.

### `Trace` — table `traces`

The top-level record for a single graph run.

| Column | Type | Description |
|---|---|---|
| `id` | `String` PK | Trace UUID |
| `name` | `String` | Trace name (from `trace_name` parameter) |
| `input` | `JSON` | Graph input payload |
| `output` | `JSON` | Graph output payload |
| `session_id` | `String` | Optional session grouping key |
| `user_id` | `String` | Optional user identifier |
| `start_time` | `DateTime` | Run start timestamp (UTC) |
| `end_time` | `DateTime` | Run end timestamp (UTC) |
| `status` | `String` | `SUCCESS`, `ERROR`, or `RUNNING` |
| `error` | `String` | Error message if the run failed |
| `metadata_json` | `JSON` | Graph topology, schema, invoke parameters |
| `agent_binary` | `LargeBinary` | Pickled compiled graph (optional, for replay) |

---

### `Span` — table `spans`

One record per node or tool execution within a trace.

| Column | Type | Description |
|---|---|---|
| `id` | `String` PK | Span UUID |
| `trace_id` | `String` FK | Parent trace |
| `parent_id` | `String` | Parent span ID (enables nesting) |
| `name` | `String` | Node or tool name |
| `span_type` | `String` | `NODE`, `TOOL`, `SPAN`, or `AGENT` |
| `input` | `JSON` | Input to the node/tool |
| `output` | `JSON` | Output from the node/tool |
| `start_time` | `DateTime` | Span start timestamp |
| `end_time` | `DateTime` | Span end timestamp |
| `status` | `String` | `SUCCESS`, `ERROR`, or `RUNNING` |
| `error` | `String` | Error message if the span failed |
| `metadata_json` | `JSON` | Additional span metadata |

---

### `Generation` — table `generations`

One record per LLM call within a trace.

| Column | Type | Description |
|---|---|---|
| `id` | `String` PK | Generation UUID |
| `trace_id` | `String` FK | Parent trace |
| `parent_id` | `String` | Parent span ID |
| `name` | `String` | Generation label |
| `model` | `String` | Model identifier (e.g. `qwen/qwen-2.5-72b-instruct`) |
| `input` | `JSON` | Input message list |
| `output` | `JSON` | Model response |
| `usage_input_tokens` | `Integer` | Prompt token count |
| `usage_output_tokens` | `Integer` | Completion token count |
| `cost_total` | `Float` | Total USD cost |
| `metadata_json` | `JSON` | Additional generation metadata |

---

### `TraceEvent` — table `trace_events`

Granular event log — one row per event emitted during a graph run.

| Column | Type | Description |
|---|---|---|
| `id` | `String` PK | Event UUID |
| `trace_id` | `String` FK | Parent trace |
| `sequence` | `Integer` | Monotonic sequence number within the trace |
| `timestamp` | `DateTime` | Event timestamp |
| `event` | `String` | Event type string (e.g. `on_node_start`) |
| `name` | `String` | Associated name (node name, tool name, etc.) |
| `node` | `String` | Node name if applicable |
| `checkpoint_id` | `String` | Checkpoint ID if applicable |
| `parent_ids` | `JSON` | JSON array of parent span IDs |
| `data` | `JSON` | Full event payload |
| `metadata_json` | `JSON` | Additional metadata |

---

### `ReplaySession` — table `replay_sessions`

Tracks multi-turn chat sessions tied to a trace for agent replay.

Each session stores conversation history and a reference back to the originating trace so the Studio can reconstruct the agent and continue the conversation.

---

## Ingestion Endpoints

These endpoints are called by `KaGraphTracer` during a graph run. You do not normally call them directly.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/trace` | Create a new `Trace` record (called on `kagraph_invoke_start`) |
| `PUT` | `/api/trace/{trace_id}` | Update trace `status`, `output`, `end_time` (called on run end/error) |
| `POST` | `/api/span` | Create a `Span` record for a node or tool |
| `PUT` | `/api/span/{span_id}` | Close a span — set `output`, `status`, `end_time` |
| `POST` | `/api/generation` | Create a `Generation` record for an LLM call |
| `PUT` | `/api/generation/{gen_id}` | Close a generation — set `output` |
| `POST` | `/api/events` | Create a single `TraceEvent` record |
| `POST` | `/api/events/batch` | Create a batch of `TraceEvent` records (primary ingestion path) |

### Batch Event Ingestion

`POST /api/events/batch` is the hot path. `KaGraphTracer` accumulates up to `batch_size` events and sends them in a single request to reduce overhead. The request body is a JSON array of event objects.

```json
[
  {
    "trace_id": "abc123",
    "sequence": 1,
    "timestamp": "2025-01-01T00:00:01Z",
    "event": "on_node_start",
    "name": "agent",
    "data": { "input": { "messages": ["..."] } }
  },
  {
    "trace_id": "abc123",
    "sequence": 2,
    "timestamp": "2025-01-01T00:00:02Z",
    "event": "on_node_end",
    "name": "agent",
    "data": { "output": { "messages": ["..."] } }
  }
]
```

---

## Query Endpoints

These endpoints are called by the Next.js frontend.

### Traces

| Method | Path | Query Params | Description |
|---|---|---|---|
| `GET` | `/api/traces` | `skip`, `limit`, `status`, `name`, `session_id`, `search`, `date_from`, `date_to` | List traces with optional filtering and pagination |
| `GET` | `/api/traces/{trace_id}` | — | Full trace detail: spans, generations, and event count |
| `DELETE` | `/api/traces/{trace_id}` | — | Delete a trace and all related spans, generations, and events |

#### `GET /api/traces` — Query Parameters

| Parameter | Type | Description |
|---|---|---|
| `skip` | `int` | Number of records to skip (for pagination) |
| `limit` | `int` | Maximum records to return |
| `status` | `str` | Filter by `SUCCESS`, `ERROR`, or `RUNNING` |
| `name` | `str` | Filter by exact trace name |
| `session_id` | `str` | Filter by session ID |
| `search` | `str` | Full-text search across ID, name, session, and error fields |
| `date_from` | `str` | ISO 8601 start date filter |
| `date_to` | `str` | ISO 8601 end date filter |

### Events

| Method | Path | Query Params | Description |
|---|---|---|---|
| `GET` | `/api/traces/{trace_id}/events` | `after`, `limit`, `event`, `node` | Paginated event log for a trace |
| `GET` | `/api/traces/{trace_id}/stream` | `after` | SSE stream of live events for an in-progress trace |
| `GET` | `/api/traces/{trace_id}/checkpoints` | — | All checkpoint events for a trace |

#### `GET /api/traces/{trace_id}/events` — Query Parameters

| Parameter | Type | Description |
|---|---|---|
| `after` | `int` | Return only events with `sequence > after` (for incremental loading) |
| `limit` | `int` | Maximum events to return |
| `event` | `str` | Filter by event type string |
| `node` | `str` | Filter by node name |

### Dashboard

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/dashboard` | Aggregate metrics across all traces |

#### `GET /api/dashboard` — Response Format

```json
{
  "traces": {
    "total": 42,
    "by_name": [
      {"name": "ReAct", "count": 10},
      {"name": "MyAgent", "count": 5}
    ],
    "replayable": 5
  },
  "events": {
    "total": 1250
  },
  "costs": {
    "total_usd": 1.23,
    "models": [
      {
        "model": "qwen/qwen-2.5-72b-instruct",
        "tokens": 50000,
        "usd": 1.23
      }
    ]
  },
  "traces_by_time": [
    {"date": "2025-01-01", "count": 3},
    {"date": "2025-01-02", "count": 7}
  ]
}
```

---

## Replay / Playground Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/traces/{trace_id}/playground` | Returns schema and pre-populated defaults for the replay form |
| `POST` | `/api/traces/{trace_id}/replay` | Re-run the graph with new inputs; returns replay events |
| `POST` | `/api/traces/{trace_id}/chat` | Send a message in a multi-turn replay session |
| `POST` | `/api/agent/chat` | Chat with the embedded in-memory agent |
| `DELETE` | `/api/agent/chat/{trace_id}` | Clear the multi-turn session for a trace |

### Replay Mechanism

When `/api/traces/{trace_id}/replay` is called:

1. The backend fetches the `Trace` record from SQLite.
2. **If `agent_binary` is present**: deserializes the blob with `cloudpickle.loads()` to recover the compiled graph object, then calls `invoke()` with the user-supplied inputs.
3. **If `agent_factory` is present**: uses the factory specification to rebuild the agent programmatically.
4. A lightweight `_InMemoryReplayEventCollector` intercepts events during the replay run.
5. All collected events are returned in the HTTP response.

The replay runs synchronously in the backend process. For long-running agents, SSE streaming is used to progressively deliver events.

---

## SSE Live Streaming

`GET /api/traces/{trace_id}/stream` returns a **Server-Sent Events** stream that enables the frontend to display events for in-progress traces in real-time.

**Protocol details**:
- The backend polls SQLite every **500 ms** for new `TraceEvent` rows with `sequence > after`.
- Each new event is yielded as:
  ```
  event: trace_event
  data: {"id": "...", "sequence": 42, "event": "on_node_start", ...}

  ```
- The stream idles out automatically after **10 minutes** (1,200 polling ticks × 0.5 s).
- The stream also terminates when the trace reaches `SUCCESS` or `ERROR` status.

**Client-side usage** (simplified):

```typescript
const es = new EventSource(`http://127.0.0.1:8000/api/traces/${traceId}/stream`);
es.addEventListener('trace_event', (e) => {
  const event = JSON.parse(e.data);
  appendEvent(event);
});
```
