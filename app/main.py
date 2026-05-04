"""
FastAPI application entry point for the AI agent API gateway.

Run with uvicorn from the project root, e.g.:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.templating import Jinja2Templates

from app.api.routes import router
from app.core.config import settings

# --- Admin dashboard metrics (process-local; reset on server restart) ---
# Revenue: +$0.01 (one cent) each time ``payment-signature`` is accepted on a gated route.
_revenue_cents: int = 0
_successful_scrapes: int = 0
_recent_scrape_logs: deque[dict[str, str]] = deque(maxlen=5)


def record_payment_verified() -> None:
    """Increment simulated revenue when a client presents a valid payment signature."""
    global _revenue_cents
    _revenue_cents += 1


def record_scrape_result(*, url: str, status: str, success: bool) -> None:
    """Push a row into the rolling log; bump the success counter only on completed scrapes."""
    global _successful_scrapes
    _recent_scrape_logs.appendleft({"url": url, "status": status})
    if success:
        _successful_scrapes += 1


def get_dashboard_view_model() -> dict[str, object]:
    """Template context for ``GET /dashboard``."""
    return {
        "total_revenue": f"${_revenue_cents / 100:.2f}",
        "requests_processed": _successful_scrapes,
        "recent_logs": list(_recent_scrape_logs),
    }


# --- FastAPI application instance ---
app = FastAPI(
    title=settings.app_name,
    description="High-performance API gateway skeleton for AI agents.",
    version="0.1.0",
    debug=settings.debug,
)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["PAYMENT-REQUIRED"],
)

app.include_router(router)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@app.get("/dashboard", include_in_schema=False)
def admin_dashboard(request: Request):
    """HTML admin view for demos (not part of the public JSON API surface)."""
    ctx = get_dashboard_view_model()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "total_revenue": ctx["total_revenue"],
            "requests_processed": ctx["requests_processed"],
            "recent_logs": ctx["recent_logs"],
        },
    )
