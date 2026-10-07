"""Shared pytest fixtures."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import pytest

# Ensure the package import works even when running tests without
# installing via pip
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tmp_session_file(tmp_path: Path) -> Path:
    """A path to a session.json that doesn't exist yet."""
    return tmp_path / "session.json"


@pytest.fixture
def fake_cookies() -> List[Dict]:
    """Realistic cookie list for building fake sessions."""
    return [
        {"name": "iamcsr", "value": "abc-123",
         "domain": "accounts.arattai.in", "path": "/", "secure": True},
        {"name": "CT_CSRF_TOKEN", "value": "deadbeef",
         "domain": "web.arattai.in", "path": "/", "secure": True},
        {"name": "x-tkp-token", "value": "20004081014-xxxx-xxxx",
         "domain": ".arattai.in", "path": "/", "secure": True},
        {"name": "e2ee_registration_id", "value": "8556",
         "domain": ".arattai.in", "path": "/", "secure": True},
        {"name": "e2ee_device_id", "value": "3685869",
         "domain": ".arattai.in", "path": "/", "secure": True},
    ]
