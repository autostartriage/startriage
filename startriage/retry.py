"""Retry utilities for flaky Launchpad API calls (503/504)."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, TypeVar

from lazr.restfulclient.errors import ServerError

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# HTTP status codes worth retrying
_RETRYABLE_STATUSES = {502, 503, 504}

# Default retry parameters
DEFAULT_INITIAL_DELAY = 5.0  # seconds
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_FACTOR = 2.0


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the exception represents a transient server error."""
    if isinstance(exc, ServerError):
        try:
            status = int(exc.response.status)
            return status in _RETRYABLE_STATUSES
        except (AttributeError, ValueError, TypeError):
            # If we can't determine the status, retry anyway for ServerError
            return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return False


def lp_retry(
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> Callable[[F], F]:
    """Decorator that retries a function on transient Launchpad errors.

    Uses exponential backoff starting at ``initial_delay`` seconds,
    multiplied by ``backoff_factor`` after each attempt.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if not _is_retryable(exc) or attempt == max_attempts:
                        raise
                    last_exc = exc
                    logger.warning(
                        "Launchpad returned a transient error (%s), "
                        "retrying in %.0fs (attempt %d/%d)...",
                        exc.__class__.__name__,
                        delay,
                        attempt,
                        max_attempts,
                    )
                    time.sleep(delay)
                    delay *= backoff_factor
            # Should not reach here, but just in case
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


def lp_retry_async(
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> Callable[[F], F]:
    """Async version of lp_retry for coroutines."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if not _is_retryable(exc) or attempt == max_attempts:
                        raise
                    last_exc = exc
                    logger.warning(
                        "Launchpad returned a transient error (%s), "
                        "retrying in %.0fs (attempt %d/%d)...",
                        exc.__class__.__name__,
                        delay,
                        attempt,
                        max_attempts,
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
