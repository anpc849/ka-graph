from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_STUDIO_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_PORT = 3000
DEFAULT_BACKEND_URL = f"http://{DEFAULT_STUDIO_HOST}:{DEFAULT_BACKEND_PORT}"
DEFAULT_DB_PATH = Path(
    os.getenv("KATRACE_DB_PATH", str(Path.cwd() / "traces.db"))
).expanduser().resolve()
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"

STUDIO_RUNTIME_PATH = Path(
    os.getenv(
        "KAGRAPH_STUDIO_RUNTIME_FILE",
        str(Path(tempfile.gettempdir()) / "kagraph_studio.json"),
    )
)


def write_studio_runtime(**values: Any) -> None:
    payload = {
        "backend_url": DEFAULT_BACKEND_URL,
        "database_url": DEFAULT_DB_URL,
        "database_path": str(DEFAULT_DB_PATH),
        "host": DEFAULT_STUDIO_HOST,
        "backend_port": DEFAULT_BACKEND_PORT,
        "frontend_port": DEFAULT_FRONTEND_PORT,
        **values,
    }
    try:
        STUDIO_RUNTIME_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_studio_backend_url() -> str | None:
    try:
        payload = json.loads(STUDIO_RUNTIME_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    backend_url = payload.get("backend_url")
    return backend_url if isinstance(backend_url, str) and backend_url.strip() else None


def resolve_backend_url(backend_url: str | None = None) -> str:
    return (
        backend_url
        or os.getenv("KATRACE_BACKEND_URL")
        or os.getenv("KAGRAPH_STUDIO_BACKEND_URL")
        or read_studio_backend_url()
        or DEFAULT_BACKEND_URL
    )
