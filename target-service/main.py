import os
import random
import time
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import session_handler

# BROKEN + FAILURE_MODE are only used for non-crash demo modes (slow, auth_error, etc.)
# The crash scenario is triggered by deploying demo-scenarios/crash_bad.py as session_handler.py
BROKEN = os.getenv("BROKEN", "false").lower() == "true"
FAILURE_MODE = os.getenv("FAILURE_MODE", "")

# Runtime demo flags — changed via /demo/break and /demo/fix without redeploying
_demo_broken: bool = False
_demo_failure_mode: str = "crash"

_VALID_MODES = ["crash", "slow", "auth_error", "db_timeout", "dependency"]

DT_TOKEN = os.getenv("DYNATRACE_PLATFORM_TOKEN", "")
DT_ENV_URL = os.getenv("DT_ENVIRONMENT_URL", "https://pmn17776.apps.dynatrace.com")
DT_OTLP_ENDPOINT = f"{DT_ENV_URL.rstrip('/')}/api/v2/otlp/v1/traces"

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


@app.get("/health")
async def health():
    broken = BROKEN or _demo_broken
    mode = _demo_failure_mode if _demo_broken else (FAILURE_MODE or None)
    return {"status": "degraded" if broken else "ok", "broken": broken, "failure_mode": mode}


@app.post("/demo/break")
async def demo_break(request: Request):
    """Runtime break — triggers failure mode in memory without redeploying.
    Dynatrace Synthetic Monitor will detect HTTP 500s and fire a Davis AI problem in ~2-3 min.
    POST {"mode": "crash"}  (modes: crash | slow | auth_error | db_timeout | dependency)
    """
    global _demo_broken, _demo_failure_mode
    body = await request.json()
    mode = body.get("mode", "crash")
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of: {_VALID_MODES}")
    _demo_broken = True
    _demo_failure_mode = mode
    return {"status": "broken", "mode": _demo_failure_mode}


@app.post("/demo/fix")
async def demo_fix():
    """Runtime fix — clears the in-memory failure mode."""
    global _demo_broken, _demo_failure_mode
    _demo_broken = False
    _demo_failure_mode = "crash"
    return {"status": "healthy"}


@app.post("/voice-agent/session/start")
async def session_start(request: Request):
    body = await request.json()
    with tracer.start_as_current_span("voice-agent.session.start") as span:
        span.set_attribute("session_id", body.get("session_id", "unknown"))

        # Runtime demo break takes precedence over env vars
        active_broken = BROKEN or _demo_broken
        active_mode = _demo_failure_mode if _demo_broken else FAILURE_MODE

        if _demo_broken and active_mode == "crash":
            span.set_attribute("error", True)
            span.set_attribute("error.type", "ValueError")
            raise HTTPException(status_code=500, detail={
                "error": "InternalServerError",
                "message": "webhook_secret is required for session authentication",
                "code": "SESSION_START_FAILED",
            })

        # Non-crash env-var-based failure modes for demo variety
        if active_broken and active_mode == "slow":
            delay = random.uniform(8.0, 15.0)
            span.set_attribute("error", True)
            span.set_attribute("error.type", "RequestTimeout")
            time.sleep(delay)
            raise HTTPException(status_code=504, detail={
                "error": "GatewayTimeout",
                "message": f"Request timed out after {delay:.1f}s",
                "code": "UPSTREAM_TIMEOUT",
            })

        if active_broken and active_mode == "auth_error":
            span.set_attribute("error", True)
            span.set_attribute("error.type", "AuthenticationError")
            raise HTTPException(status_code=401, detail={
                "error": "Unauthorized",
                "message": "JWT token validation failed: token signature verification error",
                "code": "INVALID_TOKEN",
            })

        if active_broken and active_mode == "db_timeout":
            span.set_attribute("error", True)
            span.set_attribute("error.type", "DatabaseConnectionError")
            raise HTTPException(status_code=503, detail={
                "error": "ServiceUnavailable",
                "message": "Database connection pool exhausted (max=10, active=10)",
                "code": "DB_POOL_EXHAUSTED",
                "stack_trace": (
                    "psycopg2.pool.PoolError: connection pool exhausted\n"
                    "  at SessionRepository.create (session_handler.py:34)\n"
                    "  at POST /voice-agent/session/start (main.py:58)"
                ),
            })

        if active_broken and active_mode == "dependency":
            span.set_attribute("error", True)
            span.set_attribute("error.type", "DependencyUnavailable")
            raise HTTPException(status_code=502, detail={
                "error": "BadGateway",
                "message": "Downstream notification-service is unreachable",
                "code": "DEPENDENCY_UNAVAILABLE",
                "dependency": "notification-service",
            })

        # Normal path — session_handler does the real work
        try:
            result = session_handler.start_session(body)
            span.set_attribute("session_id", result["session_id"])
            return result
        except ValueError as e:
            span.set_attribute("error", True)
            span.set_attribute("error.type", "ValueError")
            span.set_attribute("error.message", str(e))
            raise HTTPException(status_code=500, detail={
                "error": "InternalServerError",
                "message": str(e),
                "code": "SESSION_START_FAILED",
            })


@app.post("/voice-agent/session/end")
async def session_end(request: Request):
    body = await request.json()
    with tracer.start_as_current_span("voice-agent.session.end") as span:
        session_id = body.get("session_id", "unknown")
        span.set_attribute("session_id", session_id)
        try:
            result = session_handler.end_session(session_id)
            return result
        except KeyError as e:
            raise HTTPException(status_code=404, detail={"error": "NotFound", "message": str(e)})


@app.get("/voice-agent/sessions")
async def list_sessions():
    with tracer.start_as_current_span("voice-agent.sessions.list"):
        return {"sessions": session_handler.get_sessions(), "count": len(session_handler.get_sessions())}
