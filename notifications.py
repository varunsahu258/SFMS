"""Efficient dashboard and login notification queries for SFMS."""

from __future__ import annotations

from datetime import datetime, timedelta

from config import BACKUP_INTERVAL_DEFAULT, SETTING_BACKUP_INTERVAL_HOURS
from ledger import active_academic_year, ensure_student_charges
from ledger_service import LedgerService
from utils import today_str

_SQL_DATE = "date(substr({column}, 7, 4) || '-' || substr({column}, 4, 2) || '-' || substr({column}, 1, 2))"


def _rows_as_dicts(cursor) -> list[dict]:
    """Return cursor rows as dictionaries with or without sqlite3.Row."""
    columns = [description[0] for description in cursor.description]
    return [dict(row) if hasattr(row, "keys") else dict(zip(columns, row)) for row in cursor.fetchall()]


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse timestamp formats used by SFMS backup logs."""
    if not value:
        return None
    for date_format in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(str(value), date_format)
        except ValueError:
            continue
    return None


def _current_outstanding(conn) -> list[dict]:
    year = active_academic_year(conn)
    ensure_student_charges(conn, year)
    return LedgerService(conn).get_all_outstanding(year)


def _due_date(value):
    try:
        return datetime.strptime(str(value), "%d-%m-%Y")
    except (TypeError, ValueError):
        return None


def get_overdue_students(conn, threshold_days=30) -> list[dict]:
    """Return overdue students from the authoritative ledger service."""
    threshold_days = max(0, int(threshold_days))
    grouped = {}
    today = datetime.now()
    for row in _current_outstanding(conn):
        due = _due_date(row.get("due_date"))
        if due is None or (today - due).days < threshold_days:
            continue
        item = grouped.setdefault(row["student_id"], {
            "student_id": row["student_id"], "name": row["student"],
            "class": row["student_class"], "total_balance": 0.0,
            "oldest_due_date": row["due_date"], "days_overdue": 0,
        })
        item["total_balance"] += float(row["outstanding"])
        if (today - due).days > item["days_overdue"]:
            item["days_overdue"] = (today - due).days
            item["oldest_due_date"] = row["due_date"]
    return sorted(grouped.values(), key=lambda row: (row["days_overdue"], row["total_balance"]), reverse=True)


def get_todays_dues(conn) -> list[dict]:
    """Return today's dues from the authoritative ledger service."""
    return [{"student_id": row["student_id"], "name": row["student"],
             "class": row["student_class"], "fee_head": row["fee_head"],
             "amount": float(row["outstanding"])}
            for row in _current_outstanding(conn) if row.get("due_date") == today_str()]


def backup_interval_hours(conn) -> int:
    """Return the configured positive backup interval in hours."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (SETTING_BACKUP_INTERVAL_HOURS,),
    ).fetchone()
    try:
        interval = int(row[0]) if row and row[0] else BACKUP_INTERVAL_DEFAULT
    except (TypeError, ValueError):
        interval = BACKUP_INTERVAL_DEFAULT
    return max(1, interval)


def backup_overdue(conn) -> bool:
    """Return whether no backup exists within the configured interval."""
    interval = backup_interval_hours(conn)
    row = conn.execute("SELECT MAX(created_at) FROM backups_log").fetchone()
    latest = _parse_timestamp(row[0] if row else None)
    return latest is None or datetime.now() - latest > timedelta(hours=interval)


def get_notification_state(conn) -> dict:
    """Return dashboard counts from one authoritative outstanding snapshot."""
    rows = _current_outstanding(conn)
    today = datetime.now()
    oldest = {}
    for row in rows:
        due = _due_date(row.get("due_date"))
        if due is not None:
            oldest[row["student_id"]] = max(oldest.get(row["student_id"], 0), (today-due).days)
    return {
        "overdue_30": sum(days >= 30 for days in oldest.values()),
        "overdue_60": sum(days >= 60 for days in oldest.values()),
        "overdue_90": sum(days >= 90 for days in oldest.values()),
        "today_dues": sum(row.get("due_date") == today_str() for row in rows),
        "backup_overdue": backup_overdue(conn),
    }
