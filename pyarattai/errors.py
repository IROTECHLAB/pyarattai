"""Exception hierarchy for pyarattai."""
from __future__ import annotations

from typing import Any, Optional

__all__ = ["ArattaiError", "APIError", "NetworkError", "AuthError", "SessionError", "RateLimitError"]


class ArattaiError(Exception):
    """Base class for all pyarattai errors."""


class APIError(ArattaiError):
    """Raised when the Arattai API returns a 4xx/5xx response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        url: Optional[str] = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.payload = payload

    def __str__(self) -> str:  # noqa: D401
        parts = [super().__str__()]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.url:
            parts.append(f"url={self.url}")
        return " | ".join(parts)


class RateLimitError(ArattaiError):
    """Raised when Arattai returns a throttling response."""


class NetworkError(ArattaiError):
    """Raised when the underlying HTTP request fails (timeout, DNS, ...)."""


class AuthError(ArattaiError):
    """Raised when authentication fails or OTP is rejected."""


class SessionError(ArattaiError):
    """Raised when a session file is missing or malformed."""
