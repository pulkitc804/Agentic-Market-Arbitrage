"""
HTTP route definitions for the gateway.

Keep this module focused on wiring paths to handlers; heavier logic should live
in dedicated services/modules as the codebase grows.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, OpenAIError, RateLimitError
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

# One router for all HTTP routes: avoids missing a second ``include_router`` in
# ``main.py`` (a common cause of 404s when versioned routes live on a sub-router).
router = APIRouter(tags=["gateway"])

# --- clean-data tuning ---
# Bound upstream fetch and model input so a single request cannot pull huge pages or blow token limits.
_MAX_RESPONSE_BYTES: int = 8 * 1024 * 1024  # 8 MiB
_MAX_TEXT_CHARS: int = 100_000
_HTTP_TIMEOUT: httpx.Timeout = httpx.Timeout(30.0, connect=10.0)

_CLEAN_DATA_SYSTEM_PROMPT: str = (
    'You are an elite data extraction agent. Extract the main Title and a summary of the Content '
    'from the provided text. Return ONLY valid JSON in this format: {"title": "...", "content": "..."}'
)

_LLM_MODEL: str = "gpt-4o-mini"

# Reuse one async client per worker (connection pooling); key rotates only on process restart.
_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    """Lazily build the OpenAI-compatible async client (supports custom ``base_url`` via env if set later)."""
    global _openai_client
    if not settings.api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="Server is not configured: set API_KEY in the environment or .env file.",
        )
    if _openai_client is None:
        kwargs: dict[str, Any] = {"api_key": settings.api_key.strip()}
        if settings.openai_base_url and settings.openai_base_url.strip():
            kwargs["base_url"] = settings.openai_base_url.strip()
        _openai_client = AsyncOpenAI(**kwargs)
    return _openai_client


def _validate_http_url(url: str) -> None:
    """Reject obviously bad URLs before opening sockets."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="URL must use http or https.",
        )
    if not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="URL is missing a host (e.g. https://example.com/path).",
        )


async def _fetch_page_text(url: str) -> str:
    """GET the page and return plain text with markup/scripts removed."""
    headers = {
        "User-Agent": "AgenticMarketGateway/1.0 (+https://example.invalid; data-cleaning)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.InvalidURL as exc:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {exc}") from exc
    except httpx.UnsupportedProtocol as exc:
        raise HTTPException(status_code=400, detail="URL must use http or https.") from exc
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=400, detail=f"Could not connect to the server: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=408, detail="Timed out while fetching the URL.") from exc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        raise HTTPException(
            status_code=502,
            detail=f"The URL returned HTTP {code} from the origin server.",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning("httpx request error for %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {exc}") from exc

    raw = response.content
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Page is larger than {_MAX_RESPONSE_BYTES} bytes; refusing to process.",
        )

    # Decode as UTF-8 with replacement so BeautifulSoup always gets a str.
    html = raw.decode("utf-8", errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if not text:
        raise HTTPException(
            status_code=422,
            detail="No readable text could be extracted from the page (empty document or unsupported format).",
        )

    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS]

    return text


async def _extract_title_and_content_with_llm(page_text: str) -> dict[str, str]:
    """Call gpt-4o-mini and parse strict JSON ``title`` / ``content``."""
    client = _get_openai_client()

    try:
        completion = await client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": _CLEAN_DATA_SYSTEM_PROMPT},
                {"role": "user", "content": page_text},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
    except RateLimitError as exc:
        logger.warning("OpenAI rate limit: %s", exc)
        raise HTTPException(
            status_code=429,
            detail="The language model is rate-limited; try again shortly.",
        ) from exc
    except APIConnectionError as exc:
        logger.warning("OpenAI connection error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Could not reach the language model API.",
        ) from exc
    except APIStatusError as exc:
        logger.warning("OpenAI API status error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Language model API error: {exc.message}",
        ) from exc
    except OpenAIError as exc:
        logger.exception("OpenAI client error")
        raise HTTPException(
            status_code=502,
            detail=f"Language model request failed: {exc}",
        ) from exc

    message = completion.choices[0].message
    raw_content = message.content
    if not raw_content or not raw_content.strip():
        raise HTTPException(status_code=502, detail="Language model returned an empty response.")

    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        logger.warning("LLM JSON decode error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Language model returned invalid JSON.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Language model JSON was not an object.")

    title = payload.get("title")
    content = payload.get("content")
    if not isinstance(title, str) or not isinstance(content, str):
        raise HTTPException(
            status_code=502,
            detail='Language model JSON must contain string fields "title" and "content".',
        )

    return {"title": title.strip(), "content": content.strip()}


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
    Fetch a URL, strip HTML to text, run ``gpt-4o-mini`` extraction, return structured JSON.

    Requires ``API_KEY`` for the OpenAI-compatible API. Upstream fetch and LLM errors map to
    4xx/5xx with clear ``detail`` messages.
    """
    _validate_http_url(request.url)

    page_text = await _fetch_page_text(request.url.strip())
    cleaned = await _extract_title_and_content_with_llm(page_text)

    return {
        "status": "success",
        "source_url": request.url,
        "cleaned_data": cleaned,
    }
