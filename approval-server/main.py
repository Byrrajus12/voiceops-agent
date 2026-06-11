import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="VoiceOps Approval Server")

_pending: dict[str, dict] = {}
_decisions: dict[str, dict] = {}
_incident_state: dict[str, str] = {}  # incident_id → last known agent activity text
_active_dt_sessions: dict[str, str] = {}  # problem_id → session_id (dedup guard)
_dt_trigger_log: list[dict] = []  # recent DT webhook triggers for dashboard display
_demo_phone_override: str = ""  # set at demo time to redirect calls to a tester's phone
_manual_mode: bool = False      # when True, DT webhooks are received but agent is NOT auto-started

_AGENT_URL = os.getenv("AGENT_URL", "https://voiceops-agent-224808509436.us-central1.run.app")
_TARGET_URL = os.getenv("TARGET_SERVICE_URL", "https://voiceops-target-224808509436.us-central1.run.app")
_APPROVAL_URL = os.getenv("APPROVAL_SERVER_URL", "https://voiceops-approval-224808509436.us-central1.run.app")


class ApprovalRequest(BaseModel):
    incident_id: str
    action: str
    summary: str
    risk_level: str = "high"
    confidence: str = "MEDIUM"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── API endpoints ──────────────────────────────────────────────

@app.post("/approval/request", status_code=201)
async def request_approval(req: ApprovalRequest):
    approval_id = str(uuid.uuid4())
    _pending[approval_id] = {
        "id": approval_id,
        "incident_id": req.incident_id,
        "action": req.action,
        "summary": req.summary,
        "risk_level": req.risk_level,
        "confidence": req.confidence,
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


@app.post("/incident/{incident_id}/approve")
async def approve_by_incident(incident_id: str, reason: Optional[str] = Query(default=None)):
    match = next((v for v in _pending.values() if v["incident_id"] == incident_id and v["status"] == "pending"), None)
    if not match:
        raise HTTPException(status_code=404, detail="No pending approval for this incident_id")
    return await approve(match["id"], reason)


@app.post("/incident/{incident_id}/reject")
async def reject_by_incident(incident_id: str, reason: Optional[str] = Query(default=None)):
    match = next((v for v in _pending.values() if v["incident_id"] == incident_id and v["status"] == "pending"), None)
    if not match:
        raise HTTPException(status_code=404, detail="No pending approval for this incident_id")
    return await reject(match["id"], reason)


@app.get("/incident/{incident_id}/status")
async def status_by_incident(incident_id: str):
    match = next((v for v in _pending.values() if v["incident_id"] == incident_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="No approval found for this incident_id")
    return {"status": match["status"]}


@app.get("/approvals/pending")
async def list_pending():
    return [v for v in _pending.values() if v["status"] == "pending"]


@app.get("/approvals")
async def list_all():
    return list(_pending.values())


@app.get("/health")
async def health():
    return {"status": "ok", "pending_count": sum(1 for v in _pending.values() if v["status"] == "pending")}


class DemoPhoneRequest(BaseModel):
    number: str


@app.post("/demo/phone", status_code=200)
async def set_demo_phone(req: DemoPhoneRequest):
    """Store a demo-time phone number override so test calls go to the tester's phone.

    POST {"number": "+1xxxxxxxxxx"} to redirect all outbound VAPI calls.
    POST {"number": ""} to clear the override and revert to VAPI_CALLER_NUMBER.
    """
    global _demo_phone_override
    _demo_phone_override = req.number
    return {"status": "ok", "number": _demo_phone_override}


@app.get("/demo/phone")
async def get_demo_phone():
    """Return the current demo phone override (empty string if not set)."""
    return {"number": _demo_phone_override}


class ManualModeRequest(BaseModel):
    enabled: bool


@app.post("/demo/manual", status_code=200)
async def set_manual_mode(req: ManualModeRequest):
    """Toggle manual mode.

    When enabled, Dynatrace problem webhooks are received and logged but the agent
    is NOT auto-started. Use this when you want to trigger the agent manually via the
    ADK dashboard without the auto-trigger racing you.

    POST {"enabled": true}  — disable auto-trigger
    POST {"enabled": false} — re-enable auto-trigger (default)
    """
    global _manual_mode
    _manual_mode = req.enabled
    return {"status": "ok", "manual_mode": _manual_mode}


@app.get("/demo/manual")
async def get_manual_mode():
    """Return the current manual mode state."""
    return {"manual_mode": _manual_mode}


class DemoStartRequest(BaseModel):
    phone: str
    manual: bool = False
    mode: str = "crash"


@app.post("/demo/start")
async def demo_start(req: DemoStartRequest):
    """One-call demo setup for testers and judges.

    Sets your phone number, breaks the target service, and configures the trigger mode.
    Dynatrace detects the failure in ~2-3 minutes, then either auto-starts the agent
    or waits for you to trigger it manually via the ADK dashboard.

    POST {"phone": "+1xxxxxxxxxx", "manual": false, "mode": "crash"}
      phone  — required. The number that will receive the VAPI call.
      manual — optional (default false). true = trigger via ADK dashboard; false = auto-trigger.
      mode   — optional (default "crash"). One of: crash | slow | auth_error | db_timeout | dependency
    """
    global _demo_phone_override, _manual_mode

    if not req.phone:
        raise HTTPException(status_code=400, detail="phone is required")

    _demo_phone_override = req.phone
    _manual_mode = req.manual

    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            await c.post(f"{_TARGET_URL}/demo/break", json={"mode": req.mode})
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Could not reach target service: {e}")

    if req.manual:
        next_steps = [
            "Wait 2-3 minutes for Dynatrace to detect the incident.",
            f"Go to {_AGENT_URL}/dev-ui/ and start a new session.",
            "Send: 'Check for active incidents and run the full incident response workflow.'",
            f"You will receive a VAPI call on {req.phone}. Say 'approve' or press 1.",
            f"Monitor the approval dashboard at {_APPROVAL_URL}/",
            f"When done: POST {_APPROVAL_URL}/demo/stop",
        ]
    else:
        next_steps = [
            "Wait 2-3 minutes for Dynatrace to detect the incident.",
            "The agent starts automatically when Dynatrace fires the webhook.",
            f"You will receive a VAPI call on {req.phone}. Say 'approve' or press 1.",
            f"Monitor the approval dashboard at {_APPROVAL_URL}/",
            f"When done: POST {_APPROVAL_URL}/demo/stop",
        ]

    return {
        "status": "demo_started",
        "phone": req.phone,
        "manual_mode": req.manual,
        "failure_mode": req.mode,
        "next_steps": next_steps,
    }


@app.post("/demo/stop")
async def demo_stop():
    """Reset everything after a demo run.

    Clears the phone override, disables manual mode, and restores the target service.
    """
    global _demo_phone_override, _manual_mode
    _demo_phone_override = ""
    _manual_mode = False

    async with httpx.AsyncClient(timeout=10.0) as c:
        try:
            await c.post(f"{_TARGET_URL}/demo/fix")
        except Exception:
            pass  # best-effort — don't block cleanup if target is unreachable

    return {"status": "reset", "phone_override": "", "manual_mode": False}


@app.post("/incident/{incident_id}/state")
async def update_incident_state(incident_id: str, request: Request):
    """ADK agent posts here to update what it's currently doing — used by VAPI tool."""
    body = await request.json()
    _incident_state[incident_id] = body.get("state", "")
    return {"status": "ok"}


def _extract_call_context(body: dict) -> tuple[str, str]:
    """Pull incident_id and approval_id from a VAPI function-call payload."""
    message = body.get("message") or body
    call = message.get("call") or body.get("call") or {}
    metadata = call.get("metadata") or {}
    incident_id = (
        metadata.get("incident_id")
        or (body.get("parameters") or {}).get("incident_id")
        or body.get("incident_id")
    )
    approval_id = (
        metadata.get("approval_id")
        or (body.get("parameters") or {}).get("approval_id")
        or body.get("approval_id")
    )
    return incident_id or "", approval_id or ""


def _extract_tool_call_id(body: dict) -> str:
    """Extract the toolCallId VAPI sends so we can echo it back in the response."""
    message = body.get("message") or body
    # VAPI puts it in message.toolCallList[0].id
    tool_call_list = message.get("toolCallList") or []
    if tool_call_list:
        return tool_call_list[0].get("id", "")
    # Fallback: message.functionCall.id (older format)
    fc = message.get("functionCall") or {}
    return fc.get("id", "")


def _vapi_result(tool_call_id: str, result: str) -> dict:
    """Build the VAPI tool response format.

    VAPI requires {"results": [{"toolCallId": "...", "result": "..."}]}.
    Returning {"result": "..."} (singular) causes 'No result returned' in the LLM.
    """
    return {"results": [{"toolCallId": tool_call_id, "result": result}]}


@app.post("/vapi/tool/get_incident_status")
async def vapi_tool_get_incident_status(request: Request):
    """VAPI function tool — operator asked what's happening mid-call."""
    body = await request.json()
    tool_call_id = _extract_tool_call_id(body)
    incident_id, _ = _extract_call_context(body)

    if not incident_id:
        return _vapi_result(tool_call_id, "No incident ID in call context.")

    # Only match a PENDING approval — ignore stale approved/rejected entries from prior sessions
    pending_match = next((v for v in _pending.values() if v["incident_id"] == incident_id and v["status"] == "pending"), None)
    state_text = _incident_state.get(incident_id, "")

    if pending_match:
        result = f"Waiting on your go-ahead for the rollback. {state_text}".strip()
    else:
        result = state_text or "Incident response in progress — I'll give you an update shortly."

    return _vapi_result(tool_call_id, result)


@app.post("/vapi/tool/approve_rollback")
async def vapi_tool_approve_rollback(request: Request):
    """VAPI function tool — operator approved the rollback mid-call."""
    body = await request.json()
    tool_call_id = _extract_tool_call_id(body)
    incident_id, approval_id = _extract_call_context(body)

    try:
        if approval_id:
            await approve(approval_id, reason="voice_approved")
        elif incident_id:
            await approve_by_incident(incident_id, reason="voice_approved")
        else:
            return _vapi_result(tool_call_id, "Couldn't find the approval request — check the dashboard to approve manually.")
    except HTTPException as e:
        if "Already decided" in str(e.detail):
            return _vapi_result(tool_call_id, "Already approved — rollback is in progress. Stay on the line.")
        return _vapi_result(tool_call_id, f"Approval error: {e.detail}")

    return _vapi_result(tool_call_id, "Rollback approved. Stay on the line — I'll give you live updates as the rollback runs. Do NOT end the call.")


@app.post("/vapi/tool/reject_rollback")
async def vapi_tool_reject_rollback(request: Request):
    """VAPI function tool — operator rejected the rollback mid-call."""
    body = await request.json()
    tool_call_id = _extract_tool_call_id(body)
    incident_id, approval_id = _extract_call_context(body)

    try:
        if approval_id:
            await reject(approval_id, reason="voice_rejected")
        elif incident_id:
            await reject_by_incident(incident_id, reason="voice_rejected")
        else:
            return _vapi_result(tool_call_id, "Couldn't find the approval request.")
    except HTTPException as e:
        if "Already decided" in str(e.detail):
            return _vapi_result(tool_call_id, "Already decided — standing down.")
        return _vapi_result(tool_call_id, f"Error: {e.detail}")

    return _vapi_result(tool_call_id, "Understood — standing down on the rollback.")


async def _trigger_agent(session_id: str, problem_id: str) -> None:
    """Background task: create ADK session then stream the run to completion.

    Drains the SSE stream line-by-line so the agent keeps running —
    ADK aborts if the client disconnects before the run finishes.
    """
    app_name = "agent"  # ADK names the app after the directory, not root_agent.name
    user_id = "dynatrace-webhook"
    prompt = "Check for active incidents and run the full incident response workflow."

    try:
        # 60s timeout — Cloud Run cold starts can take 15-30s
        async with httpx.AsyncClient(timeout=60.0) as c:
            await c.post(
                f"{_AGENT_URL}/apps/{app_name}/users/{user_id}/sessions/{session_id}",
                json={},
            )

        # No read timeout — agent takes 10-15 min; 1200s gives plenty of headroom
        async with httpx.AsyncClient() as c:
            async with c.stream(
                "POST",
                f"{_AGENT_URL}/run",
                json={
                    "app_name": app_name,
                    "user_id": user_id,
                    "session_id": session_id,
                    "new_message": {"role": "user", "parts": [{"text": prompt}]},
                },
                timeout=httpx.Timeout(connect=10.0, read=1200.0, write=10.0, pool=5.0),
            ) as resp:
                async for _ in resp.aiter_lines():
                    pass  # drain stream — keeps agent alive until completion
    except Exception as e:
        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        print(f"[DT webhook] Agent error problem={problem_id} session={session_id}: {msg}")
    finally:
        _active_dt_sessions.pop(problem_id, None)
        for entry in _dt_trigger_log:
            if entry["session_id"] == session_id:
                entry["status"] = "done"
                entry["finished_at"] = _now()


@app.post("/webhook/dynatrace/problem")
async def dynatrace_problem_webhook(request: Request, background_tasks: BackgroundTasks):
    """Dynatrace Problem Notification webhook — auto-triggers the incident commander agent.

    Configure in Dynatrace: Settings → Alerting → Problem Notifications → Webhook.
    Set the URL to: {APPROVAL_SERVER_URL}/webhook/dynatrace/problem
    Use the default JSON payload (no custom template needed).
    """
    body = await request.json()

    state = body.get("state", "OPEN")
    if state != "OPEN":
        return {"status": "ignored", "reason": f"state={state}"}

    # DT webhook field names vary across versions — check common variants
    problem_id = (
        body.get("ProblemID")
        or body.get("problemId")
        or body.get("problem_id")
        or "unknown"
    )

    # Manual mode — log the webhook but don't auto-start the agent
    if _manual_mode:
        print(f"[DT webhook] manual_mode=True — received problem {problem_id}, not auto-starting agent")
        return {"status": "received", "manual_mode": True, "problem_id": problem_id,
                "note": "Manual mode enabled. Trigger the agent via the ADK dashboard."}

    # Dedup — if this problem already has an active session, don't spin up another
    if problem_id in _active_dt_sessions:
        existing = _active_dt_sessions[problem_id]
        return {"status": "already_running", "problem_id": problem_id, "session_id": existing}

    session_id = str(uuid.uuid4())
    _active_dt_sessions[problem_id] = session_id

    _dt_trigger_log.append({
        "problem_id": problem_id,
        "session_id": session_id,
        "triggered_at": _now(),
        "status": "running",
        "finished_at": None,
    })
    # Keep log bounded to last 10 triggers
    if len(_dt_trigger_log) > 10:
        _dt_trigger_log.pop(0)

    background_tasks.add_task(_trigger_agent, session_id, problem_id)

    print(f"[DT webhook] {problem_id} → agent session {session_id}")
    return {"status": "triggered", "problem_id": problem_id, "session_id": session_id}


@app.post("/webhook/vapi")
async def vapi_webhook(request: Request):
    """Receive VAPI call events/webhooks.

    VAPI wraps all events under a top-level "message" key. The call's metadata
    (where we store incident_id) lives at message.call.metadata. Transcript is
    at message.transcript for end-of-call-report events.
    """
    payload = await request.json()

    # VAPI wraps everything under "message"; fall back to root for non-standard senders
    message = payload.get("message") or payload
    msg_type = message.get("type", "unknown")

    # incident_id is stored in the call's metadata when place_voice_call is invoked
    call = message.get("call") or {}
    metadata = call.get("metadata") or message.get("metadata") or {}
    incident_id = metadata.get("incident_id") or payload.get("incident_id")

    # transcript comes from end-of-call-report; dtmf from status-update
    transcript = (
        message.get("transcript")
        or (message.get("artifact") or {}).get("transcript")
        or payload.get("transcript")
        or ""
    )
    dtmf = message.get("dtmf") or payload.get("dtmf") or payload.get("digits") or ""

    approval_id = metadata.get("approval_id") or call.get("approval_id")
    if not incident_id and not approval_id:
        return {"status": "ignored", "reason": "no incident_id or approval_id in payload", "type": msg_type}

    text = str(transcript).lower()
    is_approve = str(dtmf).strip() == "1" or any(w in text for w in ("approve", "yes", "confirm"))
    is_reject = str(dtmf).strip() == "2" or any(w in text for w in ("reject", "no", "deny"))

    if not is_approve and not is_reject:
        return {"status": "ignored", "type": msg_type, "note": "no approval keywords"}

    try:
        if approval_id:
            # Direct lookup — most reliable path
            if is_approve:
                await approve(approval_id, reason="voice_approved")
                return {"status": "approved", "approval_id": approval_id}
            else:
                await reject(approval_id, reason="voice_rejected")
                return {"status": "rejected", "approval_id": approval_id}
        else:
            # Fallback: find pending approval by incident_id
            if is_approve:
                await approve_by_incident(incident_id, reason="voice_approved")
                return {"status": "approved", "incident_id": incident_id}
            else:
                await reject_by_incident(incident_id, reason="voice_rejected")
                return {"status": "rejected", "incident_id": incident_id}
    except HTTPException as e:
        return {"status": "error", "detail": str(e.detail)}


# ── Browser UI ─────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    pending = [v for v in _pending.values() if v["status"] == "pending"]
    all_requests = sorted(_pending.values(), key=lambda x: x["created_at"], reverse=True)

    # Agent status banner — shown when a DT-triggered session is active
    agent_banner = ""
    running_sessions = [e for e in _dt_trigger_log if e["status"] == "running"]
    if running_sessions:
        latest = running_sessions[-1]
        agent_banner = f"""
        <div style="background:#1e3a5f;border:1px solid #3b82f6;border-radius:10px;padding:16px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;gap:12px">
          <div style="display:flex;align-items:center;gap:12px">
            <span style="font-size:22px">🤖</span>
            <div>
              <p style="font-weight:700;color:#93c5fd;margin:0">Agent Running — Auto-triggered by Dynatrace</p>
              <p style="color:#64748b;font-size:13px;margin:4px 0 0">Problem {latest["problem_id"]} · Started {latest["triggered_at"][11:19]}Z · Session {latest["session_id"][:8]}…</p>
            </div>
          </div>
          <a href="{_AGENT_URL}/dev-ui/" target="_blank" style="background:#1d4ed8;color:white;padding:8px 16px;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap">Open ADK Dashboard →</a>
        </div>"""

    cards = ""
    for r in all_requests:
        status = r["status"]
        confidence = r.get("confidence", "MEDIUM")
        conf_color = {"HIGH": "#22c55e", "MEDIUM": "#f59e0b", "LOW": "#ef4444"}.get(confidence, "#6b7280")
        status_color = {"pending": "#f59e0b", "approved": "#22c55e", "rejected": "#ef4444"}.get(status, "#6b7280")

        buttons = ""
        if status == "pending":
            aid = r["id"]
            buttons = f"""
            <div style="display:flex;gap:12px;margin-top:16px">
              <button onclick="decide('{aid}','approve')"
                style="flex:1;padding:12px;background:#22c55e;color:white;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer">
                ✅ Approve Rollback
              </button>
              <button onclick="decide('{aid}','reject')"
                style="flex:1;padding:12px;background:#ef4444;color:white;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer">
                ❌ Reject
              </button>
            </div>"""

        decision_info = ""
        if r["id"] in _decisions:
            d = _decisions[r["id"]]
            decision_info = f'<p style="margin:8px 0 0;color:#9ca3af;font-size:13px">Decided at {d["decided_at"][:19]}Z · {d.get("reason","no reason given")}</p>'

        cards += f"""
        <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:16px;border-left:4px solid {status_color}">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <span style="font-size:12px;font-weight:700;color:{conf_color};text-transform:uppercase;letter-spacing:1px">
                {confidence} CONFIDENCE
              </span>
              <h3 style="margin:4px 0;font-size:18px">{r["incident_id"]}</h3>
              <p style="margin:0;color:#94a3b8;font-size:14px">{r["action"]}</p>
            </div>
            <span style="background:{status_color}22;color:{status_color};padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600;white-space:nowrap">
              {status.upper()}
            </span>
          </div>
          <p style="margin:12px 0 0;color:#cbd5e1;font-size:14px;line-height:1.5">{r["summary"]}</p>
          <p style="margin:8px 0 0;color:#64748b;font-size:12px">Requested {r["created_at"][:19]}Z · Risk: {r["risk_level"].upper()}</p>
          {decision_info}
          {buttons}
        </div>"""

    if not cards:
        cards = '<div style="text-align:center;padding:60px;color:#64748b"><p style="font-size:48px">✅</p><p style="font-size:18px">No approval requests yet</p><p>The agent will post here when it needs human sign-off.</p></div>'

    pending_badge = f'<span style="background:#ef4444;color:white;border-radius:50%;padding:2px 8px;font-size:12px;margin-left:8px">{len(pending)}</span>' if pending else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>VoiceOps — Operator Console</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f172a; color: #e2e8f0; min-height: 100vh; }}
    button:hover {{ opacity: 0.85; transform: translateY(-1px); transition: all 0.1s; }}
  </style>
</head>
<body>
  <div style="max-width:720px;margin:0 auto;padding:32px 16px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:32px">
      <div>
        <h1 style="font-size:24px;font-weight:700">🎙️ VoiceOps</h1>
        <p style="color:#64748b;font-size:14px;margin-top:4px">Operator Approval Console</p>
      </div>
      <div style="text-align:right">
        <span style="font-size:13px;color:#64748b">Pending{pending_badge}</span>
        <p style="font-size:11px;color:#475569;margin-top:2px" id="clock"></p>
      </div>
    </div>
    {agent_banner}
    <div id="cards">{cards}</div>
  </div>
  <script>
    // Show live clock
    function tick() {{ document.getElementById('clock').textContent = new Date().toUTCString().slice(0,25); }}
    tick(); setInterval(tick, 1000);

    // Auto-refresh every 5s
    setInterval(() => location.reload(), 5000);

    async function decide(id, action) {{
      const reason = action === 'approve' ? 'operator-approved' : prompt('Rejection reason (optional):') || 'operator-rejected';
      const url = `/${{action}}/${{id}}?reason=${{encodeURIComponent(reason)}}`;
      await fetch(url, {{ method: 'POST' }});
      location.reload();
    }}
  </script>
</body>
</html>"""
