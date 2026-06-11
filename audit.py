"""Fail-closed financial and best-effort operational audit logging."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import BASE_DIR, DB_PATH, TAMPER_ACTION_PREFIX
from utils import now_str

_LOGGER = logging.getLogger("sfms.operational_audit")
if not _LOGGER.handlers:
    Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(Path(BASE_DIR) / "operational_audit_errors.log", maxBytes=1_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.ERROR)


def _serialize(details) -> str | None:
    if details is None or isinstance(details, str):
        return details
    return json.dumps(details, sort_keys=True, default=str)


def log_financial_action(conn, action, user_id, details) -> None:
    """Write a mandatory audit row on the caller's current transaction."""
    details = dict(details or {})
    table = details.pop("table", "financial")
    record_id = details.pop("record_id", None)
    old = _serialize(details.pop("old", None))
    new = _serialize(details.pop("new", details))
    conn.execute(
        """INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,
                                  old_value,new_value,tamper_attempt)
           VALUES(?,?,?,?,?,?,?,?)""",
        (now_str(), user_id, action, table, record_id, old, new,
         1 if str(action).startswith(TAMPER_ACTION_PREFIX) else 0),
    )


def log_operational_event(action, user_id, details=None, *, conn=None) -> bool:
    """Best-effort operational audit; failures are preserved in a rotating file."""
    close_conn = False
    try:
        if conn is None:
            import sqlite3
            conn = sqlite3.connect(DB_PATH)
            close_conn = True
        payload = dict(details or {})
        table = payload.pop("table", "application")
        record_id = payload.pop("record_id", None)
        conn.execute(
            """INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,
                                      old_value,new_value,tamper_attempt)
               VALUES(?,?,?,?,?,?,?,?)""",
            (now_str(), user_id, action, table, record_id, None, _serialize(payload), 0),
        )
        if close_conn:
            conn.commit()
        return True
    except Exception:
        _LOGGER.exception("Operational audit write failed: action=%s user_id=%s details=%r", action, user_id, details)
        return False
    finally:
        if close_conn and conn is not None:
            conn.close()


def log_action(conn, user_id, action, table, record_id, old=None, new=None) -> None:
    """Best-effort legacy audit that preserves the old/new column layout."""
    try:
        conn.execute(
            """INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,
                                      old_value,new_value,tamper_attempt)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                now_str(),
                user_id,
                action,
                table,
                record_id,
                _serialize(old),
                _serialize(new),
                1 if str(action).startswith(TAMPER_ACTION_PREFIX) else 0,
            ),
        )
    except Exception:
        _LOGGER.exception(
            "Legacy audit write failed: action=%s user_id=%s table=%s record_id=%s",
            action,
            user_id,
            table,
            record_id,
        )
