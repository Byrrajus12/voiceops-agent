# VoiceOps — Autonomous Incident Commander

> **Google Cloud Rapid Agent Hackathon 2026 · Dynatrace Track**

An AI agent built on **Google ADK + Gemini 2.5 Flash** that autonomously detects production incidents via **Dynatrace Davis AI**, diagnoses the root cause by correlating with GitHub commits, narrates a **Google Cloud TTS voice briefing**, waits for human approval, and triggers a **GitHub Actions rollback** — all in one closed loop.

## Live Demo

| Service | URL |
|---------|-----|
| **Agent Web UI** | https://voiceops-agent-224808509436.us-central1.run.app |
| **Approval Server** | https://voiceops-approval-224808509436.us-central1.run.app |
| **Target Service** (broken) | https://voiceops-target-224808509436.us-central1.run.app |

## Architecture

```
Dynatrace Davis AI
      │  Dynatrace MCP Gateway (Streamable HTTP)
      ▼
┌─────────────────────────────────────────────────────────┐
│           VoiceOps Incident Commander                   │
│         Google ADK · Gemini 2.5 Flash · Vertex AI       │
│                                                         │
│  Step 1 DETECT    query_problems → get_problem_by_id    │
│  Step 2 DIAGNOSE  get_recent_github_commits             │
│                   create_dql → execute_dql              │
│                   ask_dynatrace_docs                    │
│                   find_troubleshooting_guides           │
│                   create_github_issue                   │
│  Step 3 BRIEF     generate_voice_briefing (TTS → MP3)   │
│  Step 4 APPROVE   request_human_approval                │
│                   poll_approval_status                  │
│  Step 5 ACT       trigger_github_rollback               │
│         VERIFY    query_problems (confirm resolved)     │
└──────────────┬──────────────────┬───────────────────────┘
               │                  │
     GitHub Actions           Approval Server
     rollback.yml             /approve  /reject
```

## What makes this different

- **Dynatrace MCP Gateway** — uses 8 Dynatrace tools via the official hosted MCP gateway (`StreamableHTTPConnectionParams`), not a subprocess
- **Closed-loop verification** — after rollback, the agent re-queries Dynatrace to confirm the problem resolved
- **Human-in-the-loop gate** — no destructive action without explicit operator approval; 5-minute timeout with graceful standdown
- **Voice-first UX** — Google Cloud TTS Neural2-D voice generates an MP3 briefing before the approval request
- **Full paper trail** — GitHub issue created automatically at detection time, GitHub Actions workflow records the rollback

## Dynatrace MCP Tools Used

| Tool | Purpose |
|------|---------|
| `query_problems` | Fetch all open Davis AI problems |
| `get_problem_by_id` | Full details on a specific problem |
| `execute_dql` | Run DQL to query logs/traces/metrics |
| `create_dql` | Generate DQL from natural language |
| `get_entity_name` | Resolve DT entity ID → human name |
| `get_entity_id` | Find entity ID by name |
| `ask_dynatrace_docs` | Look up Dynatrace docs for problem context |
| `find_troubleshooting_guides` | Fetch remediation runbooks |

## Stack

| Layer | Technology |
|-------|-----------|
| Agent framework | Google ADK 2.2.0 |
| LLM | Gemini 2.5 Flash via Vertex AI (`locations/global`) |
| Observability | Dynatrace MCP Gateway + Davis AI |
| Voice | Google Cloud TTS Neural2-D |
| Source control | GitHub REST API + GitHub Actions |
| Hosting | Google Cloud Run |

## Project Layout

```
voiceops-agent/
├── agent/
│   ├── agent.py          # ADK Agent with McpToolset + 7 custom tools
│   ├── tools.py          # GitHub, TTS, approval, rollback tool functions
│   └── Dockerfile        # Cloud Run image (python:3.11-slim)
├── approval-server/
│   └── main.py           # FastAPI: /approval/request, /approve/{id}, /reject/{id}
├── target-service/
│   └── main.py           # FastAPI with BROKEN=true mode + OTel traces to Dynatrace
├── .github/workflows/
│   └── rollback.yml      # workflow_dispatch: checkout sha, build, health check
├── Dockerfile            # Root Dockerfile for agent Cloud Run deploy
└── .env.example          # Required environment variables
```

## Quick Start (local)

```bash
git clone https://github.com/Byrrajus12/voiceops-agent.git
cd voiceops-agent
cp .env.example .env
# Fill in tokens — see .env.example for required scopes

pip install -r requirements.txt

# Start approval server
uvicorn approval-server.main:app --port 8080

# Run agent web UI
adk web agent
```

Open http://localhost:8000, then prompt:
> "Check for active incidents and run the full incident response workflow."

## Running the demo

### Approve a rollback
When the agent prints an `approval_id`:
```bash
# Via approval_id
curl -X POST "https://voiceops-approval-224808509436.us-central1.run.app/approve/<approval_id>"

# Via incident_id (simpler)
curl -X POST "https://voiceops-approval-224808509436.us-central1.run.app/incident/<incident_id>/approve"
```

### Reject
```bash
curl -X POST "https://voiceops-approval-224808509436.us-central1.run.app/reject/<approval_id>?reason=false+positive"
```

## Required Token Scopes

### Dynatrace Platform Token (dt0s16.*)
`mcp-gateway:servers:read`, `mcp-gateway:servers:invoke`, `storage:problems:read`, `storage:events:read`, `storage:logs:read`, `davis:problems:read`, `document:read`

### GitHub PAT (fine-grained)
`Contents: read`, `Issues: write`, `Actions: write`

## Environment Variables

See [`.env.example`](.env.example) for full list. Key variables:

| Variable | Description |
|----------|-------------|
| `DYNATRACE_PLATFORM_TOKEN` | DT Platform token with MCP gateway scopes |
| `GITHUB_PAT` | GitHub PAT with repo + actions write |
| `GITHUB_REPO` | `owner/repo` to monitor |
| `GOOGLE_CLOUD_PROJECT` | GCP project for Vertex AI + TTS |
| `APPROVAL_SERVER_URL` | Approval server base URL |

## License

Apache 2.0 — see [LICENSE](LICENSE)
