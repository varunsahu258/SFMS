"""Audit-log helper functions for SFMS."""

from __future__ import annotations

from config import TAMPER_ACTION_PREFIX
from utils import now_str


def log_action(conn, user_id, action, table, record_id, old=None, new=None) -> None:
    """Insert an audit_log row and silently ignore any logging failure."""
    try:
        tamper_attempt = 1 if str(action).startswith(TAMPER_ACTION_PREFIX) else 0
        conn.execute(
            """
            INSERT INTO audit_log (
                timestamp, user_id, action, table_name, record_id,
                old_value, new_value, tamper_attempt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now_str(), user_id, action, table, record_id, old, new, tamper_attempt),
        )
    except Exception:
        pass
