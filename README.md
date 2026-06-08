# VoiceOps — Autonomous Incident Commander

An AI agent built on **Google ADK + Gemini 2.5 Flash** that autonomously detects production incidents via **Dynatrace Davis AI**, correlates them with GitHub commits, places a live outbound call via **VAPI**, gates rollback on confidence (auto for HIGH, human approval for MEDIUM/LOW), triggers a **GitHub Actions rollback**, then runs a full post-incident RCA — all in one closed loop.

> Built for the Google Cloud Rapid Agent Hackathon 2026 · Dynatrace Track

---

## Live Demo

| Service | URL |
|---------|-----|
| Agent Web UI | https://voiceops-agent-224808509436.us-central1.run.app |
| Approval Dashboard | https://voiceops-approval-224808509436.us-central1.run.app |
| Target Service | https://voiceops-target-224808509436.us-central1.run.app |

---

## How It Works

The agent runs in two phases:

**Phase 1 — Triage & Mitigate** (stop the bleeding first)
1. Queries Dynatrace Davis AI for open problems
2. Fetches recent GitHub commits, finds any deployed within 60 min of incident start → confidence score
3. Places a live VAPI phone call with the incident briefing
4. HIGH confidence → triggers rollback automatically; MEDIUM/LOW → creates approval request first, places call with `approval_id` in metadata so saying "approve" or pressing 1 on the call resolves it directly; also available via browser dashboard
5. Polls GitHub Actions until rollback completes, then re-checks Dynatrace to confirm problem closed

**Phase 2 — Post-Incident** (only after service is restored)

6. Runs deep DQL signal analysis + anomaly detection to confirm root cause
7. Calculates blast radius, failed request count, and error trend
8. Places a resolution VAPI call, files a post-incident report as a GitHub issue, closes the tracking issue

```
Dynatrace Davis AI
      │  Dynatrace MCP Gateway (Streamable HTTP)
      ▼
┌──────────────────────────────────────────────┐
│         VoiceOps Incident Commander          │
│     Google ADK · Gemini 2.5 Flash · GCP      │
│                                              │
│  Phase 1 — Triage & Mitigate                 │
│    query_problems + get_problem_by_id        │
│    get_recent_github_commits  (confidence)   │
│    place_voice_call           (VAPI call)    │
│    [HIGH] → trigger_github_rollback          │
│    [MED/LOW] → request_human_approval        │
│             → place_voice_call (approval_id) │
│             → trigger_github_rollback        │
│    get_github_workflow_status (poll)         │
│    query_problems             (verify)       │
│                                              │
│  Phase 2 — Post-Incident Analysis            │
│    create_dql + execute_dql   (signals)      │
│    adaptive_anomaly_detector                 │
│    ask_dynatrace_docs                        │
│    place_voice_call           (resolution)   │
│    create_github_issue        (PIR)          │
│    close_github_issue                        │
└─────────────────┬──────────────┬─────────────┘
                  │              │
        GitHub Actions     Approval Server
        rollback.yml       browser dashboard
```

---

## Prerequisites

- Python 3.11+
- A **Google Cloud project** with Vertex AI API enabled
- A **Dynatrace** environment with the MCP Gateway enabled (`pmn*.apps.dynatrace.com`)
- A **VAPI** account with an assistant and a phone number (Twilio as provider recommended for reliability)
- A **GitHub** repository with `rollback.yml` committed (see `.github/workflows/rollback.yml`)
- Application Default Credentials configured: `gcloud auth application-default login`

---

## Local Setup

### 1. Clone and install

```bash
git clone https://github.com/Byrrajus12/voiceops-agent.git
cd voiceops-agent
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in all values (see [Environment Variables](#environment-variables) below).

### 3. Start the approval server

```bash
uvicorn approval-server.main:app --port 8080
```

The approval dashboard is at http://localhost:8080.

### 4. Start the agent

```bash
adk web agent
```

Open http://localhost:8000, select `incident_commander`, and send:

> Check for active incidents and run the full incident response workflow.

---

## Triggering an Incident (Demo)

To simulate a production incident, deploy the target service with `BROKEN=true`. This makes `POST /voice-agent/session/start` return HTTP 500, which a Dynatrace Synthetic Monitor will detect and escalate to Davis AI.

To break/fix locally:
```bash
# Simulate failure
TARGET_SERVICE_URL=http://localhost:9000
BROKEN=true uvicorn target-service.main:app --port 9000

# The agent prompt once a Davis AI problem appears:
# "Check for active incidents and run the full incident response workflow."
```

---

## Approving / Rejecting a Rollback

When the agent hits MEDIUM or LOW confidence, it creates an approval request then places a VAPI phone call with the `approval_id` embedded in call metadata. You have four ways to respond:

**Phone call** — say "approve" or press **1** on the call. The VAPI webhook resolves the approval immediately via the `approval_id` in metadata.

**Browser** — open the approval dashboard and click Approve or Reject.

**By incident ID** (simplest for demo):
```bash
curl -X POST "<APPROVAL_SERVER_URL>/incident/<display_id>/approve"
curl -X POST "<APPROVAL_SERVER_URL>/incident/<display_id>/reject?reason=false+positive"
```

**By approval ID** (printed by the agent):
```bash
curl -X POST "<APPROVAL_SERVER_URL>/approve/<approval_id>"
curl -X POST "<APPROVAL_SERVER_URL>/reject/<approval_id>"
```

The agent polls for up to 5 minutes. If no decision arrives it stands down and tells you to escalate manually.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `DYNATRACE_PLATFORM_TOKEN` | `dt0s16.*` platform token — see scopes below |
| `GITHUB_PAT` | GitHub fine-grained PAT — see scopes below |
| `GITHUB_REPO` | Repository to monitor, e.g. `owner/repo` |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID for Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | Set to `global` for Gemini 2.5 Flash |
| `GOOGLE_GENAI_USE_VERTEXAI` | Set to `1` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account key JSON (Cloud Run uses workload identity instead) |
| `VAPI_API_KEY` | VAPI API key from dashboard |
| `VAPI_PHONE_NUMBER_ID` | VAPI phone number UUID to call from (Twilio as provider recommended) |
| `VAPI_ASSISTANT_ID` | VAPI assistant UUID |
| `VAPI_CALLER_NUMBER` | Phone number to call (E.164 format, e.g. `+15550001234`) |
| `VAPI_WEBHOOK_URL` | URL of the VAPI webhook endpoint on the approval server |
| `APPROVAL_SERVER_URL` | Base URL of the approval server |
| `TARGET_SERVICE_URL` | Base URL of the service being monitored |
| `GEMINI_MODEL` | Model ID, default `gemini-2.5-flash` |

### Dynatrace Platform Token scopes

```
mcp-gateway:servers:read
mcp-gateway:servers:invoke
storage:problems:read
storage:events:read
storage:logs:read
davis:problems:read
document:read
```

### GitHub PAT permissions (fine-grained)

```
Contents: Read
Issues: Read & Write
Actions: Read & Write
```

### GitHub Actions secrets (for rollback workflow)

The `rollback.yml` workflow needs two repository secrets set at `Settings → Secrets and variables → Actions`:

| Secret | Value |
|--------|-------|
| `GCP_SA_KEY` | Contents of the GCP service account key JSON (`sa-key.json`) |
| `DYNATRACE_PLATFORM_TOKEN` | Same `dt0s16.*` token as in `.env` |

The service account (`voiceops-agent@<project>.iam.gserviceaccount.com`) needs these IAM roles:
- `roles/run.developer`
- `roles/artifactregistry.writer`
- `roles/iam.serviceAccountUser` (on the compute SA `<number>-compute@developer.gserviceaccount.com`)

---

## Dynatrace MCP Tools

| Tool | Used for |
|------|---------|
| `query_problems` | List all open Davis AI problems |
| `get_problem_by_id` | Full problem details + affected entities |
| `execute_dql` | Query logs, traces, and metrics |
| `create_dql` | Generate DQL from natural language |
| `get_entity_name` | Resolve entity ID to human-readable name |
| `get_entity_id` | Look up entity by name |
| `ask_dynatrace_docs` | Explain a problem category from DT docs |
| `find_troubleshooting_guides` | Fetch runbooks for a problem type |
| `adaptive_anomaly_detector` | Pinpoint when a metric deviated from baseline |

---

## Project Layout

```
voiceops-agent/
├── agent/
│   ├── agent.py            # ADK Agent — McpToolset + instruction + tool list
│   ├── tools.py            # Custom tools: GitHub, VAPI, approval, rollback
│   ├── requirements.txt    # Runtime deps for Cloud Run (no Windows packages)
│   └── test-data/          # Session JSON exports for debugging and review
├── approval-server/
│   └── main.py             # FastAPI approval server + VAPI webhook + browser UI
├── target-service/
│   └── main.py             # Demo service — BROKEN=true triggers HTTP 500 + OTel
├── .github/workflows/
│   └── rollback.yml        # workflow_dispatch rollback (Docker build + Cloud Run)
├── Dockerfile              # Agent image — copies agent/requirements.txt
├── demo.sh                 # Break/fix/approve/status/deploy helpers
├── .env.example
└── PROGRESS.md             # Development status and next steps
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Agent framework | Google ADK 2.2.0 |
| LLM | Gemini 2.5 Flash via Vertex AI (`locations/global`) |
| Observability | Dynatrace MCP Gateway + Davis AI |
| Voice | VAPI — outbound phone calls (Twilio as provider) |
| Source control | GitHub REST API + GitHub Actions |
| Hosting | Google Cloud Run |

---

## License

Apache 2.0 — see [LICENSE](LICENSE)
