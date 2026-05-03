"""
Application configuration loaded from environment variables.

Uses Pydantic Settings so values are validated, typed, and documented in one place.
A local ``.env`` file is optional for development; production should use real env vars.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central settings for the API gateway.

    Subclassing ``BaseSettings`` means each field can be populated from:
    - process environment variables (names are case-insensitive by default)
    - a ``.env`` file in the working directory (see ``model_config`` below)

    Prefer uppercase env names in deployment (e.g. ``APP_NAME``).
    """

    # --- Application identity (examples you can extend) ---
    app_name: str = Field(
        default="AI Agent API Gateway",
        description="Human-readable name for logs and OpenAPI metadata.",
    )
    environment: Literal["development", "staging", "production"] = Field(
        default="development",
        description="Deployment slice; use to toggle stricter checks in the future.",
    )
    debug: bool = Field(
        default=False,
        description="When True, may enable verbose errors (never True in production).",
    )

    # --- LLM (OpenAI-compatible SDK: OpenAI, Azure OpenAI via base_url, etc.) ---
    # Populated from env ``API_KEY`` (Pydantic Settings matches ``api_key`` ↔ ``API_KEY``).
    api_key: str = Field(
        default="",
        description="Secret for the OpenAI-compatible Chat Completions API.",
    )
    # Optional override for Azure OpenAI, proxies, or other OpenAI-compatible hosts.
    openai_base_url: str | None = Field(
        default=None,
        description="If set, passed to the OpenAI SDK as ``base_url`` (e.g. Azure endpoint).",
    )

    model_config = SettingsConfigDict(
        # Load from a .env file if present (gitignored in real projects).
        env_file=".env",
        env_file_encoding="utf-8",
        # Allow extra keys in .env without failing (handy while iterating).
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    ``lru_cache`` ensures we parse env / .env once per process, which matters
    for low-latency gateways that import settings on every request path.
    """
    return Settings()


# Convenient singleton-style access for modules that prefer a global.
settings = get_settings()
