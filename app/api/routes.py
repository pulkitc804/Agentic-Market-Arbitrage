"""
HTTP route definitions for the gateway.

Keep this module focused on wiring paths to handlers; heavier logic should live
in dedicated services/modules as the codebase grows.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# One router for all HTTP routes: avoids missing a second ``include_router`` in
# ``main.py`` (a common cause of 404s when versioned routes live on a sub-router).
router = APIRouter(tags=["gateway"])

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


class CleanDataRequest(BaseModel):
    """Inbound body for the clean-data tool: a single source URL to process."""

    url: str = Field(
        ...,
        description="HTTP(S) URL pointing at the document or page to normalize.",
        examples=["https://example.com/article"],
    )


@router.get("/health")
def health_check() -> dict[str, Any]:
    """
    Liveness probe for orchestrators, load balancers, and local smoke tests.

    Returns a small JSON payload so callers do not need to parse headers only.
    """
    return {"status": "ok", "message": "Gateway is running"}


@router.post("/v1/clean-data")
async def clean_data(request: CleanDataRequest) -> dict[str, Any]:
    """
    Scrape ``request.url``, strip markup to plain text, return a mock extraction payload.

    Failures from bad URLs, network issues, or anti-bot responses are surfaced as **400**
    with a short explanation so agents can retry or change strategy.
    """
    target_url = _validate_http_url(request.url)

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

    preview = extracted_text[:500]

    return {
        "status": "success",
        "source_url": request.url,
        "cleaned_data": {
            "title": "Dummy Title",
            "content": preview,
        },
    }
