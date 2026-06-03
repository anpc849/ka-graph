import os
from dotenv import load_dotenv

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from typing import get_args, get_origin, get_type_hints
from datetime import datetime, date
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import time
import uuid
from pathlib import Path

from kaggle_benchmarks import events as kbench_events

import models
from database import DATABASE_URL, engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="KaTrace Backend")

# Load global Kaggle Benchmarks .env
load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BACKEND_LOG_PATH = Path(os.getenv("KATRACE_BACKEND_LOG", "backend.log")).resolve()


def _read_log_tail(path: Path, max_lines: int = 200) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.readlines()[-max_lines:]
    except OSError as error:
        return [f"Could not read {path}: {error}\n"]

# --- Pydantic Schemas ---

class TraceCreate(BaseModel):
    id: str
    name: str
    input: Optional[Any] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    start_time: Optional[datetime] = None
    metadata_json: Optional[Dict[str, Any]] = None
    agent_binary: Optional[str] = None # Base64 encoded string

class TraceUpdate(BaseModel):
    output: Optional[Any] = None
    end_time: Optional[datetime] = None
    status: Optional[str] = None
    error: Optional[str] = None

class SpanCreate(BaseModel):
    id: str
    trace_id: str
    parent_id: Optional[str] = None
    name: str
    span_type: str = "SPAN"
    input: Optional[Any] = None
    metadata_json: Optional[Dict[str, Any]] = None
    start_time: Optional[datetime] = None

class SpanUpdate(BaseModel):
    output: Optional[Any] = None
    end_time: Optional[datetime] = None
    status: Optional[str] = None
    error: Optional[str] = None

class GenerationCreate(BaseModel):
    id: str
    trace_id: str
    parent_id: Optional[str] = None
    name: str
    model: Optional[str] = None
    input: Optional[Any] = None
    usage_input_tokens: Optional[Any] = None
    usage_output_tokens: Optional[Any] = None
    cost_total: Optional[float] = None
    metadata_json: Optional[Any] = None
    start_time: Optional[datetime] = None

class GenerationUpdate(BaseModel):
    output: Optional[Any] = None
    end_time: Optional[datetime] = None

class TraceEventCreate(BaseModel):
    id: Optional[str] = None
    trace_id: str
    sequence: Optional[int] = None
    timestamp: Optional[datetime] = None
    event: str
    name: Optional[str] = None
    node: Optional[str] = None
    checkpoint_id: Optional[str] = None
    parent_ids: Optional[List[str]] = None
    data: Optional[Any] = None
    metadata_json: Optional[Any] = None

class TraceEventBatch(BaseModel):
    events: List[TraceEventCreate]

class ReplayRequest(BaseModel):
    input: Optional[Any] = None
    config: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    recursion_limit: Optional[int] = None
    chat_name: Optional[str] = None
    system_instructions: Optional[str] = None
    model_id: Optional[str] = None
    session_id: Optional[str] = None

class TraceChatRequest(BaseModel):
    input: Optional[Any] = None
    config: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    recursion_limit: Optional[int] = None
    chat_name: Optional[str] = None
    system_instructions: Optional[str] = None
    model_id: Optional[str] = None
    session_id: Optional[str] = None

# --- Ingestion Endpoints ---

@app.post("/api/trace")
def create_trace(trace: TraceCreate, db: Session = Depends(get_db)):
    dump = trace.model_dump()
    if dump.get("agent_binary"):
        import base64
        dump["agent_binary"] = base64.b64decode(dump["agent_binary"])

    db_trace = models.Trace(**dump)
    if not db_trace.start_time:
        db_trace.start_time = datetime.utcnow()
    db.add(db_trace)
    db.commit()
    return {"status": "ok", "id": trace.id}

@app.put("/api/trace/{trace_id}")
def update_trace(trace_id: str, update: TraceUpdate, db: Session = Depends(get_db)):
    db_trace = db.query(models.Trace).filter(models.Trace.id == trace_id).first()
    if not db_trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(db_trace, key, value)
    db.commit()
    return {"status": "ok"}

@app.post("/api/span")
def create_span(span: SpanCreate, db: Session = Depends(get_db)):
    db_span = models.Span(**span.model_dump())
    if not db_span.start_time:
        db_span.start_time = datetime.utcnow()
    db.add(db_span)
    db.commit()
    return {"status": "ok", "id": span.id}

@app.put("/api/span/{span_id}")
def update_span(span_id: str, update: SpanUpdate, db: Session = Depends(get_db)):
    db_span = db.query(models.Span).filter(models.Span.id == span_id).first()
    if not db_span:
        raise HTTPException(status_code=404, detail="Span not found")
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(db_span, key, value)
    db.commit()
    return {"status": "ok"}

@app.post("/api/generation")
def create_generation(gen: GenerationCreate, db: Session = Depends(get_db)):
    dump = gen.model_dump()
    dump["usage_input_tokens"] = _optional_int(dump.get("usage_input_tokens"))
    dump["usage_output_tokens"] = _optional_int(dump.get("usage_output_tokens"))
    if dump.get("metadata_json") is not None and not isinstance(dump["metadata_json"], dict):
        dump["metadata_json"] = {"value": dump["metadata_json"]}
    db_gen = models.Generation(**dump)
    if not db_gen.start_time:
        db_gen.start_time = datetime.utcnow()
    db.add(db_gen)
    db.commit()
    return {"status": "ok", "id": gen.id}

@app.put("/api/generation/{gen_id}")
def update_generation(gen_id: str, update: GenerationUpdate, db: Session = Depends(get_db)):
    db_gen = db.query(models.Generation).filter(models.Generation.id == gen_id).first()
    if not db_gen:
        raise HTTPException(status_code=404, detail="Generation not found")
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(db_gen, key, value)
    db.commit()
    return {"status": "ok"}

@app.post("/api/events")
def create_event(event: TraceEventCreate, db: Session = Depends(get_db)):
    db_event = _create_trace_event(db, event)
    db.commit()
    return {"status": "ok", "id": db_event.id, "sequence": db_event.sequence}

@app.post("/api/events/batch")
def create_events(batch: TraceEventBatch, db: Session = Depends(get_db)):
    next_by_trace: dict[str, int] = {}
    created = []
    for event in batch.events:
        if event.sequence is None:
            if event.trace_id not in next_by_trace:
                next_by_trace[event.trace_id] = _next_event_sequence(db, event.trace_id)
            event.sequence = next_by_trace[event.trace_id]
            next_by_trace[event.trace_id] += 1
        created.append(_create_trace_event(db, event))
    db.commit()
    return {
        "status": "ok",
        "count": len(created),
        "sequences": [event.sequence for event in created],
    }

# --- Dashboard Endpoint ---

@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    trace_count = db.query(models.Trace).count()
    return {
        "status": "ok",
        "database_url": DATABASE_URL,
        "trace_count": trace_count,
        "backend_log": str(BACKEND_LOG_PATH),
    }

@app.get("/api/studio/logs/backend")
def get_backend_log_tail(lines: int = Query(200, ge=1, le=2000)):
    return {
        "path": str(BACKEND_LOG_PATH),
        "lines": _read_log_tail(BACKEND_LOG_PATH, lines),
    }


@app.get("/api/studio/logs/backend/stream")
def stream_backend_log(lines: int = Query(80, ge=1, le=500)):
    def event_stream():
        position = 0
        sent_initial = False
        while True:
            try:
                if not sent_initial:
                    tail = "".join(_read_log_tail(BACKEND_LOG_PATH, lines))
                    if tail:
                        yield f"data: {json.dumps({'text': tail})}\n\n"
                    sent_initial = True
                    position = BACKEND_LOG_PATH.stat().st_size if BACKEND_LOG_PATH.exists() else 0
                elif BACKEND_LOG_PATH.exists():
                    size = BACKEND_LOG_PATH.stat().st_size
                    if size < position:
                        position = 0
                    if size > position:
                        with BACKEND_LOG_PATH.open("r", encoding="utf-8", errors="replace") as handle:
                            handle.seek(position)
                            chunk = handle.read()
                            position = handle.tell()
                        if chunk:
                            yield f"data: {json.dumps({'text': chunk})}\n\n"
            except Exception as error:
                error_text = f"[log stream error] {error}\n"
                yield f"data: {json.dumps({'text': error_text})}\n\n"
            time.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.get("/api/dashboard")
def get_dashboard(db: Session = Depends(get_db)):
    from sqlalchemy import func

    # 1. Traces metric
    total_traces = db.query(models.Trace).count()
    traces_by_name_query = db.query(
        models.Trace.name, func.count(models.Trace.id)
    ).group_by(models.Trace.name).order_by(func.count(models.Trace.id).desc()).limit(10).all()

    traces_by_name = [{"name": row[0] or "unknown", "count": row[1]} for row in traces_by_name_query]
    replayable_traces = db.query(models.Trace).filter(models.Trace.agent_binary.isnot(None)).count()
    total_events = db.query(models.TraceEvent).count()

    # 2. Costs metric
    total_cost = db.query(func.sum(models.Generation.cost_total)).scalar() or 0.0

    models_query = db.query(
        models.Generation.model,
        func.sum(models.Generation.usage_input_tokens),
        func.sum(models.Generation.usage_output_tokens),
        func.sum(models.Generation.cost_total)
    ).group_by(models.Generation.model).order_by(func.sum(models.Generation.cost_total).desc()).limit(10).all()

    costs_models = []
    for row in models_query:
        input_tok = row[1] or 0
        output_tok = row[2] or 0
        costs_models.append({
            "model": row[0] or "unknown",
            "tokens": input_tok + output_tok,
            "usd": row[3] or 0.0
        })

    # 3. Traces by time (daily aggregation)
    traces_time_query = db.query(
        func.date(models.Trace.start_time),
        func.count(models.Trace.id)
    ).group_by(func.date(models.Trace.start_time)).order_by(func.date(models.Trace.start_time).asc()).limit(30).all()

    traces_by_time = [{"date": row[0], "count": row[1]} for row in traces_time_query]

    # 4. Model usage summary for dashboard charts.

    return {
        "traces": {
            "total": total_traces,
            "by_name": traces_by_name,
            "replayable": replayable_traces,
        },
        "events": {"total": total_events},
        "costs": {
            "total_usd": total_cost,
            "models": costs_models
        },
        "traces_by_time": traces_by_time
    }

# --- Retrieval Endpoints ---

def _serialize_trace(trace):
    d = {c.name: getattr(trace, c.name) for c in trace.__table__.columns}
    d["agent_binary"] = bool(d.get("agent_binary")) # Send boolean to avoid UnicodeDecodeError
    if getattr(trace, "end_time", None) and getattr(trace, "start_time", None):
        d["duration_ms"] = int((trace.end_time - trace.start_time).total_seconds() * 1000)
    else:
        d["duration_ms"] = None
    return d

@app.get("/api/traces")
def get_traces(
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
):
    query = db.query(models.Trace)
    if status:
        query = query.filter(models.Trace.status == status)
    if name:
        query = query.filter(models.Trace.name.ilike(f"%{name}%"))
    if session_id:
        query = query.filter(models.Trace.session_id == session_id)
    if search:
        term = f"%{search}%"
        query = query.filter(
            (models.Trace.id.ilike(term))
            | (models.Trace.name.ilike(term))
            | (models.Trace.session_id.ilike(term))
            | (models.Trace.error.ilike(term))
        )
    if date_from:
        query = query.filter(models.Trace.start_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(models.Trace.start_time <= datetime.combine(date_to, datetime.max.time()))
    traces = query.order_by(models.Trace.start_time.desc()).offset(skip).limit(limit).all()
    return [_serialize_trace(t) for t in traces]

@app.get("/api/traces/{trace_id}")
def get_trace(trace_id: str, db: Session = Depends(get_db)):
    trace = db.query(models.Trace).filter(models.Trace.id == trace_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    spans = db.query(models.Span).filter(models.Span.trace_id == trace_id).all()
    generations = db.query(models.Generation).filter(models.Generation.trace_id == trace_id).all()

    return {
        "trace": _serialize_trace(trace),
        "spans": spans,
        "generations": generations,
        "event_count": db.query(models.TraceEvent).filter(models.TraceEvent.trace_id == trace_id).count(),
        "checkpoint_count": _checkpoint_count(db, trace_id),
    }

@app.delete("/api/traces/{trace_id}")
def delete_trace(trace_id: str, db: Session = Depends(get_db)):
    trace = db.query(models.Trace).filter(models.Trace.id == trace_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    deleted = {
        "events": db.query(models.TraceEvent).filter(models.TraceEvent.trace_id == trace_id).delete(synchronize_session=False),
        "generations": db.query(models.Generation).filter(models.Generation.trace_id == trace_id).delete(synchronize_session=False),
        "spans": db.query(models.Span).filter(models.Span.trace_id == trace_id).delete(synchronize_session=False),
        "replay_sessions": db.query(models.ReplaySession).filter(models.ReplaySession.trace_id == trace_id).delete(synchronize_session=False),
    }
    db.delete(trace)
    db.commit()
    return {"status": "ok", "id": trace_id, "deleted": deleted}

@app.get("/api/traces/{trace_id}/events")
def get_trace_events(
    trace_id: str,
    db: Session = Depends(get_db),
    after: Optional[int] = None,
    limit: int = 1000,
    event: Optional[str] = None,
    node: Optional[str] = None,
):
    _require_trace(db, trace_id)
    query = db.query(models.TraceEvent).filter(models.TraceEvent.trace_id == trace_id)
    if after is not None:
        query = query.filter(models.TraceEvent.sequence > after)
    if event:
        query = query.filter(models.TraceEvent.event == event)
    if node:
        query = query.filter(models.TraceEvent.node == node)
    events = query.order_by(models.TraceEvent.sequence.asc()).limit(limit).all()
    return [_serialize_event(item) for item in events]

@app.get("/api/traces/{trace_id}/stream")
def stream_trace_events(trace_id: str, db: Session = Depends(get_db), after: int = -1):
    _require_trace(db, trace_id)

    def event_stream():
        last_sequence = after
        idle_ticks = 0
        while idle_ticks < 1200:
            session = next(get_db())
            try:
                events = (
                    session.query(models.TraceEvent)
                    .filter(models.TraceEvent.trace_id == trace_id)
                    .filter(models.TraceEvent.sequence > last_sequence)
                    .order_by(models.TraceEvent.sequence.asc())
                    .limit(100)
                    .all()
                )
                if events:
                    idle_ticks = 0
                    for event in events:
                        last_sequence = event.sequence
                        yield f"event: trace_event\ndata: {json.dumps(_serialize_event(event), default=str)}\n\n"
                else:
                    idle_ticks += 1
                    yield ": keep-alive\n\n"
                    time.sleep(0.5)
            finally:
                session.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.get("/api/traces/{trace_id}/checkpoints")
def get_trace_checkpoints(trace_id: str, db: Session = Depends(get_db)):
    _require_trace(db, trace_id)
    events = (
        db.query(models.TraceEvent)
        .filter(models.TraceEvent.trace_id == trace_id)
        .filter(models.TraceEvent.checkpoint_id.isnot(None))
        .order_by(models.TraceEvent.sequence.asc())
        .all()
    )
    checkpoints = {}
    for event in events:
        checkpoints[event.checkpoint_id] = {
            "checkpoint_id": event.checkpoint_id,
            "sequence": event.sequence,
            "timestamp": event.timestamp,
            "event": event.event,
            "name": event.name,
            "node": event.node,
            "data": event.data,
            "metadata": event.metadata_json,
        }
    return list(checkpoints.values())

@app.get("/api/traces/{trace_id}/playground")
def get_trace_playground(trace_id: str, db: Session = Depends(get_db)):
    trace = _require_trace(db, trace_id)
    schema = _trace_playground_schema(trace)
    return {
        "replayable": _is_trace_replayable(trace),
        "defaults": _trace_playground_defaults(trace, db),
        "schema": schema,
        "models": get_models(),
    }

@app.post("/api/traces/{trace_id}/replay")
def replay_trace(trace_id: str, req: ReplayRequest, db: Session = Depends(get_db)):
    trace = _require_trace(db, trace_id)
    result = _run_replayable_agent(
        trace,
        db,
        input_value=req.input,
        config=req.config,
        context=req.context,
        recursion_limit=req.recursion_limit,
        chat_name=req.chat_name,
        system_instructions=req.system_instructions,
        model_id=req.model_id,
    )
    return {"status": "ok", **result}

@app.post("/api/traces/{trace_id}/chat")
def chat_trace(trace_id: str, req: TraceChatRequest, db: Session = Depends(get_db)):
    trace = _require_trace(db, trace_id)
    result = _run_replayable_agent(
        trace,
        db,
        input_value=req.input,
        config=req.config,
        context=req.context,
        recursion_limit=req.recursion_limit,
        chat_name=req.chat_name,
        system_instructions=req.system_instructions,
        model_id=req.model_id,
    )
    return {"status": "ok", **result}


# In-memory session history: { trace_id: [{"role":..., "sender_name":..., "content":...}] }
_chat_sessions: dict[str, list[dict]] = {}

class AgentChatRequest(BaseModel):
    trace_id: str
    input: str
    model_id: Optional[str] = None

@app.post("/api/agent/chat")
def chat_with_agent(req: AgentChatRequest, db: Session = Depends(get_db)):
    trace = _require_trace(db, req.trace_id)
    result = _run_replayable_agent(
        trace,
        db,
        input_value=req.input,
        config=None,
        context=None,
        recursion_limit=None,
        chat_name=None,
        system_instructions=None,
        model_id=req.model_id,
    )
    return {"status": "ok", "messages": result["messages"], "answer": result["output"].get("answer")}

@app.delete("/api/agent/chat/{trace_id}")
def clear_chat_history(trace_id: str):
    """Clear the multi-turn session for a trace so user can start fresh."""
    _chat_sessions.pop(trace_id, None)
    return {"status": "ok"}


def _require_trace(db: Session, trace_id: str):
    trace = db.query(models.Trace).filter(models.Trace.id == trace_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


def _create_trace_event(db: Session, event: TraceEventCreate):
    trace = _require_trace(db, event.trace_id)
    sequence = event.sequence
    if sequence is None:
        sequence = _next_event_sequence(db, event.trace_id)
    db_event = models.TraceEvent(
        id=event.id or str(uuid.uuid4()),
        trace_id=trace.id,
        sequence=sequence,
        timestamp=event.timestamp or datetime.utcnow(),
        event=event.event,
        name=event.name,
        node=event.node,
        checkpoint_id=event.checkpoint_id,
        parent_ids=event.parent_ids or [],
        data=event.data,
        metadata_json=event.metadata_json,
    )
    db.add(db_event)
    if trace.status in (None, "SUCCESS"):
        trace.status = "RUNNING"
    return db_event


def _next_event_sequence(db: Session, trace_id: str) -> int:
    from sqlalchemy import func
    current = db.query(func.max(models.TraceEvent.sequence)).filter(models.TraceEvent.trace_id == trace_id).scalar()
    return int(current or 0) + 1


def _checkpoint_count(db: Session, trace_id: str) -> int:
    return (
        db.query(models.TraceEvent.checkpoint_id)
        .filter(models.TraceEvent.trace_id == trace_id)
        .filter(models.TraceEvent.checkpoint_id.isnot(None))
        .distinct()
        .count()
    )


def _serialize_event(event):
    return {
        "id": event.id,
        "trace_id": event.trace_id,
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "event": event.event,
        "name": event.name,
        "node": event.node,
        "checkpoint_id": event.checkpoint_id,
        "parent_ids": event.parent_ids or [],
        "data": event.data,
        "metadata": event.metadata_json or {},
    }


def _trace_playground_defaults(trace, db: Session) -> dict[str, Any]:
    metadata = trace.metadata_json or {}
    invoke = dict(metadata.get("invoke") or {})
    if "input" not in invoke or invoke.get("input") is None:
        invoke["input"] = trace.input
    if invoke.get("input") is None:
        graph_start = (
            db.query(models.TraceEvent)
            .filter(models.TraceEvent.trace_id == trace.id)
            .filter(models.TraceEvent.event == "on_graph_start")
            .order_by(models.TraceEvent.sequence.asc())
            .first()
        )
        if graph_start and isinstance(graph_start.data, dict):
            invoke["input"] = graph_start.data.get("input")
    first_generation = (
        db.query(models.Generation)
        .filter(models.Generation.trace_id == trace.id)
        .order_by(models.Generation.start_time.asc())
        .first()
    )
    return {
        "input": invoke.get("input") if invoke.get("input") is not None else {},
        "config": invoke.get("config") or {},
        "context": invoke.get("context") or {},
        "recursion_limit": invoke.get("recursion_limit") or 25,
        "chat_name": invoke.get("chat_name") or trace.name or "kagraph_studio_playground",
        "system_instructions": invoke.get("system_instructions"),
        "model_id": first_generation.model if first_generation else None,
    }


def _trace_playground_schema(trace) -> dict[str, Any]:
    metadata_schema = (trace.metadata_json or {}).get("schema")
    invoke = (trace.metadata_json or {}).get("invoke") or {}
    schema = {
        "input": list((metadata_schema or {}).get("input") or []),
        "context": list((metadata_schema or {}).get("context") or []),
        "config": list((metadata_schema or {}).get("config") or []),
    }
    if trace.agent_binary:
        try:
            import cloudpickle
            agent = cloudpickle.loads(trace.agent_binary)
            builder = getattr(agent, "builder", None)
            schema["input"] = schema["input"] or _schema_fields(getattr(builder, "input_schema", None))
            schema["context"] = schema["context"] or _schema_fields(getattr(builder, "context_schema", None))
        except Exception:
            pass
    observed_input = invoke.get("input") or trace.input or {}
    if isinstance(observed_input, dict) and observed_input:
        if schema["input"]:
            observed_types = {key: _value_type_name(value) for key, value in observed_input.items()}
            schema["input"] = [
                {
                    **field,
                    "type": observed_types.get(field["name"], field.get("type") or "any")
                    if field.get("type") in (None, "", "any")
                    else field.get("type"),
                }
                for field in schema["input"]
            ]
        else:
            schema["input"] = [{"name": key, "type": _value_type_name(value), "required": True} for key, value in observed_input.items()]
    elif not schema["input"] and isinstance(observed_input, dict):
        schema["input"] = []
    observed_context = invoke.get("context") or {}
    if isinstance(observed_context, dict) and observed_context:
        existing = {field["name"]: field for field in schema["context"]}
        for key, value in observed_context.items():
            existing[key] = {"name": key, "type": _value_type_name(value), "required": False}
        schema["context"] = list(existing.values())
    elif not schema["context"] and isinstance(observed_context, dict):
        schema["context"] = []
    return schema


def _is_trace_replayable(trace) -> bool:
    return bool(trace.agent_binary or (trace.metadata_json or {}).get("agent_factory"))


def _value_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "any"


def _schema_fields(schema_obj: Any) -> list[dict[str, Any]]:
    if not schema_obj or schema_obj is dict:
        return []
    try:
        hints = get_type_hints(schema_obj, include_extras=True)
    except Exception:
        hints = getattr(schema_obj, "__annotations__", {}) or {}
    if not hints:
        return []
    required_keys = set(getattr(schema_obj, "__required_keys__", set()))
    total = bool(getattr(schema_obj, "__total__", True))
    fields = []
    for name, hint in hints.items():
        fields.append(
            {
                "name": str(name),
                "type": _schema_type_name(hint),
                "required": str(name) in required_keys if required_keys else total,
            }
        )
    return fields


def _schema_type_name(hint: Any) -> str:
    origin = get_origin(hint)
    if origin is not None and str(origin).endswith("Annotated"):
        args = get_args(hint)
        return _schema_type_name(args[0]) if args else "any"
    if origin is not None:
        name = getattr(origin, "__name__", str(origin))
        args = get_args(hint)
        if args:
            return f"{name}[{', '.join(_schema_type_name(arg) for arg in args[:2])}]"
        return name
    return getattr(hint, "__name__", str(hint).replace("typing.", ""))


class _InMemoryReplayEventCollector:
    def __init__(self) -> None:
        self.trace_id = f"playground-{uuid.uuid4()}"
        self.events: list[dict[str, Any]] = []
        self.checkpoints: list[dict[str, Any]] = []
        self._sequence = 0
        self._graph_run_id: str | None = None
        self._current_step: int | None = None
        self._current_step_id: str | None = None
        self._step_ids: dict[int, str] = {}
        self._span_stack: list[dict[str, Any]] = []
        self._last_node_run_by_name: dict[str, str] = {}
        self._graph_stack: list[dict[str, Any]] = []

    def _emit_event(
        self,
        event: str,
        name: str | None = None,
        *,
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        node: str | None = None,
        checkpoint_id: str | None = None,
        parent_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        event_metadata = dict(metadata or {})
        if self._current_step is not None:
            event_metadata.setdefault("step", self._current_step)
            if self._current_step_id:
                event_metadata.setdefault("step_run_id", self._current_step_id)
        if self._span_stack:
            event_metadata.setdefault("parent_run_id", self._span_stack[-1]["id"])
        elif self._current_step_id:
            event_metadata.setdefault("parent_run_id", self._current_step_id)
        elif self._graph_run_id:
            event_metadata.setdefault("parent_run_id", self._graph_run_id)

        self._sequence += 1
        item = {
            "id": str(uuid.uuid4()),
            "trace_id": self.trace_id,
            "sequence": self._sequence,
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "name": name,
            "node": node,
            "checkpoint_id": checkpoint_id,
            "parent_ids": parent_ids if parent_ids is not None else self._default_parent_ids(),
            "data": _make_json_safe(data or {}),
            "metadata": _make_json_safe(event_metadata),
        }
        self.events.append(item)
        if checkpoint_id or event == "on_checkpoint":
            self.checkpoints.append(item)
        return item

    def _default_parent_ids(self) -> list[str]:
        if self._span_stack:
            return [self._span_stack[-1]["id"]]
        if self._current_step_id:
            return [self._current_step_id]
        if self._graph_run_id:
            return [self._graph_run_id]
        return []

    def _store_current_graph_context(self) -> None:
        if not self._graph_stack:
            return
        self._graph_stack[-1]["current_step"] = self._current_step
        self._graph_stack[-1]["current_step_id"] = self._current_step_id
        self._graph_stack[-1]["step_ids"] = dict(self._step_ids)
        self._graph_stack[-1]["last_node_run_by_name"] = dict(self._last_node_run_by_name)

    def _activate_graph_context(self, frame: dict[str, Any] | None) -> None:
        if frame is None:
            self._graph_run_id = None
            self._current_step = None
            self._current_step_id = None
            self._step_ids = {}
            self._last_node_run_by_name = {}
            return
        self._graph_run_id = frame["run_id"]
        self._current_step = frame.get("current_step")
        self._current_step_id = frame.get("current_step_id")
        self._step_ids = dict(frame.get("step_ids") or {})
        self._last_node_run_by_name = dict(frame.get("last_node_run_by_name") or {})

    def _push_graph_context(self, frame: dict[str, Any]) -> None:
        self._store_current_graph_context()
        self._graph_stack.append(frame)
        self._activate_graph_context(frame)

    def _pop_graph_context(self) -> dict[str, Any] | None:
        self._store_current_graph_context()
        frame = self._graph_stack.pop() if self._graph_stack else None
        self._activate_graph_context(self._graph_stack[-1] if self._graph_stack else None)
        return frame

    def kagraph_invoke_start(self, **payload: Any) -> None:
        graph_obj = payload.get("graph")
        graph_name = getattr(graph_obj, "name", None) or "KaGraph"
        parent_id = self._span_stack[-1]["id"] if self._span_stack else self._current_step_id or self._graph_run_id
        is_root = not self._graph_stack
        if not is_root:
            frame = {
                "run_id": str(uuid.uuid4()),
                "name": graph_name,
                "is_root": False,
                "parent_id": parent_id,
                "current_step": None,
                "current_step_id": None,
                "step_ids": {},
                "last_node_run_by_name": {},
            }
            self._push_graph_context(frame)
            self._emit_event(
                "on_subgraph_start",
                graph_name,
                data={"input": payload.get("input")},
                metadata={
                    "run_id": frame["run_id"],
                    "parent_run_id": parent_id,
                    "graph_depth": len(self._graph_stack) - 1,
                    "is_root": False,
                },
                parent_ids=[parent_id] if parent_id else [],
            )
            return

        self._sequence = 0
        self.events = []
        self.checkpoints = []
        self._span_stack = []
        self._graph_stack = []
        self._push_graph_context(
            {
                "run_id": str(uuid.uuid4()),
                "name": graph_name,
                "is_root": True,
                "parent_id": None,
                "current_step": None,
                "current_step_id": None,
                "step_ids": {},
                "last_node_run_by_name": {},
            }
        )
        self._emit_event(
            "on_graph_start",
            graph_name,
            data={"input": payload.get("input")},
            metadata={"run_id": self._graph_run_id, "graph_depth": 0, "is_root": True},
            parent_ids=[],
        )

    def kagraph_invoke_end(self, **payload: Any) -> None:
        frame = self._graph_stack[-1] if self._graph_stack else {"run_id": self._graph_run_id, "is_root": True, "parent_id": None}
        is_root = bool(frame.get("is_root", True))
        graph_run_id = frame.get("run_id") or self._graph_run_id
        parent_id = frame.get("parent_id")
        self._emit_event(
            "on_graph_end" if is_root else "on_subgraph_end",
            frame.get("name") or "KaGraph",
            data={"output": _make_json_safe(payload.get("output"))},
            metadata={
                "run_id": graph_run_id,
                "parent_run_id": graph_run_id if is_root else parent_id,
                "graph_depth": max(len(self._graph_stack) - 1, 0),
                "is_root": is_root,
            },
            parent_ids=[graph_run_id] if is_root and graph_run_id else [parent_id] if parent_id else [],
        )
        self._pop_graph_context()

    def kagraph_invoke_error(self, **payload: Any) -> None:
        frame = self._graph_stack[-1] if self._graph_stack else {"run_id": self._graph_run_id, "is_root": True, "parent_id": None}
        is_root = bool(frame.get("is_root", True))
        graph_run_id = frame.get("run_id") or self._graph_run_id
        parent_id = frame.get("parent_id")
        self._emit_event(
            "on_graph_error" if is_root else "on_subgraph_error",
            frame.get("name") or "KaGraph",
            data={"error": str(payload.get("error"))},
            metadata={
                "run_id": graph_run_id,
                "parent_run_id": graph_run_id if is_root else parent_id,
                "graph_depth": max(len(self._graph_stack) - 1, 0),
                "is_root": is_root,
            },
            parent_ids=[graph_run_id] if is_root and graph_run_id else [parent_id] if parent_id else [],
        )
        self._pop_graph_context()

    def kagraph_step_start(self, graph: str, step: int, next: list, state: dict, **_: Any) -> None:
        step_id = str(uuid.uuid4())
        self._current_step = step
        self._current_step_id = step_id
        self._step_ids[step] = step_id
        self._emit_event(
            "on_step_start",
            graph,
            data={"step": step, "next": next, "state": state},
            metadata={"step": step, "run_id": step_id, "parent_run_id": self._graph_run_id},
            parent_ids=[self._graph_run_id] if self._graph_run_id else [],
        )

    def kagraph_step_end(self, graph: str, step: int, next: list, state: dict, **_: Any) -> None:
        step_id = self._step_ids.get(step) or self._current_step_id
        self._emit_event(
            "on_step_end",
            graph,
            data={"step": step, "next": next, "state": state},
            metadata={"step": step, "run_id": step_id, "parent_run_id": self._graph_run_id},
            parent_ids=[step_id] if step_id else [],
        )
        if self._current_step == step:
            self._current_step = None
            self._current_step_id = None

    def kagraph_node_start(self, node: str, state: dict, **_: Any) -> None:
        span_id = str(uuid.uuid4())
        parent_id = self._current_step_id or self._graph_run_id
        self._span_stack.append({"id": span_id, "name": node, "type": "NODE", "step": self._current_step})
        self._emit_event(
            "on_node_start",
            node,
            data={"state": state},
            metadata={"step": self._current_step, "run_id": span_id, "parent_run_id": parent_id},
            node=node,
            parent_ids=[parent_id] if parent_id else [],
        )

    def kagraph_node_update(self, node: str, update: dict, state: dict, channel_versions: dict, step: int, **_: Any) -> None:
        node_run_id = self._span_stack[-1]["id"] if self._span_stack else self._last_node_run_by_name.get(node)
        self._emit_event(
            "on_node_update",
            node,
            data={"update": update, "state": state, "channel_versions": channel_versions},
            metadata={"step": step, "run_id": node_run_id, "parent_run_id": node_run_id},
            node=node,
            parent_ids=[node_run_id] if node_run_id else None,
        )

    def kagraph_node_end(self, node: str | None = None, state: dict | None = None, error: BaseException | None = None, **_: Any) -> None:
        span = self._span_stack.pop() if self._span_stack else None
        node_name = node or (span or {}).get("name")
        node_run_id = (span or {}).get("id")
        if node_name and node_run_id:
            self._last_node_run_by_name[node_name] = node_run_id
        self._emit_event(
            "on_node_end",
            node_name,
            data={"state": state or {}, "error": None if error is None else str(error)},
            metadata={"step": (span or {}).get("step", self._current_step), "run_id": node_run_id, "parent_run_id": node_run_id},
            node=node_name,
            parent_ids=[node_run_id] if node_run_id else None,
        )

    def kagraph_checkpoint(self, thread_id: str, checkpoint: dict, state: dict, next: list, interrupt: Any, metadata: dict, **_: Any) -> None:
        checkpoint_id = checkpoint.get("checkpoint_id")
        source = metadata.get("source")
        parent_id = None
        if source and source not in {"checkpoint", "interrupt", "interrupt_before", "interrupt_after"}:
            parent_id = self._last_node_run_by_name.get(source)
        parent_id = parent_id or self._current_step_id or self._graph_run_id
        self._emit_event(
            "on_checkpoint",
            metadata.get("source") or "checkpoint",
            data={
                "thread_id": thread_id,
                "checkpoint": checkpoint,
                "state": state,
                "next": next,
                "interrupt": interrupt,
                "channel_versions": checkpoint.get("channel_versions"),
                "versions_seen": checkpoint.get("versions_seen"),
                "pending_writes": checkpoint.get("pending_writes"),
            },
            metadata={**metadata, "parent_run_id": parent_id},
            checkpoint_id=checkpoint_id,
            parent_ids=[parent_id] if parent_id else [],
        )

    def kagraph_tool_start(self, invocation, **_: Any) -> None:
        span_id = str(uuid.uuid4())
        parent_id = self._span_stack[-1]["id"] if self._span_stack else None
        arguments = _sanitize_tool_payload(getattr(invocation, "arguments", None))
        self._span_stack.append({"id": span_id, "name": invocation.name, "type": "TOOL", "step": self._current_step})
        self._emit_event(
            "on_tool_start",
            invocation.name,
            data={"arguments": arguments, "call_id": getattr(invocation, "call_id", None)},
            metadata={"run_id": span_id, "parent_run_id": parent_id, "step": self._current_step},
            parent_ids=[parent_id] if parent_id else [],
        )

    def kagraph_tool_end(self, result, **_: Any) -> None:
        span = self._span_stack.pop() if self._span_stack else None
        output = getattr(result, "text", None) or getattr(result, "output", None) or str(result)
        error = getattr(result, "error", None)
        self._emit_event(
            "on_tool_end",
            (span or {}).get("name"),
            data={"output": output, "error": error},
            metadata={"run_id": (span or {}).get("id"), "parent_run_id": (span or {}).get("id"), "step": (span or {}).get("step", self._current_step)},
            parent_ids=[(span or {}).get("id")] if (span or {}).get("id") else None,
        )

    def new_message(self, chat, message) -> None:
        role = getattr(getattr(message, "sender", None), "role", None)
        if role == "assistant":
            self._emit_chat_model_end(chat, message)
        self._emit_event(
            "on_message",
            getattr(getattr(message, "sender", None), "name", role or "message"),
            data={"message": _message_payload(message), "messages": [_message_payload(item) for item in getattr(chat, "messages", [])]},
            metadata={"role": role},
        )

    def start_streaming(self, message) -> None:
        self._emit_event(
            "on_chat_model_start",
            getattr(getattr(message, "sender", None), "name", "assistant"),
            data={"message": _message_payload(message)},
        )

    def new_chunk(self, message, chunk) -> None:
        chunk_text = chunk if isinstance(chunk, str) else getattr(chunk, "content", "")
        self._emit_event(
            "on_chat_model_stream",
            getattr(getattr(message, "sender", None), "name", "assistant"),
            data={"message": _message_payload(message), "chunk": chunk, "content": chunk_text},
        )

    def new_tool_call(self, message, chunk) -> None:
        self._emit_event(
            "on_tool_stream",
            getattr(getattr(message, "sender", None), "name", "assistant"),
            data={"message": _message_payload(message), "chunk": chunk},
        )

    def _emit_chat_model_end(self, chat, message) -> None:
        self._emit_event(
            "on_chat_model_end",
            getattr(getattr(message, "sender", None), "name", "assistant"),
            data={
                "message": _message_payload(message),
                "output": getattr(message, "payload", str(message)),
                "usage": _usage_dict(getattr(message, "usage", None)),
            },
            metadata={"usage": _usage_dict(getattr(message, "usage", None))},
        )


def _run_replayable_agent(
    trace,
    db: Session,
    *,
    input_value: Any,
    config: dict[str, Any] | None,
    context: dict[str, Any] | None,
    recursion_limit: int | None,
    chat_name: str | None,
    system_instructions: str | None,
    model_id: str | None,
):
    if not trace.agent_binary:
        if not (trace.metadata_json or {}).get("agent_factory"):
            raise HTTPException(status_code=400, detail="Trace is not replayable: no serialized agent or agent factory was captured.")
    try:
        agent = _load_replay_agent(trace, model_id)
        if model_id:
            _override_models(agent, model_id)
        defaults = _trace_playground_defaults(trace, db)
        run_input = defaults["input"] if input_value is None else input_value
        run_config = _studio_config(config if config is not None else defaults["config"])
        run_context = context if context is not None else defaults["context"]
        run_recursion_limit = recursion_limit or defaults["recursion_limit"]
        run_chat_name = chat_name or defaults["chat_name"]
        run_system_instructions = system_instructions if system_instructions is not None else defaults.get("system_instructions")
        if not hasattr(agent, "invoke"):
            raise HTTPException(status_code=400, detail="Trace is not replayable: captured object has no invoke().")
        collector = _InMemoryReplayEventCollector()
        kbench_events.manager.bind(collector)
        try:
            result = agent.invoke(
                run_input,
                config=run_config,
                context=run_context,
                recursion_limit=run_recursion_limit,
                chat_name=run_chat_name,
                system_instructions=run_system_instructions,
            )
        finally:
            kbench_events.manager.unbind(collector)
        return {
            "output": _make_json_safe({key: value for key, value in result.items() if key != "chat"}),
            "messages": _messages_from_result(result),
            "events": collector.events,
            "checkpoints": collector.checkpoints,
            "input": _make_json_safe(run_input),
            "config": _make_json_safe(run_config),
            "context": _make_json_safe(run_context),
            "recursion_limit": run_recursion_limit,
            "chat_name": run_chat_name,
            "model_id": model_id,
        }
    except HTTPException:
        raise
    except Exception as error:
        import traceback
        raise HTTPException(status_code=500, detail=str(error) + "\n" + traceback.format_exc())


def _load_replay_agent(trace, model_id: str | None):
    _prepare_replay_python_paths(trace)
    if trace.agent_binary:
        import cloudpickle
        return cloudpickle.loads(trace.agent_binary)
    factory = (trace.metadata_json or {}).get("agent_factory") or {}
    if isinstance(factory, str):
        module_path, function_name = factory.rsplit(":", 1)
    else:
        module_path = factory.get("module_path") or factory.get("module")
        function_name = factory.get("function")
    if not module_path or not function_name:
        raise HTTPException(status_code=400, detail="Trace is not replayable: agent factory metadata is incomplete.")
    import importlib
    import importlib.util
    import sys
    from pathlib import Path

    if str(module_path).endswith(".py") or Path(str(module_path)).exists():
        path = Path(str(module_path)).resolve()
        if str(path.parent) not in sys.path:
            sys.path.insert(0, str(path.parent))
        module_name = f"_kagraph_studio_agent_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise HTTPException(status_code=400, detail=f"Cannot load agent factory module from {path}.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(str(module_path))
    fn = getattr(module, str(function_name), None)
    if fn is None:
        raise HTTPException(status_code=400, detail=f"Agent factory function {function_name!r} was not found.")
    try:
        return fn(model_id=model_id)
    except TypeError:
        return fn()


def _prepare_replay_python_paths(trace) -> None:
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    candidate_paths = [
        root,
        root / "src",
        root / "tutorials",
        root / "reference" / "kaggle-benchmarks" / "src",
    ]
    runtime = (trace.metadata_json or {}).get("python") or {}
    cwd = runtime.get("cwd")
    if cwd:
        cwd_path = Path(cwd)
        candidate_paths.extend([cwd_path, cwd_path / "src", cwd_path / "tutorials"])
    candidate_paths.extend(Path(path) for path in runtime.get("sys_path") or [] if path)
    for path in reversed(candidate_paths):
        try:
            resolved = str(Path(path).resolve())
        except Exception:
            continue
        if resolved not in sys.path:
            sys.path.insert(0, resolved)


def _studio_config(config: dict[str, Any] | None) -> dict[str, Any]:
    run_config = _make_json_safe(config or {})
    if not isinstance(run_config, dict):
        run_config = {}
    configurable = dict(run_config.get("configurable") or {})
    configurable.pop("checkpoint_id", None)
    configurable["thread_id"] = f"studio-playground-{uuid.uuid4()}"
    run_config["configurable"] = configurable
    return run_config


def _messages_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    chat_obj = result.get("chat")
    if chat_obj is None:
        return []
    messages = []
    for msg in getattr(chat_obj, "messages", []):
        messages.append(_message_payload(msg))
    return messages


def _message_payload(msg: Any) -> dict[str, Any]:
    sender = getattr(msg, "sender", None)
    meta = _sanitize_tool_payload(getattr(msg, "_meta", {}) or {})
    tool_calls = meta.get("tool_calls") or []
    metadata = {key: value for key, value in meta.items() if key != "tool_calls"}
    raw_content = getattr(msg, "content", None)
    if _looks_like_tool_result(raw_content):
        content = getattr(raw_content, "text", None) or getattr(raw_content, "output", None) or getattr(raw_content, "error", None)
    elif _looks_like_media_content(raw_content):
        content = _media_content_payload(raw_content)
    else:
        content = getattr(msg, "payload", None)
    if content is None:
        content = getattr(msg, "text", None)
    if content is None:
        content = raw_content if raw_content is not None else ""
    return {
        "role": getattr(sender, "role", "assistant"),
        "sender_name": getattr(sender, "name", "assistant"),
        "sender_id": getattr(sender, "id", None),
        "content": _make_json_safe(content) if content is not None else "",
        "tool_calls": _make_json_safe(tool_calls),
        "metadata": _make_json_safe(metadata),
    }


def _usage_dict(usage: Any) -> dict[str, int | None]:
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "input_tokens_cost_nanodollars": getattr(usage, "input_tokens_cost_nanodollars", None),
        "output_tokens_cost_nanodollars": getattr(usage, "output_tokens_cost_nanodollars", None),
        "total_cost_nanodollars": getattr(usage, "total_cost_nanodollars", None),
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _override_models(obj, new_model_id, seen=None):
    if seen is None:
        seen = set()
    try:
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        if (
            hasattr(obj, "model")
            and isinstance(getattr(obj, "model", None), str)
            and hasattr(obj, "client")
        ):
            obj.model = new_model_id
            obj.name = new_model_id
            return
        if isinstance(obj, dict):
            for value in obj.values():
                _override_models(value, new_model_id, seen)
        elif isinstance(obj, (list, tuple)):
            for value in obj:
                _override_models(value, new_model_id, seen)
        if callable(obj) and hasattr(obj, "__closure__") and obj.__closure__:
            for cell in obj.__closure__:
                try:
                    _override_models(cell.cell_contents, new_model_id, seen)
                except ValueError:
                    pass
        if hasattr(obj, "__dict__"):
            for value in vars(obj).values():
                _override_models(value, new_model_id, seen)
    except Exception:
        return


def _make_json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        if isinstance(value, str):
            return _sanitize_tool_json_string(value)
        return value
    if isinstance(value, type):
        return _class_payload(value)
    if isinstance(value, dict):
        return {
            str(key): _make_json_safe(item)
            for key, item in value.items()
            if key != "chat" and str(key) != "signature"
        }
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    if _looks_like_tool_result(value):
        return {
            "name": getattr(value, "name", None),
            "arguments": _make_json_safe(_sanitize_tool_payload(getattr(value, "arguments", None))),
            "call_id": getattr(value, "call_id", None),
            "output": _make_json_safe(getattr(value, "output", None)),
            "error": getattr(value, "error", None),
        }
    if _looks_like_tool_invocation(value):
        return {
            "name": getattr(value, "name", None),
            "arguments": _make_json_safe(_sanitize_tool_payload(getattr(value, "arguments", None))),
            "call_id": getattr(value, "call_id", None),
        }
    if _looks_like_media_content(value):
        return _media_content_payload(value)
    if hasattr(value, "to_dict"):
        return _make_json_safe(value.to_dict())
    if hasattr(value, "model_dump"):
        return _make_json_safe(value.model_dump())
    if hasattr(value, "__dict__"):
        return _make_json_safe(vars(value))
    return str(value)


def _class_payload(cls: type[Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "class",
        "module": getattr(cls, "__module__", None),
        "name": getattr(cls, "__qualname__", getattr(cls, "__name__", str(cls))),
    }
    if hasattr(cls, "model_json_schema"):
        try:
            schema = cls.model_json_schema()
            payload["schema"] = {
                "title": schema.get("title"),
                "properties": sorted((schema.get("properties") or {}).keys()),
                "required": schema.get("required", []),
            }
        except Exception:
            pass
    return payload


def _looks_like_media_content(value: Any) -> bool:
    return hasattr(value, "url") and hasattr(value, "mime_type")


def _media_content_payload(value: Any) -> list[dict[str, Any]]:
    url = getattr(value, "url", "")
    mime_type = getattr(value, "mime_type", "") or ""
    if mime_type.startswith("audio/"):
        return [{"type": "audio_url", "audio_url": {"url": url}, "mime_type": mime_type}]
    if mime_type.startswith("video/"):
        return [{"type": "video_url", "video_url": {"url": url}, "mime_type": mime_type}]
    return [{"type": "image_url", "image_url": {"url": url}, "mime_type": mime_type}]


def _sanitize_tool_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_tool_payload(item)
            for key, item in value.items()
            if str(key) != "signature"
        }
    if isinstance(value, list):
        return [_sanitize_tool_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_tool_payload(item) for item in value)
    if isinstance(value, str):
        return _sanitize_tool_json_string(value)
    return value


def _sanitize_tool_json_string(value: str) -> str:
    try:
        parsed = json.loads(value)
    except Exception:
        return value
    if not isinstance(parsed, (dict, list)):
        return value
    sanitized = _sanitize_tool_payload(parsed)
    if sanitized == parsed:
        return value
    return json.dumps(sanitized, separators=(",", ":"))


def _looks_like_tool_invocation(value: Any) -> bool:
    return all(hasattr(value, field) for field in ("name", "arguments")) and hasattr(value, "call_id")


def _looks_like_tool_result(value: Any) -> bool:
    return _looks_like_tool_invocation(value) and (hasattr(value, "output") or hasattr(value, "error"))


# --- Playground ---

@app.get("/api/models")
def get_models():
    llms_available = os.environ.get("LLMS_AVAILABLE", "")
    if llms_available:
        return [m.strip() for m in llms_available.split(",")]
    return ["google/gemini-2.5-flash", "openai/gpt-4o"]

class PlaygroundRunRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]

@app.post("/api/playground/run")
def run_playground(req: PlaygroundRunRequest):
    try:
        import kaggle_benchmarks as kbench
        if req.model not in kbench.llms:
            return {"status": "error", "error": f"Model '{req.model}' not found in registry."}

        llm = kbench.llms[req.model]

        system_msg = next((m["content"] for m in req.messages if m["role"].lower() == "system"), None)
        user_msgs = [m for m in req.messages if m["role"].lower() != "system"]

        prompt_lines = []
        if system_msg:
            prompt_lines.append(f"System: {system_msg}")

        for msg in user_msgs:
            prompt_lines.append(f"{msg['role'].capitalize()}: {msg['content']}")

        final_prompt = "\n\n".join(prompt_lines)

        # Invoke via kbench
        response = llm.prompt(final_prompt)

        return {
            "status": "success",
            "output": response
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "error": str(e)}
