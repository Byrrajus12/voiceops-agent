import os
import random
import time
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

# --- Config ---
BROKEN = os.getenv("BROKEN", "false").lower() == "true"
# FAILURE_MODE controls the type of failure when BROKEN=true:
#   crash       - HTTP 500 MissingWebhookSecret (default, AVAILABILITY)
#   slow        - random 5-10s delay causing timeout (PERFORMANCE)
#   auth_error  - HTTP 401 invalid token (ERROR_RATE)
#   db_timeout  - fake DB connection timeout with stack trace (AVAILABILITY)
#   dependency  - downstream payment service unreachable (AVAILABILITY)
FAILURE_MODE = os.getenv("FAILURE_MODE", "crash")

DT_TOKEN = os.getenv("DYNATRACE_PLATFORM_TOKEN", "")
DT_ENV_URL = os.getenv("DT_ENVIRONMENT_URL", "https://pmn17776.apps.dynatrace.com")
DT_OTLP_ENDPOINT = f"{DT_ENV_URL.rstrip('/')}/api/v2/otlp/v1/traces"

# --- OpenTelemetry setup ---
provider = TracerProvider()
if DT_TOKEN:
    exporter = OTLPSpanExporter(
        endpoint=DT_OTLP_ENDPOINT,
        headers={"Authorization": f"Bearer {DT_TOKEN}"},
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

app = FastAPI(title="VoiceOps Target Service")
FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

# --- Failure implementations ---

def _fail_crash(span):
    """Missing required field — simulates a bad deploy that removed validation."""
    span.set_attribute("error", True)
    span.set_attribute("error.type", "MissingWebhookSecret")
    span.set_attribute("error.message", "Missing required field 'webhook_secret' in request body")
    span.set_attribute("http.status_code", 500)
    raise HTTPException(
        status_code=500,
        detail={
            "error": "InternalServerError",
            "message": "Missing required field 'webhook_secret' in request body",
            "code": "MISSING_WEBHOOK_SECRET",
            "trace_id": span.get_span_context().trace_id,
        },
    )

def _fail_slow(span):
    """Simulates a deadlock or blocking DB query causing request timeout."""
    delay = random.uniform(8.0, 15.0)
    span.set_attribute("error", True)
    span.set_attribute("error.type", "RequestTimeout")
    span.set_attribute("db.query.duration_ms", int(delay * 1000))
    time.sleep(delay)
    raise HTTPException(
        status_code=504,
        detail={
            "error": "GatewayTimeout",
            "message": f"Request timed out after {delay:.1f}s — upstream dependency did not respond",
            "code": "UPSTREAM_TIMEOUT",
        },
    )

def _fail_auth(span):
    """Simulates an expired or rotated auth token after a config deploy."""
    span.set_attribute("error", True)
    span.set_attribute("error.type", "AuthenticationError")
    span.set_attribute("http.status_code", 401)
    raise HTTPException(
        status_code=401,
        detail={
            "error": "Unauthorized",
            "message": "JWT token validation failed: token signature verification error",
            "code": "INVALID_TOKEN",
            "hint": "Token may have been rotated — check VOICE_AGENT_SECRET env var",
        },
    )

def _fail_db_timeout(span):
    """Simulates a DB connection pool exhaustion with a realistic stack trace."""
    span.set_attribute("error", True)
    span.set_attribute("error.type", "DatabaseConnectionError")
    span.set_attribute("db.system", "postgresql")
    span.set_attribute("db.connection_pool.size", 10)
    span.set_attribute("db.connection_pool.active", 10)
    span.set_attribute("http.status_code", 503)
    raise HTTPException(
        status_code=503,
        detail={
            "error": "ServiceUnavailable",
            "message": "Database connection pool exhausted (max=10, active=10)",
            "code": "DB_POOL_EXHAUSTED",
            "stack_trace": (
                "psycopg2.pool.PoolError: connection pool exhausted\n"
                "  at SessionRepository.create (session_repo.py:47)\n"
                "  at SessionService.start_session (session_service.py:23)\n"
                "  at POST /voice-agent/session/start (main.py:82)"
            ),
        },
    )

def _fail_dependency(span):
    """Simulates a downstream payment/notification service being unreachable."""
    span.set_attribute("error", True)
    span.set_attribute("error.type", "DependencyUnavailable")
    span.set_attribute("peer.service", "notification-service")
    span.set_attribute("http.status_code", 502)
    raise HTTPException(
        status_code=502,
        detail={
            "error": "BadGateway",
            "message": "Downstream notification-service is unreachable",
            "code": "DEPENDENCY_UNAVAILABLE",
            "dependency": "notification-service",
            "endpoint": "https://notification-svc.internal/send",
            "last_error": "ConnectionRefusedError: [Errno 111] Connection refused",
        },
    )

_FAILURE_HANDLERS = {
    "crash": _fail_crash,
    "slow": _fail_slow,
    "auth_error": _fail_auth,
    "db_timeout": _fail_db_timeout,
    "dependency": _fail_dependency,
}

# --- Routes ---

@app.get("/health")
async def health():
    return {
        "status": "degraded" if BROKEN else "ok",
        "broken": BROKEN,
        "failure_mode": FAILURE_MODE if BROKEN else None,
    }

@app.post("/voice-agent/session/start")
async def session_start(request: Request):
    body = await request.json()
    with tracer.start_as_current_span("voice-agent.session.start") as span:
        span.set_attribute("broken", BROKEN)
        span.set_attribute("failure_mode", FAILURE_MODE)
        span.set_attribute("session_id", body.get("session_id", "unknown"))

        if BROKEN:
            handler = _FAILURE_HANDLERS.get(FAILURE_MODE, _fail_crash)
            handler(span)

        session_id = body.get("session_id", "unknown")
        span.set_attribute("session_id", session_id)
        return {"status": "started", "session_id": session_id, "broken": False}

@app.post("/voice-agent/session/end")
async def session_end(request: Request):
    body = await request.json()
    with tracer.start_as_current_span("voice-agent.session.end") as span:
        session_id = body.get("session_id", "unknown")
        span.set_attribute("session_id", session_id)
        if BROKEN and FAILURE_MODE == "auth_error":
            _fail_auth(span)
        return {"status": "ended", "session_id": session_id}

@app.get("/voice-agent/sessions")
async def list_sessions():
    with tracer.start_as_current_span("voice-agent.sessions.list") as span:
        if BROKEN and FAILURE_MODE in ("db_timeout", "dependency"):
            handler = _FAILURE_HANDLERS[FAILURE_MODE]
            handler(span)
        return {"sessions": [], "count": 0}
