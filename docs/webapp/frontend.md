# Frontend UI — `webapp/frontend`

## Source Map

| Source File | Description |
|---|---|
| `webapp/frontend/src/app/page.tsx` | Dashboard: aggregate metrics, cost table, charts, recent traces |
| `webapp/frontend/src/app/traces/page.tsx` | Traces list: filtering, search, pagination |
| `webapp/frontend/src/app/traces/[traceId]/page.tsx` | Trace detail: all tabs (overview, timeline, spans, generations, checkpoints, playground, chat) — largest file at ~112 KB |
| `webapp/frontend/src/app/guide/page.tsx` | Getting-started guide |
| `webapp/frontend/src/app/layout.tsx` | Root layout with top navigation bar |
| `webapp/frontend/src/app/globals.css` | Global CSS and Tailwind base styles |
| `webapp/frontend/src/lib/utils.ts` | Shared utility functions (formatting, date handling, etc.) |
| `webapp/frontend/package.json` | NPM dependencies and `dev` / `build` / `start` scripts |

---

## Overview

The KaTrace Studio frontend is a **Next.js 14+ App Router** application written in TypeScript and styled with Tailwind CSS. It communicates with the FastAPI backend at `http://127.0.0.1:8000` through the Next.js `/api/*` proxy and provides a rich browser interface for browsing traces, inspecting agent execution, and replaying runs.

---

## Technology Stack

| Concern | Technology |
|---|---|
| **Framework** | Next.js 14+ (App Router, TypeScript) |
| **Styling** | Tailwind CSS |
| **Data fetching** | Native `fetch` in both Server Components and Client Components |
| **Real-time** | `EventSource` (SSE) for live trace streaming |
| **Charts & visualizations** | Custom SVG / Canvas rendering (no third-party chart library) |

### Starting the Frontend

```bash
cd webapp/frontend
npm install
npm run dev
# Open http://localhost:3000
```

---

## Pages

### `/` — Dashboard (`src/app/page.tsx`)

The landing page. Displays aggregate metrics for all traces.

**Summary cards**:
- Total trace count
- Error rate (% of traces with `ERROR` status)
- Replayable trace count (traces with `agent_binary` stored)
- Total API cost in USD

**Model breakdown table**: for each model seen across all generations —

| Column | Description |
|---|---|
| Model | Model identifier string |
| Input Tokens | Total prompt tokens consumed |
| Output Tokens | Total completion tokens consumed |
| Total USD | Aggregated cost |

**Traces-by-name bar chart**: top 10 most frequently run agent names, rendered as an SVG bar chart.

**Traces-over-time line chart**: daily trace count for the last 30 days, rendered as an SVG line chart.

**Recent traces list**: links directly to individual trace detail pages.

**Data source**: `GET /api/dashboard`

---

### `/traces` — Traces List (`src/app/traces/page.tsx`)

A searchable, filterable, paginated list of all recorded traces.

**Filter controls**:

| Filter | Description |
|---|---|
| Text search | Matches against trace ID, name, session ID, and error message |
| Status | `SUCCESS` / `ERROR` / `RUNNING` dropdown |
| Date from | ISO date lower bound on `start_time` |
| Date to | ISO date upper bound on `start_time` |

**Table columns**:

| Column | Description |
|---|---|
| Trace ID | Truncated UUID with link to detail page |
| Name | Trace name (from `trace_name` in `KaGraphTracer`) |
| Status | Color-coded badge: green for `SUCCESS`, red for `ERROR`, amber for `RUNNING` |
| Start Time | Formatted UTC timestamp |
| Duration | Computed from `start_time` and `end_time` |
| Session ID | Optional session grouping key |

**Pagination**: controlled by `skip` and `limit` query parameters, with Previous / Next page controls.

**Data source**: `GET /api/traces` with query parameters forwarded from the filter form.

---

### `/traces/[traceId]` — Trace Detail (`src/app/traces/[traceId]/page.tsx`)

The most feature-rich page in the Studio. Contains multiple tabs and panels covering every aspect of a single trace.

#### Overview Panel

- Trace name and UUID
- Status badge
- Start time, end time, and total duration
- **Input JSON**: collapsible syntax-highlighted view of the graph input
- **Output JSON**: collapsible syntax-highlighted view of the graph output
- **Error message**: shown when status is `ERROR`

#### Graph Topology

Renders an interactive SVG diagram built from `metadata_json.graph`. Shows all nodes as labelled boxes and edges as directed arrows, matching the actual LangGraph graph definition used for the run.

#### Event Timeline

Full chronological log of all `TraceEvent` records for the trace.

| Column | Description |
|---|---|
| Sequence | Monotonic event index |
| Timestamp | Event time (relative and absolute) |
| Event Type | e.g. `on_node_start`, `on_tool_end` |
| Name | Associated node or tool name |
| Data | Collapsible JSON payload |
| Metadata | Collapsible metadata JSON |

Supports incremental loading — fetches additional events as the user scrolls.

#### Node Spans

Tree view of all `Span` records for the trace. The `parent_id` hierarchy is rendered as an indented tree:

```
▶ agent (NODE)           23ms   SUCCESS
  └─ web_search (TOOL)    8ms   SUCCESS
▶ tools (NODE)            5ms   SUCCESS
```

Each span row expands to show input JSON, output JSON, status, and duration.

#### LLM Generations

List of all `Generation` records — one per LLM call — with:

- Model name
- Input messages (collapsible)
- Output / response (collapsible)
- Input token count
- Output token count
- USD cost

#### Checkpoints

List of all checkpoint events with state snapshots. Useful for inspecting intermediate graph state at each checkpoint.

#### Playground Tab

A replay form pre-populated from `GET /api/traces/{id}/playground`.

| Field | Description |
|---|---|
| `input` | Graph input JSON (editable) |
| `context` | Optional context override |
| `config` | LangGraph `RunnableConfig` overrides |
| `recursion_limit` | Max graph recursion depth |
| `model_id` | Override the LLM model for the replay |
| `system_instructions` | Override the system prompt |

Clicking **Run Replay** posts to `POST /api/traces/{id}/replay` and streams the resulting events into a live event feed below the form.

#### Agent Chat Tab

A full multi-turn conversation interface with the replayed agent.

- **Send a message**: posts to `POST /api/agent/chat` with the trace ID and the message text.
- **Conversation history**: displays the full turn-by-turn exchange.
- **Clear session**: calls `DELETE /api/agent/chat/{traceId}` to reset the multi-turn state.

#### Live Streaming

When the trace status is `RUNNING`, the page automatically subscribes to `GET /api/traces/{id}/stream` (SSE). New events are appended to the Event Timeline and Node Spans sections in real-time without a page refresh. The SSE connection is closed when the trace transitions to `SUCCESS` or `ERROR`.

**Data sources**:
- `GET /api/traces/{id}` — core trace data, spans, generations
- `GET /api/traces/{id}/events` — event log (paginated)
- `GET /api/traces/{id}/checkpoints` — checkpoint events
- `GET /api/traces/{id}/stream` — SSE live events (when `RUNNING`)
- `GET /api/traces/{id}/playground` — replay form defaults
- `POST /api/traces/{id}/replay` — trigger replay
- `POST /api/agent/chat` — multi-turn chat
- `DELETE /api/agent/chat/{id}` — clear session

---

### `/guide` — Getting-Started Guide (`src/app/guide/page.tsx`)

A static page that walks new users through setting up KaGraph and KaTrace Studio from scratch:

- **Prerequisites**: Python, Node.js, pip/npm
- **Installation**: backend and frontend setup commands
- **First trace**: minimal code example
- **Common patterns**: multi-agent tracing, replay setup, session grouping

---

## API Base URL

The frontend proxies `/api/*` to `http://127.0.0.1:8000`. To change this for staging or production deployments, update `next.config.ts` or use a Next.js environment variable:

```ts
// next.config.ts
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000';
```

Then reference `process.env.NEXT_PUBLIC_API_URL` in your components. The variable must be prefixed with `NEXT_PUBLIC_` to be exposed to the browser.

