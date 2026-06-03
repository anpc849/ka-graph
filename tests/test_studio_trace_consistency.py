from __future__ import annotations

import os
import sys
import uuid
import importlib
from pathlib import Path

import pytest
import requests


API_BASE = os.getenv("KATRACE_API_URL", "http://127.0.0.1:8000")


def _require_running_studio():
    try:
        api = requests.get(f"{API_BASE}/api/dashboard", timeout=2)
    except requests.RequestException as error:
        pytest.skip(f"KaGraph Studio is not running: {error}")
    if api.status_code != 200:
        pytest.skip(f"KaGraph Studio API returned {api.status_code}")


def _load_backend_models():
    backend_dir = Path(__file__).resolve().parents[1] / "webapp" / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    for name in ["models", "database"]:
        sys.modules.pop(name, None)
    database = importlib.import_module("database")
    models = importlib.import_module("models")

    return database, models


def test_studio_database_logs_and_api_show_consistent_messages_and_llm_calls():
    _require_running_studio()

    trace_id = f"trace-consistency-{uuid.uuid4()}"
    llm_input_messages = [
        {"role": "system", "sender_name": "system", "content": "system context"},
        {"role": "user", "sender_name": "user", "content": "hello model"},
    ]
    displayed_messages = [
        *llm_input_messages,
        {"role": "assistant", "sender_name": "assistant", "content": "assistant reply"},
    ]

    try:
        created = requests.post(
            f"{API_BASE}/api/trace",
            json={"id": trace_id, "name": "consistency", "metadata_json": {}},
            timeout=5,
        )
        assert created.status_code == 200
        generation_id = f"gen-{uuid.uuid4()}"
        generation = requests.post(
            f"{API_BASE}/api/generation",
            json={
                "id": generation_id,
                "trace_id": trace_id,
                "name": "assistant",
                "model": "test-model",
                "input": llm_input_messages,
                "metadata_json": {"usage": {"input_tokens": 3, "output_tokens": 2}},
                "usage_input_tokens": 3,
                "usage_output_tokens": 2,
            },
            timeout=5,
        )
        assert generation.status_code == 200
        generation_update = requests.put(
            f"{API_BASE}/api/generation/{generation_id}",
            json={"output": "assistant reply"},
            timeout=5,
        )
        assert generation_update.status_code == 200
        events = requests.post(
            f"{API_BASE}/api/events/batch",
            json={
                "events": [
                    {
                        "trace_id": trace_id,
                        "event": "on_graph_start",
                        "name": "consistency",
                        "data": {"input": "hello model"},
                        "metadata_json": {"run_id": "graph-run"},
                    },
                    {
                        "trace_id": trace_id,
                        "event": "on_chat_model_end",
                        "name": "assistant",
                        "parent_ids": ["graph-run"],
                        "data": {
                            "message": displayed_messages[-1],
                            "output": "assistant reply",
                            "usage": {"input_tokens": 3, "output_tokens": 2},
                        },
                        "metadata_json": {"parent_run_id": "graph-run"},
                    },
                    *[
                        {
                            "trace_id": trace_id,
                            "event": "on_message",
                            "name": message["sender_name"],
                            "parent_ids": ["graph-run"],
                            "data": {"message": message, "messages": displayed_messages[: index + 1]},
                            "metadata_json": {"role": message["role"], "parent_run_id": "graph-run"},
                        }
                        for index, message in enumerate(displayed_messages)
                    ],
                    {
                        "trace_id": trace_id,
                        "event": "on_graph_end",
                        "name": "consistency",
                        "data": {"output": {"answer": "assistant reply"}},
                        "metadata_json": {"run_id": "graph-run"},
                    },
                ]
            },
            timeout=5,
        )
        assert events.status_code == 200

        trace_detail = requests.get(f"{API_BASE}/api/traces/{trace_id}", timeout=5).json()
        trace_events = requests.get(f"{API_BASE}/api/traces/{trace_id}/events", timeout=5).json()
        api_generations = trace_detail["generations"]
        log_messages = [event["data"]["message"] for event in trace_events if event["event"] == "on_message"]
        log_llm_calls = [event for event in trace_events if event["event"] == "on_chat_model_end"]

        assert len(api_generations) == len(log_llm_calls) == 1
        assert api_generations[0]["input"] == llm_input_messages
        assert log_messages == displayed_messages

        database, models = _load_backend_models()
        db = database.SessionLocal()
        try:
            db_events = (
                db.query(models.TraceEvent)
                .filter(models.TraceEvent.trace_id == trace_id)
                .order_by(models.TraceEvent.sequence.asc())
                .all()
            )
            db_generations = (
                db.query(models.Generation)
                .filter(models.Generation.trace_id == trace_id)
                .order_by(models.Generation.start_time.asc())
                .all()
            )
            db_messages = [event.data["message"] for event in db_events if event.event == "on_message"]
            db_llm_calls = [event for event in db_events if event.event == "on_chat_model_end"]
            assert len(db_generations) == len(api_generations) == len(db_llm_calls) == len(log_llm_calls) == 1
            assert db_generations[0].input == api_generations[0]["input"] == llm_input_messages
            assert db_messages == log_messages == displayed_messages
        finally:
            db.close()
    finally:
        try:
            requests.delete(f"{API_BASE}/api/traces/{trace_id}", timeout=5)
        except requests.RequestException:
            pass
