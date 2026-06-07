"""Tool functions for the Incident Commander agent.

Dynatrace tools are provided via the Dynatrace MCP server (McpToolset in agent.py).
This module covers: GitHub commit lookup, Google TTS briefing, human approval gateway,
and GitHub Actions rollback dispatch.
"""
import os
import time

import requests

GITHUB_TOKEN = os.getenv("GITHUB_PAT", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Byrrajus12/voiceops-agent")
APPROVAL_SERVER = os.getenv("APPROVAL_SERVER_URL", "http://localhost:9000")
VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_WEBHOOK_URL = os.getenv("VAPI_WEBHOOK_URL", "")
VAPI_CALLER_NUMBER = os.getenv("VAPI_CALLER_NUMBER", "")


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
            ssml_gender=texttospeech.SsmlVoiceGender.MALE,
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


def request_human_approval(incident_id: str, action: str, summary: str, risk_level: str = "high", confidence: str = "MEDIUM") -> dict:
    """Submit a rollback approval request to the human approval server.

    Only call this for MEDIUM or LOW confidence diagnoses. HIGH confidence cases
    should trigger rollback directly without human gate.
    Returns an approval_id that the operator uses to approve/reject via the dashboard.
    """
    payload = {
        "incident_id": incident_id,
        "action": action,
        "summary": summary,
        "risk_level": risk_level,
        "confidence": confidence,
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


def create_github_issue(incident_id: str, title: str, body: str) -> dict:
    """Create an incident tracking issue on GitHub with the 'incident' label.

    Call this after detecting a Dynatrace problem to create a paper trail.
    Returns the URL of the created issue.
    """
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    owner, repo = GITHUB_REPO.split("/", 1)
    try:
        resp = requests.post(
            f"https://api.github.com/repos/{owner}/{repo}/issues",
            headers=headers,
            json={"title": title, "body": body, "labels": ["incident"]},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"error": f"Network error contacting GitHub: {e}"}

    if resp.status_code == 410:
        return {"error": "Issues are disabled on this repository"}
    if resp.status_code not in (200, 201):
        return {"error": f"GitHub API returned {resp.status_code}: {resp.text[:300]}"}

    issue = resp.json()
    return {"url": issue["html_url"], "number": issue["number"], "incident_id": incident_id}


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
        return {"error": "rollback.yml not found — ensure .github/workflows/rollback.yml is committed to main"}
    if resp.status_code == 422:
        return {"error": f"Invalid dispatch inputs: {resp.text[:300]}"}
    return {"error": f"GitHub Actions API returned {resp.status_code}: {resp.text[:300]}"}


def place_voice_call(briefing_text: str, to_number: str | None = None, incident_id: str | None = None) -> dict:
    """Initiate an outbound phone call via VAPI.

    Attempts to create a call using VAPI and instruct the assistant to play the briefing
    and ask for an approval phrase (e.g., "approve"). If VAPI credentials are missing or
    the API call fails, falls back to `generate_voice_briefing` (TTS to file) and returns
    the path to the transcript or audio.
    """
    if not VAPI_API_KEY or not VAPI_PHONE_NUMBER_ID or not VAPI_ASSISTANT_ID:
        # Missing credentials — fallback to TTS transcript/audio
        fallback_path = "/tmp/incident_briefing.txt"
        try:
            # attempt normal TTS mp3 generation first
            tts_result = generate_voice_briefing(briefing_text)
            return {"status": "vapi_unavailable", "reason": "missing_credentials", "fallback": tts_result}
        except Exception:
            with open(fallback_path, "w") as f:
                f.write(briefing_text)
            return {"status": "vapi_unavailable", "reason": "missing_credentials", "path": fallback_path}

    payload = {
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "assistantId": VAPI_ASSISTANT_ID,
        "customer": {"number": to_number or os.getenv("YOUR_PHONE_NUMBER")},
        "assistantOverrides": {
            "firstMessage": briefing_text,
        },
        "metadata": {},
    }
    if incident_id:
        payload["metadata"]["incident_id"] = incident_id

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post("https://api.vapi.ai/call/phone", headers=headers, json=payload, timeout=30)
    except requests.RequestException as e:
        # network error — fallback to TTS
        tts_result = generate_voice_briefing(briefing_text)
        return {"status": "error", "error": f"Network error contacting VAPI: {e}", "fallback": tts_result}

    if resp.status_code in (200, 201, 202, 204):
        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": resp.text}
        return {"status": "triggered", "vapi_status": resp.status_code, "response": data}

    # Non-success from VAPI — fallback
    try:
        body = resp.json()
    except Exception:
        body = {"text": resp.text}
    tts_result = generate_voice_briefing(briefing_text)
    return {"status": "error", "vapi_status": resp.status_code, "vapi_response": body, "fallback": tts_result}


def get_github_workflow_status(workflow_file: str = "rollback.yml", wait_for_completion: bool = True) -> dict:
    """Poll the most recent GitHub Actions workflow run until it completes.

    Call this after trigger_github_rollback. Polls every 10s for up to 3 minutes.
    Returns conclusion: success|failure, or status: in_progress if still running after timeout.
    """
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    owner, repo = GITHUB_REPO.split("/", 1)
    deadline = time.time() + 180  # 3 min max

    while True:
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs",
                headers=headers,
                params={"per_page": 1},
                timeout=30,
            )
        except requests.RequestException as e:
            return {"error": f"Network error: {e}"}

        if resp.status_code != 200:
            return {"error": f"GitHub API returned {resp.status_code}"}

        runs = resp.json().get("workflow_runs", [])
        if not runs:
            return {"status": "no_runs", "message": "No workflow runs found"}

        run = runs[0]
        if run["status"] == "completed" or not wait_for_completion or time.time() >= deadline:
            return {
                "status": run["status"],
                "conclusion": run["conclusion"],
                "run_id": run["id"],
                "html_url": run["html_url"],
                "created_at": run["created_at"],
                "head_sha": run["head_sha"][:8],
                "display": f"{run['status'].upper()} / {run['conclusion'] or 'in progress'}"
            }

        print(f"[workflow] {run['status']} — polling again in 10s...", flush=True)
        time.sleep(10)


def close_github_issue(issue_number: int, resolution_comment: str) -> dict:
    """Close the incident GitHub issue with a resolution comment.

    Call this after confirming the incident is resolved to complete the paper trail.
    """
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    owner, repo = GITHUB_REPO.split("/", 1)
    try:
        # Post resolution comment
        requests.post(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments",
            headers=headers,
            json={"body": resolution_comment},
            timeout=30,
        )
        # Close the issue
        resp = requests.patch(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}",
            headers=headers,
            json={"state": "closed", "state_reason": "completed"},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"error": f"Network error: {e}"}

    if resp.status_code != 200:
        return {"error": f"GitHub API returned {resp.status_code}: {resp.text[:200]}"}

    return {"status": "closed", "issue_number": issue_number, "url": resp.json()["html_url"]}
