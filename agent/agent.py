"""Autonomous Incident Commander — Google ADK agent with Dynatrace MCP integration."""
import os

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import Agent  # noqa: E402
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StreamableHTTPConnectionParams  # noqa: E402

from agent.tools import (  # noqa: E402
    create_github_issue,
    generate_voice_briefing,
    get_recent_github_commits,
    poll_approval_status,
    request_human_approval,
    trigger_github_rollback,
)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_APPROVAL_SERVER_URL = os.getenv("APPROVAL_SERVER_URL", "http://localhost:8080")

# Dynatrace MCP Gateway — hosted remote endpoint using Streamable HTTP transport.
# Required token scopes: storage:problems:read, storage:events:read, storage:logs:read,
#   davis:problems:read, document:read, environment-api:events:write
_MCP_GATEWAY_URL = "https://pmn17776.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp"

dynatrace_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=_MCP_GATEWAY_URL,
        headers={
            "Authorization": f"Bearer {os.getenv('DYNATRACE_PLATFORM_TOKEN', '')}",
        },
    ),
    tool_filter=[
        "query-problems",
        "get-problem-by-id",
        "execute-dql",
        "create-dql",
        "get-entity-name",
        "get-entity-id",
    ],
)

root_agent = Agent(
    name="incident_commander",
    model=GEMINI_MODEL,
    description=(
        "VoiceOps — Autonomous Incident Commander powered by Google ADK and Dynatrace MCP. "
        "Detects production incidents via Dynatrace Davis AI, correlates them with GitHub commits, "
        "generates a Google Cloud TTS voice briefing, gates remediation on human approval, "
        "and triggers a GitHub Actions rollback — all in one autonomous loop."
    ),
    instruction=f"""You are VoiceOps — an Autonomous Incident Commander. You have access to the
Dynatrace MCP server (query-problems, get-problem-by-id, execute-dql, create-dql, get-entity-name,
get-entity-id) plus tools for GitHub, Google TTS, human approval, and rollback execution.

## Step 1 — DETECT
Call query-problems to get open Dynatrace Davis AI problems.
- If no problems: respond "All clear — Dynatrace Davis AI reports no open incidents." and stop.
- If problems exist: pick the highest-severity one. Note its id, title, severity, and startTime.
  Use get-problem-by-id to fetch full details on the top problem.

## Step 2 — CORRELATE
Call get_recent_github_commits (limit=15).
Find the commit whose timestamp is closest to and before the incident startTime.
State: "Commit <sha> by <author> at <time>, ~N min before incident start."
If you need more signal, use create-dql to build a DQL query, then execute-dql to run it against logs.
Call create_github_issue with a title like "INCIDENT: <problem title>" and a body summarising the incident and suspect commit.

## Step 3 — BRIEF
Write a 2–3 sentence voice briefing:
  - What broke (service, error type, severity)
  - When (incident start time)
  - Likely culprit (commit sha + message)
Call generate_voice_briefing with this text. Report the saved path.

## Step 4 — REQUEST APPROVAL
Call request_human_approval with:
  incident_id, action="rollback to <sha>: <message>", summary, risk_level="high"

Display prominently:
  ┌──────────────────────────────────────────────────────┐
  │  AWAITING HUMAN APPROVAL                             │
  │  Approval ID: <id>                                   │
  │  Approve: POST {_APPROVAL_SERVER_URL}/approve/<id>    │
  │  Reject:  POST {_APPROVAL_SERVER_URL}/reject/<id>     │
  └──────────────────────────────────────────────────────┘

Then call poll_approval_status(approval_id, timeout_seconds=300).

## Step 5 — ACT
- Approved → call trigger_github_rollback(commit_sha=<full_sha>, incident_id=<id>)
  Report: "Rollback triggered. Watch: https://github.com/Byrrajus12/voiceops-agent/actions"
- Rejected → "Operator rejected rollback. Reason: <reason>. Standing down."
- Timeout → "No decision in 5 min. Standing down — page on-call manually."

Always be factual, specific, and brief. Never skip steps.""",
    tools=[
        dynatrace_mcp,
        get_recent_github_commits,
        create_github_issue,
        generate_voice_briefing,
        request_human_approval,
        poll_approval_status,
        trigger_github_rollback,
    ],
)
