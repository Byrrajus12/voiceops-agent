import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Approval Server", description="Human-in-the-loop approval gateway for VoiceOps agent actions")

_pending: dict[str, dict] = {}
_decisions: dict[str, dict] = {}


class ApprovalRequest(BaseModel):
    incident_id: str
    action: str
    summary: str
    risk_level: str = "high"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.post("/approval/request", status_code=201)
async def request_approval(req: ApprovalRequest):
    approval_id = str(uuid.uuid4())
    _pending[approval_id] = {
        "id": approval_id,
        "incident_id": req.incident_id,
        "action": req.action,
        "summary": req.summary,
        "risk_level": req.risk_level,
        "created_at": _now(),
        "status": "pending",
    }
    return {"approval_id": approval_id, "status": "pending", "created_at": _pending[approval_id]["created_at"]}


@app.post("/approve/{approval_id}")
async def approve(approval_id: str, reason: Optional[str] = Query(default=None)):
    if approval_id not in _pending:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if _pending[approval_id]["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Already decided: {_pending[approval_id]['status']}")
    _pending[approval_id]["status"] = "approved"
    _decisions[approval_id] = {"approved": True, "reason": reason, "decided_at": _now()}
    return {"status": "approved", "approval_id": approval_id, "reason": reason}


@app.post("/reject/{approval_id}")
async def reject(approval_id: str, reason: Optional[str] = Query(default=None)):
    if approval_id not in _pending:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if _pending[approval_id]["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Already decided: {_pending[approval_id]['status']}")
    _pending[approval_id]["status"] = "rejected"
    _decisions[approval_id] = {"approved": False, "reason": reason, "decided_at": _now()}
    return {"status": "rejected", "approval_id": approval_id, "reason": reason}


@app.get("/approval/{approval_id}/status")
async def get_status(approval_id: str):
    if approval_id not in _pending:
        raise HTTPException(status_code=404, detail="Approval request not found")
    result = dict(_pending[approval_id])
    if approval_id in _decisions:
        result["decision"] = _decisions[approval_id]
    return result


@app.get("/approvals/pending")
async def list_pending():
    return [v for v in _pending.values() if v["status"] == "pending"]


@app.get("/approvals")
async def list_all():
    return list(_pending.values())


@app.get("/health")
async def health():
    return {"status": "ok", "pending_count": sum(1 for v in _pending.values() if v["status"] == "pending")}
