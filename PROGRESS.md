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
- 8 custom Python tools (GitHub, TTS, approval, rollback)

### Workflow (current — mitigate-first design)

**Phase 1 — Triage & Mitigate**
1. **DETECT** — `query_problems` + `get_problem_by_id` + `get_entity_name`
2. **QUICK TRIAGE** — `get_recent_github_commits(limit=10)`, find commits within 60 min before incident → confidence level (HIGH / MEDIUM / LOW)
3. **ALERT BRIEF** — `generate_voice_briefing` → Google Cloud TTS Neural2-D MP3
4. **GATE & ROLLBACK** — HIGH confidence → auto-rollback; MEDIUM/LOW → human approval via browser dashboard → `trigger_github_rollback` → `get_github_workflow_status` (polls to completion) → `query_problems` (confirm resolved)

**Phase 2 — Post-Incident (runs only after service is restored)**

5. **ROOT CAUSE ANALYSIS** — `create_dql` + `execute_dql` (error rate/5xx spike), `adaptive_anomaly_detector`, `ask_dynatrace_docs`, `find_troubleshooting_guides`
6. **IMPACT ANALYSIS** — failed request count, blast radius, trend at time of detection
7. **CLOSE** — resolution `generate_voice_briefing`, `create_github_issue` (post-incident report), `close_github_issue`

### Approval Server (`approval-server/main.py`)
- FastAPI with browser dashboard at `/`
- Approve/reject via UI buttons or REST API
- Confidence badges (HIGH=green, MEDIUM=amber, LOW=red)
- Auto-refreshes every 5 seconds
- Routes: `/approval/request`, `/approve/{id}`, `/reject/{id}`, `/incident/{id}/approve`, `/incident/{id}/reject`

### Target Service (`target-service/main.py`)
- `BROKEN=true` env var → `POST /voice-agent/session/start` returns HTTP 500
- OTel traces → Dynatrace via Bearer token
- Dynatrace Synthetic HTTP Monitor fires every 1 min from GCP Iowa → triggers Davis AI problem P-26061 (AVAILABILITY) when broken

### GitHub Actions (`rollback.yml`)
- `workflow_dispatch` with inputs: `rollback_to` (sha), `incident_id`, `triggered_by`
- Checks out target commit, builds target-service Docker image, health checks it

---

## What's Been Tested

- ✅ Dynatrace Davis AI problem detection (P-26061 AVAILABILITY)
- ✅ Commit correlation + confidence scoring
- ✅ GitHub issue creation (issue #2 created)
- ✅ Google Cloud TTS voice briefing (261KB MP3 generated)
- ✅ Human approval request + manual approval via API
- ✅ Rollback workflow dispatch
- ⚠️ Full Phase 1 → Phase 2 end-to-end loop **not yet tested** with new mitigate-first order

---

## What's Next

### Must-do before submission (Jun 11)

- [ ] **Redeploy agent** to Cloud Run with the new mitigate-first workflow
- [ ] **Full end-to-end test** — set `BROKEN=true`, let Davis AI fire, run agent, confirm: rollback → DT problem closed → RCA runs → issue closed
- [ ] **Demo video** (3 min max) — required for hackathon submission
  - Show: broken target → Davis AI problem → agent detects → voice brief plays → approval dashboard → rollback → verified resolved → post-incident report
- [ ] **Submit hackathon form**

### Wolkop's integration

- [ ] **Phone call on incident** — call operator's phone number when problem is detected (fires at Step 3, alongside the MP3 briefing)
  - Recommended: Twilio `POST /Calls` with TwiML reading the briefing text
  - Add as `call_operator(phone_number, briefing_text)` tool in `tools.py`
  - Phone number should come from an env var (`OPERATOR_PHONE_NUMBER`)
  - The MP3 stays as an audit record even when live call is added

### Nice-to-have (if time allows)

- [ ] Slack/webhook notification when agent starts incident response
- [ ] Multi-incident handling (currently picks highest severity, ignores others)
- [ ] Configurable rollback target (currently always rolls back to commit before incident)

---

## Key Technical Notes

- **Dynatrace token type**: Must be `dt0s16.*` platform token with `Bearer` auth to `apps.dynatrace.com` — NOT `Api-Token` to `live.dynatrace.com`
- **MCP tool names**: `tool_filter` uses hyphens (`query-problems`); agent instruction references them with underscores (`query_problems`) — ADK converts automatically
- **TTS voice**: `en-US-Neural2-D` requires `SsmlVoiceGender.MALE` (not NEUTRAL — that throws a 400)
- **`adk web`**: Must be run from parent directory as `adk web agent` — not from inside `agent/`
- **Cloud Run agent SA**: Needs `roles/aiplatform.user` on `224808509436-compute@developer.gserviceaccount.com`
- **DQL syntax**: `summarize cnt=count(), by:{field.name}` — comma before `by`, field in braces
