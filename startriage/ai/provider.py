"""AI provider abstraction and OpenAI-compatible implementations."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

import aiohttp

logger = logging.getLogger(__name__)


class AIProvider(ABC):
    """Abstract base class for AI triage providers."""

    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a system+user prompt and return the assistant's response text."""


class OpenAICompatibleProvider(AIProvider):
    """Provider that talks to any OpenAI-compatible chat completions API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_headers = extra_headers or {}

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(
                        f"AI API returned {resp.status}: {body[:500]}"
                    )
                data = await resp.json()
                return data["choices"][0]["message"]["content"]


# Default settings per provider name.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4",
        "env_key": "OPENROUTER_API_KEY",
    },
    "copilot": {
        "base_url": "https://models.inference.ai.azure.com",
        "model": "gpt-4o",
        "env_key": "GITHUB_TOKEN",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
    },
}


def get_provider(
    provider_name: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AIProvider:
    """Create an AI provider instance from name and optional overrides.

    API keys are resolved in order: explicit *api_key* arg → environment
    variable (provider-specific) → error.
    """
    defaults = _PROVIDER_DEFAULTS.get(provider_name)
    if defaults is None:
        available = ", ".join(sorted(_PROVIDER_DEFAULTS))
        raise ValueError(
            f"Unknown AI provider {provider_name!r}. Available: {available}"
        )

    resolved_key = api_key or os.environ.get(defaults["env_key"], "")
    if not resolved_key:
        raise ValueError(
            f"No API key for provider {provider_name!r}. "
            f"Set the {defaults['env_key']} environment variable or "
            f"configure ai.api_key in your startriage config."
        )

    resolved_url = base_url or defaults["base_url"]
    resolved_model = model or defaults["model"]

    extra_headers: dict[str, str] = {}
    if provider_name == "openrouter":
        extra_headers["HTTP-Referer"] = "https://github.com/ubuntu/startriage"
        extra_headers["X-Title"] = "startriage"

    logger.info(
        "Using AI provider %s, model %s, endpoint %s",
        provider_name,
        resolved_model,
        resolved_url,
    )
    return OpenAICompatibleProvider(
        resolved_url, resolved_key, resolved_model, extra_headers
    )
