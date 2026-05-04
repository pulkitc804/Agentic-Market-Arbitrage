"""
HTTP route definitions for the gateway.

Keep this module focused on wiring paths to handlers; heavier logic should live
in dedicated services/modules as the codebase grows.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# One router for all HTTP routes: avoids missing a second ``include_router`` in
# ``main.py`` (a common cause of 404s when versioned routes live on a sub-router).
router = APIRouter(tags=["gateway"])

# x402-style payment hint encoded for the ``PAYMENT-REQUIRED`` response header (Base64 JSON).
_X402_PAYMENT_METADATA: dict[str, str] = {
    "price": "0.01",
    "asset": "USDC",
    "network": "base",
    "address": "0x0000000000000000000000000000000000000000",
}


def require_payment_signature(
    payment_signature: Annotated[str | None, Header(alias="payment-signature")] = None,
) -> None:
    """
    Gate paid tool routes: require a non-empty ``payment-signature`` header.

    If it is missing, respond with **HTTP 402 Payment Required** and a
    ``PAYMENT-REQUIRED`` header whose value is Base64-encoded JSON describing how to pay.
    """
    if payment_signature is None or not payment_signature.strip():
        payload = json.dumps(_X402_PAYMENT_METADATA, separators=(",", ":"))
        b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        raise HTTPException(
            status_code=402,
            detail="Payment Required",
            headers={"PAYMENT-REQUIRED": b64},
        )
    # Simulated settlement tick for the admin dashboard (see ``app.main.record_payment_verified``).
    from app.main import record_payment_verified

    record_payment_verified()

_MAX_RESPONSE_BYTES: int = 8 * 1024 * 1024  # 8 MiB
_HTTP_TIMEOUT: httpx.Timeout = httpx.Timeout(30.0, connect=10.0)
# Browser-like User-Agent reduces the chance origins return 403 to generic bots.
_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 AgenticMarketGateway/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _validate_http_url(url: str) -> str:
    """Return stripped URL or raise ``HTTPException(400)`` if unusable."""
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http or https.")
    if not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="URL is missing a host (e.g. https://example.com/path).",
        )
    return cleaned


class ScrapeRequest(BaseModel):
    """JSON request body for ``POST /v1/clean-data`` (parsed by FastAPI from the HTTP message)."""

    model_config = ConfigDict(extra="ignore")

    url: str = Field(
        ...,
        min_length=1,
        description="HTTP(S) URL pointing at the document or page to scrape.",
        examples=["https://example.com/article"],
    )


@router.get("/health")
def health_check() -> dict[str, Any]:
    """
    Liveness probe for orchestrators, load balancers, and local smoke tests.

    Returns a small JSON payload so callers do not need to parse headers only.
    """
    return {"status": "ok", "message": "Gateway is running"}


# Public catalog metadata for agent directories (e.g. agentic.market). No payment header required.
_DISCOVERY_DOCUMENT: dict[str, Any] = {
    "name": "Blaze Scrape-to-Arbitrage",
    "description": "Converts messy web data into structured arbitrage signals for AI agents.",
    "pricing": {"amount": "0.01", "asset": "USDC", "network": "base"},
    "category": "Data",
    "capabilities": ["web-scraping", "financial-analysis", "data-cleaning"],
}


@router.get("/v1/discovery")
def discovery() -> dict[str, Any]:
    """Structured service description for agent indexers."""
    return _DISCOVERY_DOCUMENT


def _http_exception_detail(exc: HTTPException) -> str:
    """Flatten ``HTTPException.detail`` for dashboard logging."""
    detail = exc.detail
    if isinstance(detail, str):
        return detail[:500]
    return str(detail)[:500]


async def _clean_data_scrape_and_arbitrage(body: ScrapeRequest) -> dict[str, Any]:
    """
    Core scrape + mock arbitrage response (raises ``HTTPException`` on failure).
    """
    target_url = _validate_http_url(body.url)

    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        ) as client:
            response = await client.get(target_url)

        # Treat non-2xx as "blocked or unavailable" for the scraper (agents get one status family).
        if response.status_code >= 400:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The website returned HTTP {response.status_code}. "
                    "The page may block automated clients or require authentication."
                ),
            )

        raw = response.content
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise HTTPException(
                status_code=400,
                detail="The page is too large to scrape safely; try a smaller document or a direct article URL.",
            )

        html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style"]):
            tag.decompose()

        extracted_text = soup.get_text(separator="\n", strip=True)
        extracted_text = re.sub(r"\n{3,}", "\n\n", extracted_text).strip()

        if not extracted_text:
            raise HTTPException(
                status_code=400,
                detail="No readable text could be extracted (empty page or unsupported content).",
            )

    except HTTPException:
        raise
    except httpx.InvalidURL as exc:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {exc}") from exc
    except httpx.UnsupportedProtocol as exc:
        raise HTTPException(status_code=400, detail="URL must use http or https.") from exc
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not reach the server (DNS, refused connection, or TLS issue): {exc}",
        ) from exc
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=400,
            detail="The request timed out while fetching the URL; the site may be slow or blocking scrapers.",
        )
    except httpx.RequestError as exc:
        logger.warning("httpx error scraping %s: %s", target_url, exc)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch the URL: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error while scraping %s", target_url)
        raise HTTPException(
            status_code=400,
            detail=f"Unexpected error while processing the page: {exc}",
        ) from exc

    # TODO: Pass 'extracted_text' to the LLM (AWS/Azure) once credits are confirmed.

    logger.debug("Scraped %d characters from %s", len(extracted_text), target_url)

    return {
        "status": "success",
        "source_url": body.url,
        "arbitrage_opportunity": True,
        "confidence_score": 0.95,
        "action_recommendation": "BUY",
        "summary": (
            "The scraped data indicates a price discrepancy between Exchange A and Exchange B."
        ),
    }


@router.post("/v1/clean-data", dependencies=[Depends(require_payment_signature)])
async def clean_data(body: ScrapeRequest) -> dict[str, Any]:
    """
    Scrape ``body.url`` to validate the pipeline, then return a **mock arbitrage** payload
    shaped for downstream agent services (real scoring would consume ``extracted_text`` later).

    The JSON body is validated as ``ScrapeRequest`` (do not name this parameter ``request``—that
    name is reserved for Starlette's ``Request`` and breaks body parsing with Pydantic v2).

    Failures from bad URLs, network issues, or anti-bot responses are surfaced as **400**
    with a short explanation so agents can retry or change strategy.
    """
    from app.main import record_scrape_result

    log_url = body.url.strip()
    try:
        result = await _clean_data_scrape_and_arbitrage(body)
    except HTTPException as exc:
        record_scrape_result(url=log_url, status=_http_exception_detail(exc), success=False)
        raise
    record_scrape_result(url=log_url, status="success", success=True)
    return result
