"""Shared database and UI helpers for SFMS master-data windows."""

from __future__ import annotations

import json
import sqlite3
from tkinter import messagebox

import auth
from audit import log_action
from config import DB_PATH


def connect_db() -> sqlite3.Connection:
    """Open a SQLite connection with required SFMS pragmas enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def current_user_id() -> int | None:
    """Return the current authenticated user's id, or None when logged out."""
    return auth.CURRENT_SESSION.user_id if auth.CURRENT_SESSION is not None else None


def ensure_permission_write(permission_key: str) -> bool:
    """Require a configured application permission before a database write."""
    if not auth.has_permission(permission_key):
        messagebox.showerror(
            "Access denied",
            "You do not have permission to perform this action.",
        )
        return False
    auth.CURRENT_SESSION.touch()
    return True


def ensure_admin_write() -> bool:
    """Return True only when an ADMIN session is available for a write operation."""
    if not auth.can_override_financial_data():
        messagebox.showerror("Access denied", "Administrator login is required for this action.")
        return False
    auth.CURRENT_SESSION.touch()
    return True


def audit(conn: sqlite3.Connection, action: str, table: str, record_id, old=None, new=None) -> None:
    """Write an audit row for the current session user."""
    old_value = json.dumps(old, default=str) if old is not None else None
    new_value = json.dumps(new, default=str) if new is not None else None
    log_action(conn, current_user_id(), action, table, record_id, old_value, new_value)
