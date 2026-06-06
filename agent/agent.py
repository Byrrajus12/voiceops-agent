"""Autonomous Incident Commander — Google ADK agent with Dynatrace MCP integration."""
import os

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import Agent  # noqa: E402
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioConnectionParams, StdioServerParameters  # noqa: E402

from agent.tools import (  # noqa: E402
    generate_voice_briefing,
    get_recent_github_commits,
    poll_approval_status,
    request_human_approval,
    trigger_github_rollback,
)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Dynatrace MCP server — uses apps.dynatrace.com (Platform API) with dt0s16 platform token.
# Required token scopes: storage:problems:read, storage:events:read, storage:logs:read,
#   davis:problems:read, document:read, environment-api:events:write
_dt_env = os.getenv("DT_ENVIRONMENT", f"https://{os.getenv('DYNATRACE_TENANT', 'pmn17776.apps.dynatrace.com')}")
if not _dt_env.startswith("http"):
    _dt_env = f"https://{_dt_env}"

dynatrace_mcp = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="npx",
            args=["-y", "@dynatrace-oss/dynatrace-mcp-server@latest"],
            env={
                "DT_ENVIRONMENT": _dt_env,
                "DT_PLATFORM_TOKEN": os.getenv("DYNATRACE_PLATFORM_TOKEN", ""),
            },
        ),
        timeout=30.0,
    ),
    tool_filter=[
        "list_problems",
        "execute_dql",
        "send_event",
        "generate_dql_from_natural_language",
        "find_entity_by_name",
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
    instruction="""You are VoiceOps — an Autonomous Incident Commander. You have access to the
Dynatrace MCP server (list_problems, execute_dql, send_event, generate_dql_from_natural_language,
find_entity_by_name) plus tools for GitHub, Google TTS, human approval, and rollback execution.

## Step 1 — DETECT
Call list_problems to get open Dynatrace Davis AI problems.
- If no problems: respond "All clear — Dynatrace Davis AI reports no open incidents." and stop.
- If problems exist: pick the highest-severity one. Note its id, title, severity, and startTime.

## Step 2 — CORRELATE
Call get_recent_github_commits (limit=15).
Find the commit whose timestamp is closest to and before the incident startTime.
State: "Commit <sha> by <author> at <time>, ~N min before incident start."
If you need more signal, use generate_dql_from_natural_language then execute_dql to query logs.

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
  │  Approve: POST http://localhost:9000/approve/<id>    │
  │  Reject:  POST http://localhost:9000/reject/<id>     │
  └──────────────────────────────────────────────────────┘

Then call poll_approval_status(approval_id, timeout_seconds=300).

## Step 5 — ACT
- Approved → call trigger_github_rollback(commit_sha=<full_sha>, incident_id=<id>)
  Then call send_event to write a ROLLBACK_TRIGGERED event back into Dynatrace for traceability.
  Report: "Rollback triggered. Watch: https://github.com/<repo>/actions"
- Rejected → "Operator rejected rollback. Reason: <reason>. Standing down."
- Timeout → "No decision in 5 min. Standing down — page on-call manually."

Always be factual, specific, and brief. Never skip steps.""",
    tools=[
        dynatrace_mcp,
        generate_voice_briefing,
        get_recent_github_commits,
        request_human_approval,
        poll_approval_status,
        trigger_github_rollback,
    ],
)
