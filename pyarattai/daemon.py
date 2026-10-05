"""Helpers to install & manage the Node.js E2EE daemon."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

__all__ = ["install", "start", "stop", "status"]

_DAEMON_DIR = Path(os.path.expanduser("~/.pyarattai/daemon"))
_PID_FILE = _DAEMON_DIR / "daemon.pid"


def _pkg_daemon_dir() -> Path:
    return Path(__file__).resolve().parent / "daemon"


def install(force: bool = False) -> Path:
    """Copy bundled JS files into ``~/.pyarattai/daemon/``.

    Args:
        force: Overwrite existing files.

    Returns:
        The daemon install directory.
    """
    _DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    src = _pkg_daemon_dir()
    if not src.exists():
        raise RuntimeError(
            "No daemon scripts bundled. Install with `pip install "
            "'irotechlab-pyarattai[daemon]'` or add JS files manually."
        )
    for f in src.iterdir():
        dest = _DAEMON_DIR / f.name
        if dest.exists() and not force:
            continue
        shutil.copy2(f, dest)
    return _DAEMON_DIR


def start(node_bin: str = "node") -> int:
    """Start the daemon in the background. Returns the PID."""
    install()
    script = _DAEMON_DIR / "arattai-daemon.cjs"
    if not script.exists():
        raise RuntimeError(f"Daemon script missing: {script}")
    log = open(_DAEMON_DIR / "daemon.log", "a")  # noqa: SIM115
    proc = subprocess.Popen(
        [node_bin, str(script)],
        cwd=str(_DAEMON_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _PID_FILE.write_text(str(proc.pid))
    return proc.pid


def stop() -> bool:
    """Stop the daemon if it is running."""
    if not _PID_FILE.exists():
        return False
    try:
        pid = int(_PID_FILE.read_text().strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        pass
    _PID_FILE.unlink(missing_ok=True)
    return True


def status() -> Optional[int]:
    """Return the PID if the daemon is running, else None."""
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None
