#!/usr/bin/env bash
# VoiceOps Demo & Operations Script
# Usage: ./demo.sh <command>
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT=sreagnt
REGION=us-central1
TARGET_SVC=voiceops-target
AGENT_SVC=voiceops-agent
APPROVAL_SVC=voiceops-approval

AGENT_URL="https://voiceops-agent-224808509436.us-central1.run.app"
APPROVAL_URL="https://voiceops-approval-224808509436.us-central1.run.app"
TARGET_URL="https://voiceops-target-224808509436.us-central1.run.app"

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
RESET="\033[0m"

log()  { echo -e "${BOLD}$*${RESET}"; }
ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "${RED}✗${RESET}  $*"; }
info() { echo -e "${CYAN}→${RESET} $*"; }

# ── Commands ──────────────────────────────────────────────────────────────────

cmd_status() {
  log "\nService Status"
  echo "───────────────────────────────────────────────"

  for url_name in "$AGENT_URL:Agent" "$APPROVAL_URL:Approval" "$TARGET_URL:Target"; do
    url="${url_name%%:*}"
    name="${url_name##*:}"
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url/health" 2>/dev/null || echo "ERR")
    if [ "$code" = "200" ]; then
      ok "$name  →  $url"
    else
      err "$name  →  $url  (HTTP $code)"
    fi
  done

  echo ""
  info "Dynatrace Davis AI problems:"
  curl -s -X POST \
    "https://pmn17776.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp" \
    -H "Authorization: Bearer $(grep DYNATRACE_PLATFORM_TOKEN .env | cut -d= -f2-)" \
    -H "Content-Type: application/json" \
    -d '{"method":"tools/call","params":{"name":"query-problems","arguments":{}}}' 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d,indent=2))" 2>/dev/null \
    || warn "Could not reach Dynatrace MCP (check DYNATRACE_PLATFORM_TOKEN in .env)"
  echo ""
}

cmd_break() {
  log "\nBreaking target service (BROKEN=true)..."
  gcloud run services update "$TARGET_SVC" \
    --region "$REGION" \
    --project "$PROJECT" \
    --update-env-vars "BROKEN=true" \
    --quiet
  ok "Target service is now broken"
  info "Dynatrace Synthetic Monitor polls every ~1 min → Davis AI problem will fire in 2–3 min"
  info "Watch: $AGENT_URL"
  echo ""
  warn "Run the agent once a problem appears:"
  echo "  Prompt: 'Check for active incidents and run the full incident response workflow.'"
}

cmd_fix() {
  log "\nRestoring target service (BROKEN=false)..."
  gcloud run services update "$TARGET_SVC" \
    --region "$REGION" \
    --project "$PROJECT" \
    --update-env-vars "BROKEN=false" \
    --quiet
  ok "Target service restored"
  info "Dynatrace should close the problem within 1–2 min"
}

cmd_demo() {
  log "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log "  VoiceOps — Full Demo Flow"
  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  log "Step 1 — Check current service health"
  cmd_status

  log "Step 2 — Break the target service"
  cmd_break

  echo ""
  log "Step 3 — Wait for Dynatrace to detect the incident"
  info "Polling $TARGET_URL/health every 10s ..."
  for i in $(seq 1 18); do
    sleep 10
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$TARGET_URL/health" 2>/dev/null || echo "ERR")
    echo "  [${i}0s] Target health: HTTP $code"
  done
  warn "3 minutes elapsed. Davis AI problem should be active now."

  echo ""
  log "Step 4 — Run the agent"
  echo -e "  Open: ${CYAN}$AGENT_URL${RESET}"
  echo -e "  Prompt: ${BOLD}\"Check for active incidents and run the full incident response workflow.\"${RESET}"
  echo ""

  log "Step 5 — Approval (if MEDIUM/LOW confidence)"
  echo -e "  Dashboard: ${CYAN}$APPROVAL_URL${RESET}"
  echo -e "  Or approve by incident ID:"
  echo -e "    ${BOLD}./demo.sh approve <incident_id>${RESET}"
  echo ""

  log "Step 6 — Monitor rollback"
  echo -e "  GitHub Actions: ${CYAN}https://github.com/Byrrajus12/voiceops-agent/actions${RESET}"
  echo ""
}

cmd_approve() {
  local incident_id="${1:-}"
  if [ -z "$incident_id" ]; then
    err "Usage: ./demo.sh approve <incident_id>"
    exit 1
  fi
  log "\nApproving rollback for incident $incident_id ..."
  resp=$(curl -s -X POST "$APPROVAL_URL/incident/$incident_id/approve?reason=demo-approved")
  echo "$resp" | python3 -m json.tool 2>/dev/null || echo "$resp"
  ok "Approved"
}

cmd_reject() {
  local incident_id="${1:-}"
  if [ -z "$incident_id" ]; then
    err "Usage: ./demo.sh reject <incident_id>"
    exit 1
  fi
  log "\nRejecting rollback for incident $incident_id ..."
  resp=$(curl -s -X POST "$APPROVAL_URL/incident/$incident_id/reject?reason=demo-rejected")
  echo "$resp" | python3 -m json.tool 2>/dev/null || echo "$resp"
  ok "Rejected"
}

cmd_local() {
  log "\nStarting services locally..."

  # Check .env exists
  if [ ! -f .env ]; then
    err ".env not found — copy .env.example and fill in tokens"
    exit 1
  fi

  # Load env
  set -a; source .env; set +a

  # Approval server
  info "Starting approval server on :8080 ..."
  uvicorn approval-server.main:app --port 8080 --host 0.0.0.0 &
  APPROVAL_PID=$!
  echo "  PID $APPROVAL_PID"

  sleep 2

  # Target service (optional — broken or healthy)
  BROKEN_FLAG="${BROKEN:-false}"
  info "Starting target service on :9000 (BROKEN=$BROKEN_FLAG) ..."
  BROKEN=$BROKEN_FLAG uvicorn target-service.main:app --port 9000 --host 0.0.0.0 &
  TARGET_PID=$!
  echo "  PID $TARGET_PID"

  sleep 2

  # Agent
  info "Starting agent web UI on :8000 ..."
  APPROVAL_SERVER_URL=http://localhost:8080 \
  TARGET_SERVICE_URL=http://localhost:9000 \
  adk web agent &
  AGENT_PID=$!
  echo "  PID $AGENT_PID"

  echo ""
  ok "All services running:"
  echo "  Agent        →  http://localhost:8000"
  echo "  Approval UI  →  http://localhost:8080"
  echo "  Target       →  http://localhost:9000"
  echo ""
  echo "Press Ctrl+C to stop all services."

  trap "kill $APPROVAL_PID $TARGET_PID $AGENT_PID 2>/dev/null; echo 'Stopped.'" EXIT
  wait
}

cmd_logs() {
  local svc="${1:-$AGENT_SVC}"
  log "\nStreaming logs for $svc ..."
  gcloud run services logs read "$svc" \
    --region "$REGION" \
    --project "$PROJECT" \
    --limit 50
}

cmd_deploy() {
  local target="${1:-all}"
  log "\nDeploying $target ..."

  deploy_agent() {
    info "Building and deploying agent ..."
    gcloud builds submit . \
      --tag "gcr.io/$PROJECT/$AGENT_SVC:latest" \
      --project "$PROJECT"
    gcloud run deploy "$AGENT_SVC" \
      --image "gcr.io/$PROJECT/$AGENT_SVC:latest" \
      --region "$REGION" \
      --project "$PROJECT" \
      --quiet
    ok "Agent deployed → $AGENT_URL"
  }

  deploy_approval() {
    info "Building and deploying approval server ..."
    gcloud builds submit ./approval-server \
      --tag "gcr.io/$PROJECT/$APPROVAL_SVC:latest" \
      --project "$PROJECT"
    gcloud run deploy "$APPROVAL_SVC" \
      --image "gcr.io/$PROJECT/$APPROVAL_SVC:latest" \
      --region "$REGION" \
      --project "$PROJECT" \
      --quiet
    ok "Approval server deployed → $APPROVAL_URL"
  }

  deploy_target() {
    info "Building and deploying target service ..."
    gcloud builds submit ./target-service \
      --tag "gcr.io/$PROJECT/$TARGET_SVC:latest" \
      --project "$PROJECT"
    gcloud run deploy "$TARGET_SVC" \
      --image "gcr.io/$PROJECT/$TARGET_SVC:latest" \
      --region "$REGION" \
      --project "$PROJECT" \
      --set-env-vars "BROKEN=false" \
      --quiet
    ok "Target service deployed → $TARGET_URL"
  }

  case "$target" in
    agent)    deploy_agent ;;
    approval) deploy_approval ;;
    target)   deploy_target ;;
    all)      deploy_agent; deploy_approval; deploy_target ;;
    *)        err "Unknown target: $target. Use: agent | approval | target | all" ;;
  esac
}

cmd_help() {
  echo ""
  echo -e "${BOLD}VoiceOps Demo Script${RESET}"
  echo ""
  echo "Usage: ./demo.sh <command> [args]"
  echo ""
  echo "Demo commands:"
  echo "  demo                   Full guided demo flow (break → wait → run agent)"
  echo "  break                  Set target service BROKEN=true on Cloud Run"
  echo "  fix                    Set target service BROKEN=false on Cloud Run"
  echo "  approve <incident_id>  Approve a pending rollback via the approval server"
  echo "  reject  <incident_id>  Reject a pending rollback"
  echo ""
  echo "Operations:"
  echo "  status                 Check health of all 3 services + open DT problems"
  echo "  logs [agent|approval|target]  Tail Cloud Run logs (default: agent)"
  echo "  deploy [agent|approval|target|all]  Build and redeploy to Cloud Run"
  echo "  local                  Run all services locally (requires .env)"
  echo ""
}

# ── Entrypoint ────────────────────────────────────────────────────────────────
CMD="${1:-help}"
shift || true

case "$CMD" in
  demo)    cmd_demo ;;
  break)   cmd_break ;;
  fix)     cmd_fix ;;
  status)  cmd_status ;;
  approve) cmd_approve "${1:-}" ;;
  reject)  cmd_reject "${1:-}" ;;
  local)   cmd_local ;;
  logs)    cmd_logs "${1:-}" ;;
  deploy)  cmd_deploy "${1:-all}" ;;
  help|--help|-h) cmd_help ;;
  *)       err "Unknown command: $CMD"; cmd_help; exit 1 ;;
esac
