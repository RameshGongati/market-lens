"""Inspect and control the standalone alert monitor from inside the app.

``alert_monitor.py`` runs as a separate process so alerts keep arriving after
Streamlit is closed. The app therefore has no handle on it and has to discover
its state from the outside.

The probe is the same flock the monitor uses for single-instance enforcement:
if the lock can be taken, nobody is holding it and no monitor is running. That
is authoritative in a way a pidfile is not — the kernel drops an flock however
the process dies, so a monitor that crashed or was killed cannot go on
reporting itself as running, which is exactly the failure a stale pidfile
produces.

The PID inside the lock file is read only AFTER the lock has been shown to be
held, and is used solely to signal the process. Reading it first would be
reading a number that may belong to a long-dead run.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

_APP_DIR = Path.home() / ".market-lens"
_LOCK_FILE = _APP_DIR / "alert_monitor.lock"

# alerts/monitor_control.py -> alerts -> project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _PROJECT_ROOT / "alert_monitor.py"


def _lock_is_held() -> bool:
    """True when another process holds the monitor's single-instance lock."""
    if not _LOCK_FILE.exists():
        return False
    try:
        import fcntl
    except ImportError:
        # Non-POSIX: no flock, so the monitor cannot be running here either.
        return False
    try:
        # Opened read-only on purpose. flock does not require write access,
        # and "r" cannot truncate the PID the monitor wrote.
        with open(_LOCK_FILE, "r", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            # We took it, so it was free. Release immediately — holding it
            # even briefly would make a monitor started in the next second
            # believe a duplicate was already running and exit.
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False
    except OSError as exc:
        logger.warning("Could not probe monitor lock: %s", exc)
        return False


def monitor_pid() -> int | None:
    """PID of the running monitor, or ``None`` when nothing is running."""
    if not _lock_is_held():
        return None
    try:
        text = _LOCK_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def is_running() -> bool:
    """Whether a monitor process currently holds the lock."""
    return _lock_is_held()


def start_monitor() -> tuple[bool, str]:
    """Launch the monitor as a detached background process.

    ``start_new_session`` puts it in its own process group so it outlives the
    Streamlit server that spawned it — without it the monitor would die with
    the app, which is the one thing it exists not to do.

    Returns:
        ``(ok, message)`` — the message is shown to the user either way.
    """
    if is_running():
        return False, "The monitor is already running."
    if not _SCRIPT.exists():
        return False, f"Cannot find {_SCRIPT}."
    try:
        subprocess.Popen(
            [sys.executable, str(_SCRIPT)],
            cwd=str(_PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        logger.error("Failed to start alert monitor: %s", exc)
        return False, f"Failed to start the monitor: {exc}"
    return True, "Monitor starting — the status refreshes within a few seconds."


def stop_monitor() -> tuple[bool, str]:
    """Ask the running monitor to shut down.

    SIGTERM rather than SIGKILL because ``alert_monitor.py`` installs a handler
    for it and exits its loop cleanly, flushing alert history on the way out.
    """
    pid = monitor_pid()
    if pid is None:
        return False, "No monitor is running."
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False, "The monitor process has already exited."
    except OSError as exc:
        logger.error("Failed to stop alert monitor (pid %s): %s", pid, exc)
        return False, f"Failed to stop the monitor: {exc}"
    return True, f"Stop signal sent to the monitor (pid {pid})."
