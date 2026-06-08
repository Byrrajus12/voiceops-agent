"""Entry point: run the Incident Commander agent against a live prompt."""
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai.types import Content, Part  # noqa: E402

from agent.agent import root_agent  # noqa: E402


async def main(prompt: str) -> None:
    session_service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="voiceops", session_service=session_service)
    session = await session_service.create_session(app_name="voiceops", user_id="ops-team")

    message = Content(role="user", parts=[Part(text=prompt)])

    print(f"[voiceops] Starting incident commander workflow...\n{'─' * 60}")
    async for event in runner.run_async(
        user_id="ops-team",
        session_id=session.id,
        new_message=message,
    ):
        if not hasattr(event, "content") or not event.content:
            continue
        for part in event.content.parts:
            if hasattr(part, "text") and part.text:
                print(part.text, flush=True)
            elif hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                args = dict(fc.args) if fc.args else {}
                # Truncate long args for readability
                display = {k: (str(v)[:80] + "…" if len(str(v)) > 80 else v) for k, v in args.items()}
                print(f"\n→ TOOL CALL: {fc.name}({display})", flush=True)
            elif hasattr(part, "function_response") and part.function_response:
                fr = part.function_response
                resp = str(fr.response)[:200]
                print(f"← TOOL RESULT: {fr.name}: {resp}…" if len(str(fr.response)) > 200 else f"← TOOL RESULT: {fr.name}: {fr.response}", flush=True)
    print(f"{'─' * 60}\n[voiceops] Done.")


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Check for active incidents and run the full incident response workflow."
    )
    asyncio.run(main(prompt))
