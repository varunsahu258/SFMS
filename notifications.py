"""Efficient dashboard and login notification queries for SFMS."""

from __future__ import annotations

from datetime import datetime, timedelta

from config import BACKUP_INTERVAL_DEFAULT, SETTING_BACKUP_INTERVAL_HOURS
from ledger import active_academic_year, ensure_student_charges
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


def get_overdue_students(conn, threshold_days=30) -> list[dict]:
    """Return students with positive charge-ledger balances older than a threshold."""
    threshold_days = max(0, int(threshold_days))
    ensure_student_charges(conn, active_academic_year(conn))
    due_date_sql = _SQL_DATE.format(column="l.due_date")
    cursor = conn.execute(
        f"""
        SELECT s.id AS student_id, s.name, s.class,
               SUM(l.balance) AS total_balance,
               strftime('%d-%m-%Y', MIN({due_date_sql})) AS oldest_due_date,
               CAST(julianday('now','localtime')-julianday(MIN({due_date_sql})) AS INTEGER) AS days_overdue
        FROM charge_ledger l JOIN students s ON s.id=l.student_id
        WHERE l.balance>0 AND l.status<>'CANCELLED' AND s.is_active=1
              AND {due_date_sql} < date('now','localtime', ?)
        GROUP BY s.id,s.name,s.class
        ORDER BY days_overdue DESC,total_balance DESC
        """,
        (f"-{threshold_days} days",),
    )
    rows = _rows_as_dicts(cursor)
    for row in rows:
        row["total_balance"] = float(row["total_balance"] or 0)
        row["days_overdue"] = int(row["days_overdue"] or 0)
    return rows


def get_todays_dues(conn) -> list[dict]:
    """Return charge-ledger items due today with positive balances."""
    ensure_student_charges(conn, active_academic_year(conn))
    cursor = conn.execute(
        """
        SELECT s.id AS student_id,s.name,s.class,fh.name AS fee_head,l.balance AS amount
        FROM charge_ledger l
        JOIN students s ON s.id=l.student_id
        JOIN fee_heads fh ON fh.id=l.fee_head_id
        WHERE s.is_active=1 AND l.status<>'CANCELLED' AND l.due_date=? AND l.balance>0
        ORDER BY s.class,s.name,fh.name
        """,
        (today_str(),),
    )
    rows = _rows_as_dicts(cursor)
    for row in rows:
        row["amount"] = float(row["amount"] or 0)
    return rows


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
    """Return overdue counts, today's charge count, and backup status."""
    ensure_student_charges(conn, active_academic_year(conn))
    due_date_sql = _SQL_DATE.format(column="l.due_date")
    overdue = conn.execute(
        f"""
        WITH student_overdue AS (
            SELECT l.student_id,
                   CAST(julianday('now','localtime')-julianday(MIN({due_date_sql})) AS INTEGER) AS days_overdue
            FROM charge_ledger l JOIN students s ON s.id=l.student_id
            WHERE l.balance>0 AND l.status<>'CANCELLED' AND s.is_active=1
                  AND {due_date_sql} IS NOT NULL
            GROUP BY l.student_id
        )
        SELECT COALESCE(SUM(days_overdue>=30),0),
               COALESCE(SUM(days_overdue>=60),0),
               COALESCE(SUM(days_overdue>=90),0)
        FROM student_overdue
        """
    ).fetchone()
    today_due_count = conn.execute(
        "SELECT COUNT(*) FROM charge_ledger l JOIN students s ON s.id=l.student_id WHERE s.is_active=1 AND l.status<>'CANCELLED' AND l.due_date=? AND l.balance>0",
        (today_str(),),
    ).fetchone()[0]
    return {
        "overdue_30": int(overdue[0] or 0), "overdue_60": int(overdue[1] or 0),
        "overdue_90": int(overdue[2] or 0), "today_dues": int(today_due_count or 0),
        "backup_overdue": backup_overdue(conn),
    }
