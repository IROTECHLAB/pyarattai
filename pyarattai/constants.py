"""Module-level constants for pyarattai."""
from __future__ import annotations

AUTH_BASE = "https://accounts.arattai.in"
CHAT_BASE = "https://web.arattai.in"
FILES_BASE = "https://files.arattai.in"
E2EE_DEFAULT_URL = "http://127.0.0.1:8766"

SESSION_DIR = "~/.pyarattai"
SESSION_FILE = "~/.pyarattai/session.json"
BOT_DB_FILE = "~/.pyarattai/bot.db"

CLIENT_VERSION = "14651084"
PRODUCT_IDENTIFIER = "ozen"
CONSENTS_VERSION = "1"

# Match the working reference client exactly. Android 10 / Chrome 119
# UA is what Zoho's ZGS edge accepts on Termux.
_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.6045.193 Mobile Safari/537.36"
)

_SIGNIN_URL = (
    f"{AUTH_BASE}/signin?servicename=Arattai"
    "&serviceurl=https%3A%2F%2Fweb.arattai.in&QRLogin=false"
)

AUTH_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": AUTH_BASE,
    "Referer": _SIGNIN_URL,
}

CHAT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en",
    "Origin": CHAT_BASE,
    "Referer": f"{CHAT_BASE}/",
    "X-Requested-With": "XMLHttpRequest",
    "X-XHR-exception": "true",
    "X-Consents-Version": CONSENTS_VERSION,
    "X-Product-Identifier": PRODUCT_IDENTIFIER,
    "X-Client-Version": CLIENT_VERSION,
}

FILES_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Origin": CHAT_BASE,
    "Referer": f"{CHAT_BASE}/",
    "X-Requested-With": "XMLHttpRequest",
}

STATUS_TYPING = "104"
STATUS_IDLE = "105"

MTYPE_SYSTEM = 16

MAX_RETRIES = 3
RETRY_BACKOFF = 0.6

__all__ = [
    "AUTH_BASE", "CHAT_BASE", "FILES_BASE", "E2EE_DEFAULT_URL",
    "SESSION_DIR", "SESSION_FILE", "BOT_DB_FILE",
    "CLIENT_VERSION", "PRODUCT_IDENTIFIER", "CONSENTS_VERSION",
    "AUTH_HEADERS", "CHAT_HEADERS", "FILES_HEADERS",
    "STATUS_TYPING", "STATUS_IDLE", "MTYPE_SYSTEM",
    "MAX_RETRIES", "RETRY_BACKOFF",
]
