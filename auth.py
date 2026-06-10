"""Authentication, authorization, and session-timeout handling for SFMS."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from tkinter import messagebox

from app_events import signal_session_timeout

import bcrypt

from audit import log_operational_event
from config import DB_PATH, SETTING_SESSION_TIMEOUT_MINUTES, SESSION_TIMEOUT_DEFAULT
from permissions import DEFAULT_ACCOUNTANT_PERMISSIONS, PERMISSION_KEYS
from security_utils import GENERIC_LOGIN_FAILURE_MESSAGE, MACHINE_AUTHORIZATION_REQUIRED_MESSAGE
from utils import now_str

LOCKOUT_MINUTES = 30
MAX_FAILED_ATTEMPTS = 5
LOGIN_SUCCESS_ACTION = "LOGIN_SUCCESS"
LOGIN_FAIL_ACTION = "LOGIN_FAIL"
LOGOUT_ACTION = "LOGOUT"
USERS_TABLE = "users"


@dataclass
class Session:
    """Represent an authenticated user session."""

    token: str
    user_id: int
    username: str
    role: str
    login_time: datetime
    last_active: datetime

    def is_timed_out(self, timeout_minutes: int) -> bool:
        """Return True when the session has been inactive longer than the timeout."""
        return datetime.now() - self.last_active >= timedelta(minutes=timeout_minutes)

    def touch(self) -> None:
        """Update the session's last-active timestamp to the current time."""
        self.last_active = datetime.now()


CURRENT_SESSION = None


def _connect() -> sqlite3.Connection:
    """Open a configured SQLite connection for authentication operations."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse a stored timestamp value into a datetime, returning None if invalid."""
    if not value:
        return None
    for date_format in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    return None


def _lock_minutes_remaining(locked_at: str | None) -> int:
    """Return whole minutes remaining before an account lock expires."""
    locked_time = _parse_timestamp(locked_at)
    if locked_time is None:
        return 0
    unlock_time = locked_time + timedelta(minutes=LOCKOUT_MINUTES)
    seconds_remaining = max(0, int((unlock_time - datetime.now()).total_seconds()))
    return (seconds_remaining + 59) // 60


def _log_login_failure(conn, user_id, username_attempted: str, failure_reason: str, source_ip: str | None = None) -> None:
    """Write a warning-level internal audit row for an authentication failure."""
    log_operational_event(
        LOGIN_FAIL_ACTION,
        user_id,
        {
            "table": USERS_TABLE if user_id is not None else "authentication",
            "record_id": user_id or username_attempted,
            "severity": "WARNING",
            "username_attempted": username_attempted,
            "failure_reason": failure_reason,
            "source_ip": source_ip,
            "timestamp": now_str(),
        },
        conn=conn,
    )


def login(username, password, source_ip: str | None = None) -> tuple[bool, str]:
    """Authenticate a user and create CURRENT_SESSION on success."""
    global CURRENT_SESSION
    normalized_username = str(username).strip()
    with _connect() as conn:
        from integrity import machine_authorization_required

        if machine_authorization_required(conn):
            return False, MACHINE_AUTHORIZATION_REQUIRED_MESSAGE
        user = conn.execute(
            """
            SELECT id, username, password_hash, role, is_active, failed_attempts, locked_at
            FROM users
            WHERE username = ?
            """,
            (normalized_username,),
        ).fetchone()
        if user is None:
            _log_login_failure(conn, None, normalized_username, "USER_NOT_FOUND", source_ip)
            return False, GENERIC_LOGIN_FAILURE_MESSAGE
        if user["is_active"] == 0:
            _log_login_failure(conn, user["id"], normalized_username, "DEACTIVATED", source_ip)
            return False, GENERIC_LOGIN_FAILURE_MESSAGE

        locked_time = _parse_timestamp(user["locked_at"])
        if locked_time is not None:
            locked_for = datetime.now() - locked_time
            if locked_for < timedelta(minutes=LOCKOUT_MINUTES):
                _log_login_failure(conn, user["id"], normalized_username, "LOCKED", source_ip)
                return False, GENERIC_LOGIN_FAILURE_MESSAGE
            conn.execute("UPDATE users SET failed_attempts = 0, locked_at = NULL WHERE id = ?", (user["id"],))
            user = conn.execute(
                """
                SELECT id, username, password_hash, role, is_active, failed_attempts, locked_at
                FROM users
                WHERE id = ?
                """,
                (user["id"],),
            ).fetchone()

        password_hash = user["password_hash"] or ""
        password_ok = bcrypt.checkpw(str(password).encode("utf-8"), password_hash.encode("utf-8"))
        if not password_ok:
            failed_attempts = int(user["failed_attempts"] or 0) + 1
            locked_at = now_str() if failed_attempts >= MAX_FAILED_ATTEMPTS else None
            conn.execute(
                "UPDATE users SET failed_attempts = ?, locked_at = ? WHERE id = ?",
                (failed_attempts, locked_at, user["id"]),
            )
            _log_login_failure(conn, user["id"], normalized_username, "BAD_PASSWORD", source_ip)
            return False, GENERIC_LOGIN_FAILURE_MESSAGE

        conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_at = NULL, last_login = ? WHERE id = ?",
            (now_str(), user["id"]),
        )
        login_time = datetime.now()
        CURRENT_SESSION = Session(
            token=str(uuid.uuid4()),
            user_id=int(user["id"]),
            username=user["username"],
            role=user["role"],
            login_time=login_time,
            last_active=login_time,
        )
        log_operational_event(LOGIN_SUCCESS_ACTION, CURRENT_SESSION.user_id, {"table": USERS_TABLE, "record_id": CURRENT_SESSION.user_id}, conn=conn)
        return True, "OK"


def logout() -> None:
    """Log out the current user, audit the event, and clear CURRENT_SESSION."""
    global CURRENT_SESSION
    if CURRENT_SESSION is not None:
        with _connect() as conn:
            log_operational_event(LOGOUT_ACTION, CURRENT_SESSION.user_id, {"table": USERS_TABLE, "record_id": CURRENT_SESSION.user_id}, conn=conn)
    CURRENT_SESSION = None


def require_role(*roles):
    """Decorate a callable so it only runs for users with one of the supplied roles."""
    def decorator(func):
        """Return a wrapped function that enforces role membership."""
        @wraps(func)
        def wrapper(*args, **kwargs):
            """Run the protected callable after checking the active session role."""
            if CURRENT_SESSION is None or CURRENT_SESSION.role not in roles:
                messagebox.showerror("Access denied", "You do not have permission to perform this action.")
                return None
            CURRENT_SESSION.touch()
            return func(*args, **kwargs)

        return wrapper

    return decorator


def current_user_can_write() -> bool:
    """Return True when an authenticated session exists before a database write."""
    return CURRENT_SESSION is not None and CURRENT_SESSION.token is not None


def has_permission(permission_key: str) -> bool:
    """Return whether the current authenticated user has a capability."""
    if permission_key not in PERMISSION_KEYS:
        return False
    session = CURRENT_SESSION
    if session is None or session.token is None:
        return False
    if session.role == "ADMIN":
        return True
    if session.role != "ACCOUNTANT":
        return False
    user_id = session.user_id
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT allowed FROM user_permissions WHERE user_id=? AND permission_key=?",
                (user_id, permission_key),
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None:
        return bool(row["allowed"])
    return permission_key in DEFAULT_ACCOUNTANT_PERMISSIONS


def require_permission(permission_key: str):
    """Decorate a UI action so ADMIN or an explicitly permitted accountant may run it."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not has_permission(permission_key):
                messagebox.showerror("Access denied", "You do not have permission to perform this action.")
                return None
            CURRENT_SESSION.touch()
            return func(*args, **kwargs)
        return wrapper
    return decorator


def can_manage_non_financial_data() -> bool:
    """Return True for roles allowed to maintain students, classes, and school documents."""
    return current_user_can_write() and (CURRENT_SESSION.role == "ADMIN" or has_permission("manage_students"))


def can_override_financial_data() -> bool:
    """Return True only for administrators allowed to alter financial outcomes."""
    return current_user_can_write() and CURRENT_SESSION.role == "ADMIN"


def touch_session() -> None:
    """Refresh CURRENT_SESSION activity when a UI action occurs."""
    if CURRENT_SESSION is not None:
        CURRENT_SESSION.touch()


def get_login_status(username: str) -> dict[str, int | str | None]:
    """Return failed-attempt and lock information for login UI feedback."""
    with _connect() as conn:
        user = conn.execute(
            "SELECT failed_attempts, locked_at FROM users WHERE username = ?",
            (str(username).strip(),),
        ).fetchone()
    if user is None:
        return {"failed_attempts": 0, "locked_at": None, "minutes_remaining": 0}
    return {
        "failed_attempts": int(user["failed_attempts"] or 0),
        "locked_at": user["locked_at"],
        "minutes_remaining": _lock_minutes_remaining(user["locked_at"]),
    }


def check_timeout() -> None:
    """Signal timed-out sessions without touching Tk from worker threads."""
    if CURRENT_SESSION is None:
        return
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (SETTING_SESSION_TIMEOUT_MINUTES,),
        ).fetchone()
    timeout_minutes = int(row["value"]) if row and row["value"] else SESSION_TIMEOUT_DEFAULT
    if CURRENT_SESSION is not None and CURRENT_SESSION.is_timed_out(timeout_minutes):
        signal_session_timeout()
