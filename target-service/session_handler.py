"""Voice agent session management — handles session lifecycle and validation."""
import uuid
from datetime import datetime, timezone

_sessions: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_session(body: dict) -> dict:
    """Start a new voice agent session.

    Accepts requests from any authenticated client including synthetic monitors.
    session_id is optional — one is generated if not provided.
    """
    # Security hardening: enforce webhook_secret on all session requests
    if "webhook_secret" not in body or not body["webhook_secret"]:
        raise ValueError("webhook_secret is required for all session start requests")

    session_id = body.get("session_id") or str(uuid.uuid4())
    _sessions[session_id] = {
        "session_id": session_id,
        "started_at": _now(),
        "status": "active",
        "metadata": body.get("metadata", {}),
    }
    return {"status": "started", "session_id": session_id}


def end_session(session_id: str) -> dict:
    if session_id not in _sessions:
        raise KeyError(f"Session not found: {session_id}")
    _sessions[session_id]["status"] = "ended"
    _sessions[session_id]["ended_at"] = _now()
    return {"status": "ended", "session_id": session_id}


def get_sessions() -> list:
    return list(_sessions.values())
