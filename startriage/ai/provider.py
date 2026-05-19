"""AI provider abstraction and OpenAI-compatible implementations."""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

import aiohttp

logger = logging.getLogger(__name__)


class UnknownModelError(Exception):
    """Raised when the API rejects the model name as unknown."""

    def __init__(self, model: str, available: list[str]) -> None:
        self.model = model
        self.available = available
        models_list = "\n  ".join(available) if available else "(could not retrieve model list)"
        super().__init__(
            f"Unknown model: {model!r}\n\nAvailable models:\n  {models_list}"
        )


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
        max_tokens: int = 16384,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.extra_headers = extra_headers or {}

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
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
                    if self._is_unknown_model_error(body):
                        available = await self._fetch_available_models(headers)
                        raise UnknownModelError(self.model, available)
                    raise RuntimeError(
                        f"AI API returned {resp.status}: {body[:500]}"
                    )
                data = await resp.json()
                return data["choices"][0]["message"]["content"]

    @staticmethod
    def _is_unknown_model_error(body: str) -> bool:
        """Check if the API error is an unknown model error."""
        try:
            data = json.loads(body)
            return data.get("error", {}).get("code") == "unknown_model"
        except (json.JSONDecodeError, AttributeError):
            return False

    async def _fetch_available_models(self, headers: dict[str, str]) -> list[str]:
        """Query the model catalog endpoint to get available model names."""
        # GitHub Models uses /catalog/models on the base domain, not relative to base_url.
        # For other providers, fall back to {base_url}/models.
        from urllib.parse import urlparse

        parsed = urlparse(self.base_url)
        catalog_url = f"{parsed.scheme}://{parsed.netloc}/catalog/models"
        fallback_url = f"{self.base_url}/models"

        try:
            async with aiohttp.ClientSession() as session:
                # Try catalog endpoint first (GitHub Models)
                async with session.get(
                    catalog_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return self._extract_model_ids(data)

                # Fall back to standard /models endpoint (OpenAI-compatible)
                async with session.get(
                    fallback_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return self._extract_model_ids(data)
                    return []
        except Exception:
            return []

    @staticmethod
    def _extract_model_ids(data: object) -> list[str]:
        """Extract model IDs from various API response formats."""
        if isinstance(data, list):
            return sorted(m.get("id") or m.get("name", "") for m in data if isinstance(m, dict))
        if isinstance(data, dict) and "data" in data:
            return sorted(m.get("id", "") for m in data["data"] if isinstance(m, dict))
        return []


# Default settings per provider name.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4",
        "env_key": "OPENROUTER_API_KEY",
    },
    "copilot": {
        "base_url": "https://models.github.ai/inference",
        "model": "openai/gpt-4.1",
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
    max_tokens: int | None = None,
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
    elif provider_name == "copilot":
        extra_headers["Accept"] = "application/vnd.github+json"
        extra_headers["X-GitHub-Api-Version"] = "2026-03-10"

    resolved_max_tokens = max_tokens or 16384

    logger.info(
        "Using AI provider %s, model %s, endpoint %s",
        provider_name,
        resolved_model,
        resolved_url,
    )
    return OpenAICompatibleProvider(
        resolved_url, resolved_key, resolved_model, resolved_max_tokens, extra_headers
    )
