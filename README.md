# VoiceOps Agent — Autonomous Incident Commander

An AI agent built on **Google ADK + Gemini** that autonomously detects production incidents, correlates them to the culprit commit, narrates a voice briefing via **Google Cloud TTS**, waits for human approval, then triggers a **GitHub Actions** rollback — all in one loop.

```
Dynatrace Alert
      │
      ▼
[1] Detect incident (Dynatrace API)
      │
      ▼
[2] Correlate with GitHub commits
      │
      ▼
[3] Generate voice briefing (Google TTS)
      │
      ▼
[4] Request human approval (approval-server webhook)
      │
    ┌─┴─────────────┐
    │ Approved       │ Rejected / Timeout
    ▼               ▼
[5a] Trigger        [5b] Stand down
   GitHub Actions
   rollback.yml
```

## Components

| Directory | What it does |
|-----------|-------------|
| `agent/` | Google ADK agent (Gemini 2.0 Flash) with 7 tools |
| `approval-server/` | FastAPI webhook — `/approve/{id}` and `/reject/{id}` |
| `target-service/` | Broken FastAPI checkout service (BROKEN=true → 80% 500s) |
| `.github/workflows/rollback.yml` | GitHub Actions rollback workflow (workflow_dispatch) |

## Quick Start

### 1. Clone & configure

```bash
git clone https://github.com/Byrrajus12/voiceops-agent.git
cd voiceops-agent
cp .env.example .env
# Fill in your tokens in .env
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the services

```bash
# Terminal 1 — approval server
uvicorn approval-server.main:app --port 9000 --reload

# Terminal 2 — broken checkout service
BROKEN=true uvicorn target-service.main:app --port 8080 --reload

# Terminal 3 — load tester (generates 500s for Dynatrace to detect)
python target-service/load_tester.py http://localhost:8080 10 300
```

### 4. Run the agent

```bash
# Interactive web UI (recommended for demos)
adk web

# Or run headless
python -m agent.run
```

### 5. Approve or reject

When the agent prints an `approval_id`:

```bash
# Approve the rollback
curl -X POST "http://localhost:9000/approve/<approval_id>?reason=confirmed"

# Or reject it
curl -X POST "http://localhost:9000/reject/<approval_id>?reason=false+positive"
```

## Docker Compose

```bash
docker compose up
```

Starts both services. The agent still runs locally via `adk web` or `python -m agent.run`.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DYNATRACE_TENANT` | Dynatrace environment hostname (e.g. `wkf10640.apps.dynatrace.com`) |
| `DYNATRACE_PLATFORM_TOKEN` | Dynatrace API token (needs `problems.read`, `events.ingest`) |
| `GITHUB_PAT` | GitHub Personal Access Token (needs `repo`, `workflow`) |
| `GITHUB_REPO` | `owner/repo` of the monitored repository |
| `GEMINI_API_KEY` | Google AI API key for Gemini |
| `GCP_PROJECT` | GCP project ID (for Google Cloud TTS) |
| `APPROVAL_SERVER_URL` | URL of the approval server (default: `http://localhost:9000`) |
| `BROKEN` | Set `true` to activate 80% failure rate on checkout service |
| `FAILURE_RATE` | Override failure rate (default `0.8`) |

## Agent Tools

| Tool | Purpose |
|------|---------|
| `check_dynatrace_incidents` | List open Dynatrace problems via API v2 |
| `create_dynatrace_test_event` | Ingest a synthetic availability event (demo mode) |
| `get_recent_github_commits` | Fetch recent commits for correlation |
| `generate_voice_briefing` | Synthesize MP3 via Google Cloud TTS |
| `request_human_approval` | POST approval request to approval-server |
| `poll_approval_status` | Block until approved/rejected/timeout |
| `trigger_github_rollback` | Dispatch `rollback.yml` via GitHub Actions API |

## Demo Flow (no real Dynatrace incident)

```bash
# 1. Start services
docker compose up -d

# 2. Create a synthetic Dynatrace event via the agent
adk web
# Prompt: "Create a test incident for checkout-service with 80% error rate, then run the full workflow"
```
