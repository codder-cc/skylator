"""
Staging session registry — tracks temporary directories created for
host→remote model transfers.

Lifecycle:
  1. workers_model_load() calls create_session() → staging_id + path
  2. Host downloads model files into the session path
  3. File-serving endpoint reads from session path (validates staging_id)
  4. workers_model_load() calls delete_session() after result arrives
"""
from __future__ import annotations
import shutil
import threading
from pathlib import Path

_lock     = threading.Lock()
_sessions: dict[str, Path] = {}


def create_session(cache_dir: Path) -> tuple[str, Path]:
    import uuid
    sid  = str(uuid.uuid4())
    path = cache_dir / "model_staging" / sid
    path.mkdir(parents=True, exist_ok=True)
    with _lock:
        _sessions[sid] = path
    return sid, path


def get_session_path(sid: str) -> Path | None:
    with _lock:
        return _sessions.get(sid)


def delete_session(sid: str) -> None:
    with _lock:
        path = _sessions.pop(sid, None)
    if path and path.exists():
        shutil.rmtree(path, ignore_errors=True)
