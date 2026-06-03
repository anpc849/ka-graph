# KaTrace Studio — Web Application Overview

## Source Map

| Source File | Description |
|---|---|
| `webapp/backend/main.py` | All FastAPI endpoints — ingestion, query, replay, SSE streaming |
| `webapp/backend/models.py` | SQLAlchemy ORM models: `Trace`, `Span`, `Generation`, `TraceEvent`, `ReplaySession` |
| `webapp/backend/database.py` | SQLite engine setup and session factory |
| `webapp/frontend/src/app/page.tsx` | Dashboard page |
| `webapp/frontend/src/app/traces/page.tsx` | Traces list page |
| `webapp/frontend/src/app/traces/[traceId]/page.tsx` | Trace detail page |
| `webapp/frontend/src/app/guide/page.tsx` | Getting-started guide page |

---

## What is KaTrace Studio?

**KaTrace Studio** is the full-stack observability and debugging dashboard for KaGraph agent runs. It captures traces, spans, LLM generations, and raw events; displays them in a rich browser UI; and supports **agent replay** — re-running any previously recorded trace with modified inputs, directly from the browser.

Core capabilities:

- **Trace ingestion**: receives structured events from `KaGraphTracer` via HTTP.
- **Persistent storage**: all data is stored in a local SQLite database (`traces.db`).
- **Rich UI**: timeline views, node execution trees, checkpoint browsers, state diffs, and graph topology diagrams.
- **Cost tracking**: per-model token counts and USD costs aggregated across all traces.
- **Replay / Playground**: deserializes pickled graph binaries and re-runs them with new inputs.
- **Agent Chat**: multi-turn conversation with a replayed agent, directly from the browser.
- **Live streaming**: SSE-based real-time event streaming for in-progress traces.

---

## Architecture

```
┌─────────────────┐     HTTP      ┌──────────────────┐    SQLite    ┌──────────────┐
│  KaGraph Agent  │ ──────────►  │  FastAPI Backend  │ ──────────►  │  traces.db   │
│ (KaGraphTracer) │               │     :8000         │              │  (SQLite)    │
└─────────────────┘               └────────┬──────────┘              └──────────────┘
                                           │  REST API
                                  ┌────────▼──────────┐
                                  │  Next.js Frontend  │
                                  │      :3000         │
                                  └───────────────────┘
```

The three components are:

| Component | Technology | Default Port |
|---|---|---|
| **KaGraph Agent** | Python (any environment) | — |
| **Backend** | FastAPI + SQLAlchemy + SQLite | `8000` |
| **Frontend** | Next.js (TypeScript, Tailwind CSS) | `3000` |

All three can run on the same machine during development. For production, the backend and frontend can be deployed separately.

---

## Directory Structure

```
webapp/
├── backend/
│   ├── main.py              # FastAPI application (all endpoints)
│   ├── models.py            # SQLAlchemy ORM models
│   ├── database.py          # SQLite engine + session factory
│   └── requirements.txt     # Python dependencies
└── frontend/
    ├── src/
    │   └── app/
    │       ├── page.tsx                    # Dashboard page
    │       ├── traces/
    │       │   ├── page.tsx                # Traces list page
    │       │   └── [traceId]/
    │       │       └── page.tsx            # Trace detail page
    │       └── guide/
    │           └── page.tsx                # Getting-started guide
    ├── package.json
    └── next.config.ts
```

---

## Starting the Studio

For the packaged CLI, prefer:

```bash
kagraph-studio --mode local
```

In Kaggle or another hosted notebook, expose the frontend through LocalTunnel:

```bash
kagraph-studio --mode localtunnel
```

LocalTunnel mode uses a production Next.js frontend by default. This avoids dev-server HMR and chunk-loading failures through the tunnel. Local mode uses the dev frontend by default.

Add `--verbose` to keep the cell attached and stream backend logs in real time:

```bash
kagraph-studio --mode localtunnel --verbose
```

### 1. Start the Backend

```bash
cd webapp/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The backend creates `traces.db` in the working directory on first run. When launched through `kagraph-studio`, this is the directory where the user ran `kagraph-studio`, even though the FastAPI process itself runs from the installed webapp backend folder.

### 2. Start the Frontend

```bash
cd webapp/frontend
npm install
npm run dev
```

Open **http://localhost:3000** in your browser.

### 3. Run Your Agent with Tracing

```python
from kagraph.tracing import trace

with trace('MyAgent', backend_url='http://127.0.0.1:8000'):
    result = app.invoke({'question': 'Hello, KaGraph!'})
```

Refresh the Studio — your trace will appear on the Dashboard and in the Traces list.

---

## Environment Variables

### Agent Side

| Variable | Default | Description |
|---|---|---|
| `KATRACE_BACKEND_URL` | `http://127.0.0.1:8000` | Backend URL used by `KaGraphTracer` when `backend_url` is not passed explicitly to `trace()` |
| `KATRACE_DB_PATH` | `./traces.db` | SQLite file path used by Studio when `KATRACE_DB_URL` is not set |
| `KATRACE_DB_URL` | `sqlite:///./traces.db` | Full SQLAlchemy database URL for the Studio backend |

### Backend Side

The backend loads a `.env` file from the repository root. Relevant variables:

| Variable | Description |
|---|---|
| `MODEL_PROXY_API_KEY` | API key for the LLM proxy used during agent replay |

---

## Data Flow

The full lifecycle of a trace from agent execution to browser display:

```
1. Agent calls app.invoke() inside a trace() context
        │
        ▼
2. KaGraphTracer captures events and batches them
        │
        ├── POST /api/trace          → creates Trace record
        ├── POST /api/span           → creates Span records (nodes, tools)
        ├── POST /api/generation     → creates Generation records (LLM calls)
        └── POST /api/events/batch   → stores raw TraceEvent records
        │
        ▼
3. FastAPI backend persists everything to traces.db (SQLite)
        │
        ▼
4. Frontend queries the backend REST API
        │
        ├── GET /api/dashboard       → aggregate metrics
        ├── GET /api/traces          → trace list with filters
        ├── GET /api/traces/{id}     → full trace detail
        └── GET /api/traces/{id}/stream  → SSE live events
        │
        ▼
5. Browser renders the Studio UI
```

For **replay**, the Studio retrieves the `agent_binary` blob from the `Trace` record, deserializes it with `cloudpickle`, and calls `invoke()` with the new inputs provided by the user.

---

## Key Features of the Studio UI

### Dashboard (`/`)
- Real-time **cost tracking** broken down by model.
- **Trace volume** over time (daily chart).
- **Traces by name** bar chart (top 10 agent names).
- Recent traces list with quick links to detail pages.

### Traces List (`/traces`)
- Full-text search across trace IDs, names, session IDs, and error messages.
- Filter by **status** (`SUCCESS` / `ERROR` / `RUNNING`), **date range**, and **session ID**.
- Paginated with `skip`/`limit` controls.

### Trace Detail (`/traces/[traceId]`)
The most feature-rich page:
- **Overview panel**: name, status badge, timing, input/output JSON, error message.
- **Graph topology**: interactive SVG diagram rendered from `metadata_json.graph`.
- **Event timeline**: full chronological event log with collapsible JSON data.
- **Node spans**: nested tree of all node/tool spans with durations and I/O.
- **LLM generations**: model, messages, output, token counts, cost per call.
- **Checkpoints**: state snapshots at each checkpoint event.
- **Playground tab**: form for replay with pre-populated defaults.
- **Agent Chat tab**: multi-turn conversation with the replayed agent.
- **Live streaming**: auto-subscribes to SSE when trace status is `RUNNING`.

### Playground / Replay
Re-run any trace with modified inputs — no agent code needed. The Studio deserializes the pickled graph binary and invokes it fresh. Replay events stream back to the browser in real-time.

### Getting-Started Guide (`/guide`)
Step-by-step setup walkthrough with code snippets, prerequisites, and common usage patterns.
