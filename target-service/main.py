import os
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

# --- Config ---
BROKEN = os.getenv("BROKEN", "false").lower() == "true"
DT_TOKEN = os.getenv("DYNATRACE_PLATFORM_TOKEN", "")
DT_ENV_URL = os.getenv("DT_ENVIRONMENT_URL", "https://pmn17776.live.dynatrace.com")
DT_OTLP_ENDPOINT = f"{DT_ENV_URL.rstrip('/')}/api/v2/otlp/v1/traces"

# --- OpenTelemetry setup ---
provider = TracerProvider()
if DT_TOKEN:
    exporter = OTLPSpanExporter(
        endpoint=DT_OTLP_ENDPOINT,
        headers={"Authorization": f"Api-Token {DT_TOKEN}"},
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# --- App ---
app = FastAPI(title="VoiceOps Target Service")
FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


@app.get("/health")
async def health():
    return {"status": "degraded" if BROKEN else "ok", "broken": BROKEN}


@app.post("/voice-agent/session/start")
async def session_start(request: Request):
    body = await request.json()
    with tracer.start_as_current_span("voice-agent.session.start") as span:
        span.set_attribute("broken", BROKEN)
        if BROKEN and "webhook_secret" not in body:
            span.set_attribute("error", True)
            span.set_attribute("error.message", "Missing webhook_secret in payload")
            raise HTTPException(
                status_code=500,
                detail="Internal Server Error: missing required field 'webhook_secret'",
            )
        session_id = body.get("session_id", "unknown")
        span.set_attribute("session_id", session_id)
        return {"status": "started", "session_id": session_id, "broken": BROKEN}
