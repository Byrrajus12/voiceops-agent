"""Tool functions for the Incident Commander agent."""
import os
import time
from datetime import datetime, timezone

import requests

DYNATRACE_BASE = f"https://{os.getenv('DYNATRACE_TENANT', 'wkf10640.apps.dynatrace.com')}"
DYNATRACE_TOKEN = os.getenv("DYNATRACE_PLATFORM_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_PAT", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Byrrajus12/voiceops-agent")
APPROVAL_SERVER = os.getenv("APPROVAL_SERVER_URL", "http://localhost:9000")


def check_dynatrace_incidents() -> dict:
    """Query Dynatrace for currently open problems/incidents.

    Returns a list of open incidents with severity, affected entities, and start time.
    Call this first to detect whether a production problem exists.
    """
    headers = {
        "Authorization": f"Api-Token {DYNATRACE_TOKEN}",
        "Accept": "application/json; charset=utf-8",
    }
    params = {
        "problemSelector": "status(OPEN)",
        "fields": "+evidenceDetails,+impactAnalysis",
        "pageSize": 10,
    }
    try:
        resp = requests.get(
            f"{DYNATRACE_BASE}/api/v2/problems",
            headers=headers,
            params=params,
            timeout=30,
        )
    except requests.RequestException as e:
        return {"error": f"Network error contacting Dynatrace: {e}"}

    if resp.status_code == 401:
        return {"error": "Dynatrace authentication failed — check DYNATRACE_PLATFORM_TOKEN"}
    if resp.status_code != 200:
        return {"error": f"Dynatrace API returned {resp.status_code}: {resp.text[:300]}"}

    data = resp.json()
    problems = data.get("problems", [])
    if not problems:
        return {"incidents": [], "count": 0, "message": "No open incidents — all clear"}

    formatted = []
    for p in problems[:5]:
        start_ms = p.get("startTime", 0)
        start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat() if start_ms else "unknown"
        formatted.append({
            "id": p.get("problemId"),
            "title": p.get("title"),
            "severity": p.get("severityLevel"),
            "impact": p.get("impactLevel"),
            "status": p.get("status"),
            "start_time": start_dt,
            "start_time_ms": start_ms,
            "affected_entities": [e.get("name") for e in p.get("affectedEntities", [])[:5]],
            "root_cause": p.get("evidenceDetails", {}).get("details", [{}])[0].get("displayName") if p.get("evidenceDetails") else None,
        })

    return {"incidents": formatted, "count": len(formatted)}


def create_dynatrace_test_event(service_name: str = "checkout-service", error_rate: float = 0.8) -> dict:
    """Ingest a custom availability event into Dynatrace to simulate an incident for demo purposes.

    Use this when there are no real incidents but you need to demonstrate the workflow.
    """
    headers = {
        "Authorization": f"Api-Token {DYNATRACE_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "eventType": "AVAILABILITY_EVENT",
        "title": f"High error rate detected on {service_name}",
        "entitySelector": f"type(SERVICE),entityName({service_name})",
        "properties": {
            "error_rate": str(error_rate),
            "triggered_by": "voiceops-demo",
            "description": f"{service_name} is returning {error_rate*100:.0f}% HTTP 500 errors",
        },
    }
    try:
        resp = requests.post(
            f"{DYNATRACE_BASE}/api/v2/events/ingest",
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        return {"error": f"Network error: {e}"}

    if resp.status_code in (200, 201):
        return {"status": "event_ingested", "response": resp.json()}
    return {"error": f"Dynatrace events API returned {resp.status_code}: {resp.text[:300]}"}


def get_recent_github_commits(limit: int = 10) -> dict:
    """Fetch the most recent commits from the monitored GitHub repository.

    Use this to correlate an incident start time with a specific commit that likely caused it.
    Returns commits sorted newest-first with sha, message, author, and timestamp.
    """
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    owner, repo = GITHUB_REPO.split("/", 1)
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits",
            headers=headers,
            params={"per_page": min(limit, 30)},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"error": f"Network error contacting GitHub: {e}"}

    if resp.status_code == 401:
        return {"error": "GitHub authentication failed — check GITHUB_PAT"}
    if resp.status_code != 200:
        return {"error": f"GitHub API returned {resp.status_code}: {resp.text[:300]}"}

    commits = []
    for c in resp.json():
        commits.append({
            "sha": c["sha"][:8],
            "full_sha": c["sha"],
            "message": c["commit"]["message"].split("\n")[0][:120],
            "author": c["commit"]["author"]["name"],
            "timestamp": c["commit"]["author"]["date"],
            "url": c["html_url"],
        })

    return {"commits": commits, "count": len(commits), "repo": GITHUB_REPO}


def generate_voice_briefing(briefing_text: str, output_path: str = "/tmp/incident_briefing.mp3") -> dict:
    """Generate an MP3 voice briefing using Google Cloud Text-to-Speech.

    Synthesizes the briefing_text to speech using a neural voice and saves to output_path.
    Returns the file path and size on success.
    """
    try:
        from google.cloud import texttospeech  # type: ignore

        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=briefing_text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name="en-US-Neural2-D",
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=0.93,
            pitch=0.0,
        )
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
        with open(output_path, "wb") as f:
            f.write(response.audio_content)
        return {
            "status": "success",
            "path": output_path,
            "bytes": len(response.audio_content),
            "text_length": len(briefing_text),
        }
    except ImportError:
        # Fallback: save the text if TTS library not available
        text_path = output_path.replace(".mp3", ".txt")
        with open(text_path, "w") as f:
            f.write(briefing_text)
        return {
            "status": "tts_unavailable",
            "message": "google-cloud-texttospeech not installed — saved text transcript instead",
            "path": text_path,
            "briefing_text": briefing_text,
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "briefing_text": briefing_text}


def request_human_approval(incident_id: str, action: str, summary: str, risk_level: str = "high") -> dict:
    """Submit a rollback approval request to the human approval server.

    Returns an approval_id. Share this ID with the operator so they can approve or reject
    via POST /approve/{approval_id} or POST /reject/{approval_id} on the approval server.
    """
    payload = {
        "incident_id": incident_id,
        "action": action,
        "summary": summary,
        "risk_level": risk_level,
    }
    try:
        resp = requests.post(f"{APPROVAL_SERVER}/approval/request", json=payload, timeout=30)
    except requests.RequestException as e:
        return {"error": f"Cannot reach approval server at {APPROVAL_SERVER}: {e}"}

    if resp.status_code not in (200, 201):
        return {"error": f"Approval server returned {resp.status_code}: {resp.text[:300]}"}
    return resp.json()


def poll_approval_status(approval_id: str, timeout_seconds: int = 300, poll_interval: int = 10) -> dict:
    """Poll the approval server until a human approves or rejects the action.

    Blocks until decision or timeout. Returns status='approved'|'rejected'|'timeout'.
    timeout_seconds: maximum wait time (default 5 minutes).
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = requests.get(f"{APPROVAL_SERVER}/approval/{approval_id}/status", timeout=30)
        except requests.RequestException as e:
            return {"error": f"Cannot reach approval server: {e}"}

        if resp.status_code != 200:
            return {"error": f"Approval server returned {resp.status_code}"}

        data = resp.json()
        if data["status"] in ("approved", "rejected"):
            return data

        remaining = int(deadline - time.time())
        print(f"[approval] Waiting for decision on {approval_id} — {remaining}s remaining...")
        time.sleep(poll_interval)

    return {
        "status": "timeout",
        "approval_id": approval_id,
        "message": f"No decision received within {timeout_seconds}s — standing down",
    }


def trigger_github_rollback(commit_sha: str, incident_id: str) -> dict:
    """Trigger the GitHub Actions rollback workflow via workflow_dispatch.

    Dispatches the rollback.yml workflow targeting the specified commit SHA.
    Returns immediately — the workflow runs asynchronously in GitHub Actions.
    """
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    owner, repo = GITHUB_REPO.split("/", 1)
    payload = {
        "ref": "main",
        "inputs": {
            "rollback_to": commit_sha,
            "incident_id": incident_id,
            "triggered_by": "voiceops-agent",
        },
    }
    try:
        resp = requests.post(
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/rollback.yml/dispatches",
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        return {"error": f"Network error contacting GitHub Actions: {e}"}

    if resp.status_code == 204:
        return {
            "status": "triggered",
            "workflow": "rollback.yml",
            "rollback_to": commit_sha,
            "incident_id": incident_id,
            "repo": GITHUB_REPO,
            "actions_url": f"https://github.com/{GITHUB_REPO}/actions",
        }
    if resp.status_code == 404:
        return {"error": "rollback.yml workflow not found — ensure .github/workflows/rollback.yml exists and is committed to main"}
    if resp.status_code == 422:
        return {"error": f"Invalid dispatch inputs: {resp.text[:300]}"}
    return {"error": f"GitHub Actions API returned {resp.status_code}: {resp.text[:300]}"}
