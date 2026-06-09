"""Efficient dashboard and login notification queries for SFMS."""

from __future__ import annotations

from datetime import datetime, timedelta

from config import BACKUP_INTERVAL_DEFAULT, SETTING_BACKUP_INTERVAL_HOURS
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
    """Return students with positive balances on payments older than a threshold."""
    threshold_days = max(0, int(threshold_days))
    payment_date_sql = _SQL_DATE.format(column="p.payment_date")
    cursor = conn.execute(
        f"""
        SELECT s.id AS student_id, s.name, s.class,
               SUM(p.balance) AS total_balance,
               strftime('%d-%m-%Y', MIN({payment_date_sql})) AS oldest_due_date,
               CAST(julianday('now', 'localtime') - julianday(MIN({payment_date_sql})) AS INTEGER) AS days_overdue
        FROM payments p
        JOIN students s ON s.id = p.student_id
        WHERE {payment_date_sql} < date('now', 'localtime', ?)
        GROUP BY s.id, s.name, s.class
        HAVING SUM(p.balance) > 0
        ORDER BY days_overdue DESC, total_balance DESC
        """,
        (f"-{threshold_days} days",),
    )
    rows = _rows_as_dicts(cursor)
    for row in rows:
        row["total_balance"] = float(row["total_balance"] or 0)
        row["days_overdue"] = int(row["days_overdue"] or 0)
    return rows


def get_todays_dues(conn) -> list[dict]:
    """Return today's unpaid fee-structure items for active students."""
    cursor = conn.execute(
        """
        SELECT s.id AS student_id, s.name, s.class,
               fh.name AS fee_head, MAX(fs.amount) AS amount
        FROM students s
        JOIN fee_structure fs ON fs.class = s.class
        JOIN fee_heads fh ON fh.id = fs.fee_head_id
        LEFT JOIN (
            SELECT student_id, fee_head_id,
                   COUNT(*) AS payment_count,
                   SUM(balance) AS total_balance
            FROM payments
            GROUP BY student_id, fee_head_id
        ) paid ON paid.student_id = s.id AND paid.fee_head_id = fs.fee_head_id
        WHERE s.is_active = 1
          AND fs.due_date = ?
          AND (COALESCE(paid.payment_count, 0) = 0 OR COALESCE(paid.total_balance, 0) > 0)
        GROUP BY s.id, s.name, s.class, fh.id, fh.name
        ORDER BY s.class, s.name, fh.name
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
    """Return cumulative overdue counts, today's dues count, and backup status."""
    payment_date_sql = _SQL_DATE.format(column="p.payment_date")
    overdue = conn.execute(
        f"""
        WITH student_overdue AS (
            SELECT p.student_id,
                   CAST(julianday('now', 'localtime') - julianday(MIN({payment_date_sql})) AS INTEGER) AS days_overdue,
                   SUM(p.balance) AS total_balance
            FROM payments p
            WHERE {payment_date_sql} IS NOT NULL
            GROUP BY p.student_id
            HAVING SUM(p.balance) > 0
        )
        SELECT
            COALESCE(SUM(CASE WHEN days_overdue >= 30 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN days_overdue >= 60 THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN days_overdue >= 90 THEN 1 ELSE 0 END), 0)
        FROM student_overdue
        """
    ).fetchone()
    today_due_count = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT s.id, fs.fee_head_id
            FROM students s
            JOIN fee_structure fs ON fs.class = s.class
            LEFT JOIN (
                SELECT student_id, fee_head_id, COUNT(*) AS payment_count, SUM(balance) AS total_balance
                FROM payments GROUP BY student_id, fee_head_id
            ) paid ON paid.student_id = s.id AND paid.fee_head_id = fs.fee_head_id
            WHERE s.is_active = 1 AND fs.due_date = ?
              AND (COALESCE(paid.payment_count, 0) = 0 OR COALESCE(paid.total_balance, 0) > 0)
            GROUP BY s.id, fs.fee_head_id
        )
        """,
        (today_str(),),
    ).fetchone()[0]
    return {
        "overdue_30": int(overdue[0] or 0),
        "overdue_60": int(overdue[1] or 0),
        "overdue_90": int(overdue[2] or 0),
        "today_dues": int(today_due_count or 0),
        "backup_overdue": backup_overdue(conn),
    }
