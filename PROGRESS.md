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
7. **CLOSE** — resolution `place_voice_call`, `create_github_issue` (post-incident report), `close_github_issue`

### Approval Server (`approval-server/main.py`)
- FastAPI with browser dashboard at `/`
- Approve/reject via UI buttons, REST API, or VAPI phone call (say "approve" / press 1)
- Confidence badges (HIGH=green, MEDIUM=amber, LOW=red)
- Auto-refreshes every 5 seconds
- Routes: `/approval/request`, `/approve/{id}`, `/reject/{id}`, `/incident/{id}/approve`, `/incident/{id}/reject`, `/webhook/vapi`
- VAPI webhook parses `message.call.metadata` for `approval_id` — direct lookup bypasses incident_id scan

### Target Service (`target-service/main.py`)
- `BROKEN=true` env var → `POST /voice-agent/session/start` returns HTTP 500
- OTel traces → Dynatrace via Bearer token
- Dynatrace Synthetic HTTP Monitor fires every 1 min from GCP Iowa → triggers Davis AI problem P-26061 (AVAILABILITY) when broken

### GitHub Actions (`rollback.yml`)
- `workflow_dispatch` with inputs: `rollback_to` (full SHA), `incident_id`, `triggered_by`
- Authenticates via `GCP_SA_KEY` secret → configures Docker for GCR → `docker build` + `docker push` → `gcloud run deploy` → health check
- Requires `GCP_SA_KEY` and `DYNATRACE_PLATFORM_TOKEN` as repository secrets
- SA needs: `roles/run.developer`, `roles/artifactregistry.writer`, `roles/iam.serviceAccountUser` (on compute SA)

---

## What's Been Tested

- ✅ Dynatrace Davis AI problem detection (P-26061, P-26063 AVAILABILITY)
- ✅ Commit correlation + confidence scoring (HIGH, MEDIUM, LOW paths)
- ✅ VAPI outbound phone call (Twilio as provider)
- ✅ Voice approval — saying "approve" on call triggers webhook → dispatches rollback
- ✅ GitHub Actions rollback — full Docker build → push to GCR → Cloud Run deploy → health check
- ✅ GitHub issue creation + close (post-incident report)
- ✅ Phase 2 RCA: DQL signal analysis, anomaly detector, docs lookup, impact calculation
- ✅ Full Phase 1 → Phase 2 end-to-end loop (session X / P-26063, Jun 2026)

---

## What's Next

### Must-do before submission (Jun 11)

- [ ] **Demo video** (3 min max) — required for hackathon submission
  - Show: broken target → Davis AI problem → agent detects → VAPI call → voice approval → rollback → resolved → post-incident report on GitHub
- [ ] **Submit hackathon form**

### Nice-to-have (if time allows)

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
- **Approval flow ordering**: For MEDIUM/LOW, create the approval request FIRST → get `approval_id` → pass to `place_voice_call`. This ensures the approval exists when the webhook fires during the call.
- **GitHub Actions rollback**: `rollback.yml` requires full 40-char SHA — short SHAs fail `actions/checkout@v4` checkout. `get_recent_github_commits` returns full SHAs.
- **gcr.io + Artifact Registry**: `gcr.io` is backed by Artifact Registry — `roles/storage.admin` alone is insufficient; `roles/artifactregistry.writer` is required for push.
- **Dynatrace timing**: After rollback deploys, Dynatrace takes 3–5 minutes to re-run synthetic monitors and auto-close the problem. Agent proceeds to Phase 2 regardless.
