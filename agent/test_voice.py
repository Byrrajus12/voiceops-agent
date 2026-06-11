"""Manual VAPI smoke test for VoiceOps.

This script first creates a pending approval so the webhook can resolve the
incident by `incident_id`, then starts an outbound VAPI phone call.
"""
import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv()


async def test() -> None:
    approval_server = os.getenv("APPROVAL_SERVER_URL", "http://localhost:9000")
    incident_id = os.getenv("TEST_INCIDENT_ID", "TEST-001")
    operator_number = os.getenv("YOUR_PHONE_NUMBER") or os.getenv("VAPI_CALLER_NUMBER")

    if not operator_number:
        raise RuntimeError("Set YOUR_PHONE_NUMBER or VAPI_CALLER_NUMBER in .env")

    async with httpx.AsyncClient(timeout=30.0) as client:
        pending_payload = {
            "incident_id": incident_id,
            "action": "rollback confirmation call",
            "summary": "Test VAPI call: confirm the approval and webhook flow.",
            "risk_level": "high",
            "confidence": "MEDIUM",
        }

        pending_response = await client.post(f"{approval_server}/approval/request", json=pending_payload)

        print("APPROVAL SERVER:", approval_server)
        print("PENDING STATUS CODE:", pending_response.status_code)
        print("PENDING RAW RESPONSE:", pending_response.text)

        if pending_response.status_code not in (200, 201):
            raise RuntimeError(f"Approval request failed: {pending_response.status_code} {pending_response.text}")

        # Realistic incident context — same format the ADK agent passes at call time.
        # Edit this to match a real problem if you have one open in Dynatrace.
        test_incident_context = (
            "Incident P-99999 — HIGH severity on checkout-service since 02:46 UTC. "
            "Suspect commit: abc123def456 by sai, 4 min before incident. Confidence: HIGH. "
            "Rolling back to safe parent commit."
        )

        call_response = await client.post(
            "https://api.vapi.ai/call/phone",
            headers={
                "Authorization": f"Bearer {os.getenv('VAPI_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "phoneNumberId": os.getenv("VAPI_PHONE_NUMBER_ID"),
                "assistantId": os.getenv("VAPI_ASSISTANT_ID"),
                "customer": {"number": operator_number},
                "assistantOverrides": {
                    "firstMessage": "Hey, VoiceOps here.",
                    "variableValues": {"incident_context": test_incident_context},
                },
                "metadata": {"incident_id": incident_id},
            },
        )
        print("CALL STATUS CODE:", call_response.status_code)
        print("CALL RAW RESPONSE:", call_response.text)


asyncio.run(test())