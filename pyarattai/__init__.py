"""pyarattai — Arattai client + bot framework."""
from __future__ import annotations

from .auth import Auth
from .bot import ArattaiBot, CommandHandler, Message
from .client import ArattaiClient
from .e2ee import E2EEBridge
from .signal import SignalBridge
from .errors import APIError, ArattaiError, AuthError, NetworkError, SessionError
from .models import Chat
from .session import Session

__version__ = "0.1.0"

__all__ = [
    "ArattaiBot", "ArattaiClient", "Auth", "Session",
    "Chat", "Message", "CommandHandler",
    "E2EEBridge", "SignalBridge",
    "ArattaiError", "APIError", "NetworkError", "AuthError", "SessionError",
    "__version__",
]
