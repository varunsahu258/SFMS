"""Thread-safe application event queues for Tk main-thread handling."""

from __future__ import annotations

import queue
from dataclasses import dataclass

SESSION_TIMEOUT = "SESSION_TIMEOUT"
BACKUP_WARNING = "BACKUP_WARNING"

@dataclass(frozen=True)
class AppEvent:
    """A queued event produced by a worker and consumed by Tk's main thread."""
    type: str
    payload: dict | None = None

ui_event_queue: queue.Queue[AppEvent] = queue.Queue()


def signal_session_timeout() -> None:
    """Signal the Tk main thread that the authenticated session expired."""
    ui_event_queue.put(AppEvent(SESSION_TIMEOUT, {}))


def signal_backup_warning(failures: int) -> None:
    """Signal the Tk main thread that automatic backups are repeatedly failing."""
    ui_event_queue.put(AppEvent(BACKUP_WARNING, {"failures": int(failures)}))
