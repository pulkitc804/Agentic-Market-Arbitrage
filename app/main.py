"""
FastAPI application entry point for the AI agent API gateway.

Run with uvicorn from the project root, e.g.:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import settings

# Paths that skip the x402 payment gate (public probes and API documentation).
_X402_EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/docs", "/openapi.json"})

# --- FastAPI application instance ---
# ``settings`` drives title and optional future behavior (debug banners, etc.).
app = FastAPI(
    title=settings.app_name,
    description="High-performance API gateway skeleton for AI agents.",
    version="0.1.0",
    debug=settings.debug,
)

# --- CORS ---
# Allowing all origins is intentional for early agent integration.
# Tighten ``allow_origins`` to explicit domains before production traffic.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # Browsers forbid credentials together with wildcard ``*`` origins.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers ---
# All HTTP routes (``/health``, ``/v1/clean-data``, …) live on ``router`` in ``routes.py``.
app.include_router(router)


@app.middleware("http")
async def x402_payment_middleware(request: Request, call_next):
    """
    x402-style payment gate for agent traffic.

    Unpaid requests receive HTTP 402 with dummy settlement metadata so callers
    (for example autonomous agents) know which wallet and price to use. In a
    full implementation you would verify the ``payment-signature`` payload here;
    for now its presence is treated as proof of intent to pay.
    """
    path: str = request.url.path

    # Public surface: liveness and OpenAPI/Swagger must stay reachable without a header.
    if path in _X402_EXEMPT_PATHS:
        return await call_next(request)

    # Starlette header lookup is case-insensitive; we normalize to the canonical name.
    payment_signature: str | None = request.headers.get("payment-signature")
    if payment_signature is None or not payment_signature.strip():
        return JSONResponse(
            status_code=402,
            content={
                "error": "Payment required",
                "x402_wallet_address": "0xFakeWalletAddress123",
                "x402_price": "0.02 USDC",
            },
        )

    return await call_next(request)
