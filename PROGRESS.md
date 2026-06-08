# VoiceOps — Development Progress

**Hackathon:** Google Cloud Rapid Agent Hackathon 2026 · Dynatrace Track  
**Deadline:** June 11, 2026  
**Prize:** $5,000 first place  
**Repo:** https://github.com/Byrrajus12/voiceops-agent

---

## What's Built

### Infrastructure (all deployed on Cloud Run)

| Service | URL | Status |
|---------|-----|--------|
| Agent (ADK web UI) | https://voiceops-agent-224808509436.us-central1.run.app | ✅ Live |
| Approval Server | https://voiceops-approval-224808509436.us-central1.run.app | ✅ Live |
| Target Service | https://voiceops-target-224808509436.us-central1.run.app | ✅ Live |

### Agent (`agent/agent.py`)
- Google ADK 2.2.0 + Gemini 2.5 Flash via Vertex AI (`locations/global`)
- Dynatrace MCP Gateway via `McpToolset` + `StreamableHTTPConnectionParams`
- 9 Dynatrace tools via MCP: `query_problems`, `get_problem_by_id`, `execute_dql`, `create_dql`, `get_entity_name`, `get_entity_id`, `ask_dynatrace_docs`, `find_troubleshooting_guides`, `adaptive_anomaly_detector`
- 8 custom Python tools (GitHub, VAPI, approval, rollback)

### Workflow (current — mitigate-first design)

**Phase 1 — Triage & Mitigate**
1. **DETECT** — `query_problems` + `get_problem_by_id` + `get_entity_name`
2. **QUICK TRIAGE** — `get_recent_github_commits(limit=10)`, find commits within 60 min before incident → confidence level (HIGH / MEDIUM / LOW)
3. **ALERT BRIEF** — `place_voice_call` → VAPI outbound call (Twilio as provider). For MEDIUM/LOW: `request_human_approval` first to get `approval_id`, embed it in call metadata so saying "approve" / pressing 1 resolves directly.
4. **GATE & ROLLBACK** — HIGH confidence → auto-rollback; MEDIUM/LOW → `poll_approval_status` (voice, dashboard, or REST) → `trigger_github_rollback` → `get_github_workflow_status` (polls to completion) → `query_problems` (confirm resolved; Dynatrace takes 3–5 min post-deploy to re-run synthetic monitor)

**Phase 2 — Post-Incident (runs only after service is restored)**

5. **ROOT CAUSE ANALYSIS** — `create_dql` + `execute_dql` (error rate/5xx spike), `adaptive_anomaly_detector`, `ask_dynatrace_docs`, `find_troubleshooting_guides`
6. **IMPACT ANALYSIS** — failed request count, blast radius, trend at time of detection
7. **CLOSE** — `send_voice_update` (RCA summary to operator + open-ended conversational wrap-up), `create_github_issue` (post-incident report), `close_github_issue`

### Approval Server (`approval-server/main.py`)
- FastAPI with browser dashboard at `/`
- Approve/reject via UI buttons, REST API, or VAPI phone call (say "approve" / "do it")
- Confidence badges (HIGH=green, MEDIUM=amber, LOW=red)
- Auto-refreshes every 5 seconds
- Routes: `/approval/request`, `/approve/{id}`, `/reject/{id}`, `/incident/{id}/approve`, `/incident/{id}/reject`, `/webhook/vapi`
- VAPI webhook parses `message.call.metadata` for `approval_id` — direct lookup bypasses incident_id scan
- **VAPI server-side tools** (3 endpoints called by VAPI assistant during the call):
  - `POST /webhook/vapi/tool/get_incident_status` — returns current incident state (only matches `status=="pending"` approvals to avoid stale reads)
  - `POST /webhook/vapi/tool/approve_rollback` — resolves pending approval, triggers rollback
  - `POST /webhook/vapi/tool/reject_rollback` — rejects pending approval, stands down
- All tool endpoints return `{"results": [{"toolCallId": "...", "result": "..."}]}` (plural results array, toolCallId echoed)
- Incident state: `POST /incident/{id}/state` (ADK writes), `GET /incident/{id}/state` (VAPI reads)

### Target Service (`target-service/`)
- FastAPI with OTel traces → Dynatrace via Bearer token
- Dynatrace Synthetic HTTP Monitor fires every 1 min from GCP Iowa → triggers Davis AI problem (AVAILABILITY) when broken
- **Demo mechanism**: `demo.sh break crash` copies `demo-scenarios/crash_bad.py` → `session_handler.py`, commits, pushes, deploys to Cloud Run. The bad handler requires `webhook_secret` on every request → synthetic monitor gets HTTP 500.
- `demo.sh fix` restores `demo-scenarios/session_handler_good.py` → `session_handler.py`, commits, pushes, deploys.
- `session_handler_good.py` is the committed known-good baseline (no webhook_secret check).

### GitHub Actions (`rollback.yml`)
- `workflow_dispatch` with inputs: `rollback_to` (GOOD SHA — parent of bad commit), `incident_id`, `triggered_by`
- `trigger_github_rollback` tool passes BAD_SHA; tool auto-fetches `parents[0].sha` via GitHub API and sets `rollback_to` — agent never guesses the parent
- Authenticates via `GCP_SA_KEY` secret → configures Docker for GCR → `docker build` + `docker push` → `gcloud run deploy` → health check
- Health check verifies **both** `/health` AND `POST /voice-agent/session/start` — a 500 on session/start fails the workflow
- Requires `GCP_SA_KEY` and `DYNATRACE_PLATFORM_TOKEN` as repository secrets
- SA needs: `roles/run.developer`, `roles/artifactregistry.writer`, `roles/iam.serviceAccountUser` (on compute SA)

---

## What's Been Tested

- ✅ Dynatrace Davis AI problem detection (P-26061, P-26063 AVAILABILITY)
- ✅ Commit correlation + confidence scoring (HIGH, MEDIUM, LOW paths)
- ✅ VAPI outbound phone call (Twilio as provider)
- ✅ Voice approval — saying "approve" / "do it" on call triggers VAPI server-side tool → dispatches rollback
- ✅ GitHub Actions rollback — full Docker build → push to GCR → Cloud Run deploy → dual health check (`/health` + `session/start`)
- ✅ GitHub issue creation + close (post-incident report)
- ✅ Phase 2 RCA: DQL signal analysis, anomaly detector, docs lookup, impact calculation
- ✅ Full Phase 1 → Phase 2 end-to-end loop (verified session 20, Jun 2026)
- ✅ Bidirectional VAPI voice — ADK pushes updates via `send_voice_update` (VAPI Live Call Control `say` command), operator pulls via `get_incident_status` server-side tool
- ✅ Proactive mid-call voice updates without operator prompting (rollback triggered, rollback done, RCA complete, incident closed)
- ✅ Auto BAD→GOOD SHA resolution — `trigger_github_rollback` calls GitHub API `parents[0].sha`, no LLM guessing
- ✅ Stale approval/incident state isolation — `update_incident_state` called before `place_voice_call` to reset prior session state
- ✅ No raw SHAs, timestamps, or entity IDs in voice messages (RULES enforced in agent instruction)

---

## What's Next

### Must-do before submission (Jun 11)

- [ ] **Demo video** (3 min max) — required for hackathon submission
  - Show: broken target → Davis AI problem → agent detects → VAPI call → proactive voice updates → voice approval → rollback → resolved → post-incident report on GitHub
- [ ] **Submit hackathon form**

### Nice-to-have (if time allows)

- [ ] **Inbound call support** — operator hangs up mid-incident, calls back on same number, VoiceOps resumes exactly where it left off (needs `GET /operator/{phone}/context` endpoint + VAPI inbound config)
- [ ] Session data auto-export — save agent session JSON to `agent/test-data/` automatically after each run
- [ ] Slack/webhook notification when agent starts incident response
- [ ] Multi-incident handling (currently picks highest severity, ignores others)

---

## Key Technical Notes

- **Dynatrace token type**: Must be `dt0s16.*` platform token with `Bearer` auth to `apps.dynatrace.com` — NOT `Api-Token` to `live.dynatrace.com`
- **MCP tool names**: `tool_filter` uses hyphens (`query-problems`); agent instruction references them with underscores (`query_problems`) — ADK converts automatically
- **`adk web`**: Must be run from parent directory as `adk web agent` — not from inside `agent/`
- **Cloud Run agent SA**: Needs `roles/aiplatform.user` on `224808509436-compute@developer.gserviceaccount.com`
- **DQL syntax**: `summarize cnt=count(), by:{field.name}` — comma before `by`, field in braces
- **VAPI phone number**: Use Twilio as the provider in VAPI — had reliability issues with the default VAPI number
- **VAPI AMD**: Disable Answering Machine Detection on the VAPI assistant — it was causing unreliable call delivery
- **VAPI webhook structure**: All events are wrapped under `message` key. `message.call.metadata` holds `incident_id` and `approval_id`. `message.transcript` holds end-of-call transcript.
- **VAPI tool response format**: Must return `{"results": [{"toolCallId": "...", "result": "..."}]}` — plural `results` array with toolCallId echoed. Singular `{"result":"..."}` causes "No result returned" in VAPI's LLM.
- **VAPI toolCallId location**: `message.toolCallList[0].id` in the webhook body (not `message.functionCall.id`).
- **VAPI Live Call Control**: `send_voice_update` POSTs to `monitor.controlUrl` from the call creation response (per-call WebSocket URL). Body: `{"type":"say","content":"...","endCallAfterSpoken":false}`. NOT a constructed REST path.
- **Approval flow ordering**: For MEDIUM/LOW, create the approval request FIRST → get `approval_id` → pass to `place_voice_call`. This ensures the approval exists when the webhook fires during the call.
- **Stale state between sessions**: Call `update_incident_state` with "Triage in progress" BEFORE `place_voice_call` so VAPI reads fresh state, not the prior session's "All done. Incident closed." message.
- **trigger_github_rollback**: Agent passes BAD_SHA. Tool auto-fetches `parents[0].sha` via `GET /repos/{owner}/{repo}/commits/{sha}` → passes that as `rollback_to`. Eliminates LLM confusion when multiple commits share the same message.
- **GitHub Actions rollback**: `rollback.yml` requires full 40-char SHA — short SHAs fail `actions/checkout@v4` checkout. `get_recent_github_commits` returns full SHAs.
- **gcr.io + Artifact Registry**: `gcr.io` is backed by Artifact Registry — `roles/storage.admin` alone is insufficient; `roles/artifactregistry.writer` is required for push.
- **Dynatrace timing**: After rollback deploys, Dynatrace takes 3–5 minutes to re-run synthetic monitors and auto-close the problem. Agent proceeds to Phase 2 regardless.
- **demo.sh gcloud on Windows**: Use bare `gcloud` (PATH lookup) — resolving the full path produces `C:\Users\HP\AppData\Local\Google\Cloud SDK\...` with a space, which breaks bash word-splitting.
