import os
import random
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Checkout Service")

BROKEN = os.getenv("BROKEN", "false").lower() == "true"
FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.8"))


class CheckoutRequest(BaseModel):
    user_id: str
    items: list[str]
    total: float


@app.post("/checkout")
async def checkout(req: CheckoutRequest):
    if BROKEN and random.random() < FAILURE_RATE:
        raise HTTPException(
            status_code=500,
            detail="Internal Server Error: payment gateway timeout — upstream service unreachable",
        )
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    return {"status": "success", "order_id": order_id, "user_id": req.user_id, "total": req.total}


@app.get("/health")
async def health():
    return {"status": "degraded" if BROKEN else "ok", "broken": BROKEN, "failure_rate": FAILURE_RATE if BROKEN else 0}


@app.get("/")
async def root():
    return {"service": "checkout", "version": "1.2.3"}
