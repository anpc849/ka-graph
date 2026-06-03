from sqlalchemy import Column, String, Integer, DateTime, JSON, ForeignKey, Float, LargeBinary
from sqlalchemy.orm import relationship
import datetime
import uuid
from database import Base

def generate_uuid():
    return str(uuid.uuid4())

class Trace(Base):
    __tablename__ = "traces"

    id = Column(String, primary_key=True, index=True, default=generate_uuid)
    name = Column(String, index=True)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    session_id = Column(String, index=True, nullable=True)
    user_id = Column(String, index=True, nullable=True)
    start_time = Column(DateTime, default=datetime.datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    status = Column(String, default="SUCCESS") # SUCCESS, ERROR
    error = Column(String, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    agent_binary = Column(LargeBinary, nullable=True)

    spans = relationship("Span", back_populates="trace")
    generations = relationship("Generation", back_populates="trace")
    events = relationship("TraceEvent", back_populates="trace", order_by="TraceEvent.sequence")
    replay_sessions = relationship("ReplaySession", back_populates="trace")

class Span(Base):
    __tablename__ = "spans"

    id = Column(String, primary_key=True, index=True, default=generate_uuid)
    trace_id = Column(String, ForeignKey("traces.id"))
    parent_id = Column(String, nullable=True) # ID of parent span if any
    name = Column(String, index=True)
    span_type = Column(String) # SPAN, CHAIN, TOOL, AGENT
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    start_time = Column(DateTime, default=datetime.datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    status = Column(String, default="SUCCESS")
    error = Column(String, nullable=True)
    metadata_json = Column(JSON, nullable=True)

    trace = relationship("Trace", back_populates="spans")

class Generation(Base):
    __tablename__ = "generations"

    id = Column(String, primary_key=True, index=True, default=generate_uuid)
    trace_id = Column(String, ForeignKey("traces.id"))
    parent_id = Column(String, nullable=True)
    name = Column(String)
    model = Column(String, nullable=True)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    usage_input_tokens = Column(Integer, nullable=True)
    usage_output_tokens = Column(Integer, nullable=True)
    cost_total = Column(Float, nullable=True)
    metadata_json = Column(JSON, nullable=True) # reasoning_traces etc
    start_time = Column(DateTime, default=datetime.datetime.utcnow)
    end_time = Column(DateTime, nullable=True)

    trace = relationship("Trace", back_populates="generations")


class TraceEvent(Base):
    __tablename__ = "trace_events"

    id = Column(String, primary_key=True, index=True, default=generate_uuid)
    trace_id = Column(String, ForeignKey("traces.id"), index=True)
    sequence = Column(Integer, index=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    event = Column(String, index=True)
    name = Column(String, index=True, nullable=True)
    node = Column(String, index=True, nullable=True)
    checkpoint_id = Column(String, index=True, nullable=True)
    parent_ids = Column(JSON, nullable=True)
    data = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)

    trace = relationship("Trace", back_populates="events")


class ReplaySession(Base):
    __tablename__ = "replay_sessions"

    id = Column(String, primary_key=True, index=True, default=generate_uuid)
    trace_id = Column(String, ForeignKey("traces.id"), index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String, default="READY")
    checkpoint_id = Column(String, nullable=True)
    model_id = Column(String, nullable=True)
    messages = Column(JSON, nullable=True)
    last_output = Column(JSON, nullable=True)
    error = Column(String, nullable=True)

    trace = relationship("Trace", back_populates="replay_sessions")
