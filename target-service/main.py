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
    return {
        "status": "degraded" if BROKEN else "ok",
        "broken": BROKEN,
        "failure_mode": FAILURE_MODE or None,
    }


@app.post("/voice-agent/session/start")
async def session_start(request: Request):
    body = await request.json()
    with tracer.start_as_current_span("voice-agent.session.start") as span:
        span.set_attribute("session_id", body.get("session_id", "unknown"))

        # Non-crash env-var-based failure modes for demo variety
        if BROKEN and FAILURE_MODE == "slow":
            delay = random.uniform(8.0, 15.0)
            span.set_attribute("error", True)
            span.set_attribute("error.type", "RequestTimeout")
            time.sleep(delay)
            raise HTTPException(status_code=504, detail={
                "error": "GatewayTimeout",
                "message": f"Request timed out after {delay:.1f}s",
                "code": "UPSTREAM_TIMEOUT",
            })

        if BROKEN and FAILURE_MODE == "auth_error":
            span.set_attribute("error", True)
            span.set_attribute("error.type", "AuthenticationError")
            raise HTTPException(status_code=401, detail={
                "error": "Unauthorized",
                "message": "JWT token validation failed: token signature verification error",
                "code": "INVALID_TOKEN",
            })

        if BROKEN and FAILURE_MODE == "db_timeout":
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

        if BROKEN and FAILURE_MODE == "dependency":
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
