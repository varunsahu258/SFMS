"""Three-installment fee schedules and overdue calculations."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import sqlite3

from ledger import active_academic_year, ensure_student_charges

INSTALLMENT_PERCENTAGES = (Decimal("0.48"), Decimal("0.26"), Decimal("0.26"))
DATE_FORMAT = "%d-%m-%Y"


def _parse_date(value: str) -> date:
    return datetime.strptime(str(value or "").strip(), DATE_FORMAT).date()


def validate_installment_dates(dates: tuple[str, str, str]) -> tuple[str, str, str]:
    """Validate three chronological DD-MM-YYYY installment dates."""
    parsed = tuple(_parse_date(value) for value in dates)
    if not (parsed[0] < parsed[1] < parsed[2]):
        raise ValueError("Installment due dates must be in increasing order.")
    return tuple(value.strip() for value in dates)


def save_installment_schedule(conn: sqlite3.Connection, academic_year: str, class_name: str,
                              dates: tuple[str, str, str], user_id: int | None) -> None:
    """Create or update the fixed 48/26/26 schedule for a class and year."""
    first, second, third = validate_installment_dates(dates)
    conn.execute(
        """INSERT INTO installment_schedules(
               academic_year,class_name,installment_1_due,installment_2_due,installment_3_due,updated_at,updated_by
           ) VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(academic_year,class_name) DO UPDATE SET
               installment_1_due=excluded.installment_1_due,
               installment_2_due=excluded.installment_2_due,
               installment_3_due=excluded.installment_3_due,
               updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
        (academic_year, class_name, first, second, third,
         datetime.now().strftime("%d-%m-%Y %H:%M:%S"), user_id),
    )


def get_installment_schedule(conn: sqlite3.Connection, academic_year: str, class_name: str):
    """Return the configured schedule row for a class and year."""
    return conn.execute(
        "SELECT * FROM installment_schedules WHERE academic_year=? AND class_name=?",
        (academic_year, class_name),
    ).fetchone()


def installment_amounts(total) -> tuple[Decimal, Decimal, Decimal]:
    """Split a total exactly into 48%, 26%, and the balancing final 26%."""
    total_value = Decimal(str(total or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    first = (total_value * INSTALLMENT_PERCENTAGES[0]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    second = (total_value * INSTALLMENT_PERCENTAGES[1]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return first, second, total_value - first - second


def overdue_installment_students(conn: sqlite3.Connection, as_of: str | None = None,
                                 class_name: str = "", search: str = "") -> list[dict]:
    """Return students whose cumulative cleared amount is below a due installment target."""
    year = active_academic_year(conn)
    if not year:
        return []
    ensure_student_charges(conn, year)
    target_date = _parse_date(as_of) if as_of else date.today()
    term = f"%{search.strip()}%"
    rows = conn.execute(
        """SELECT s.id,s.scholar_no,s.name,s.father_name,s.class,s.section,s.phone,
                  sch.installment_1_due,sch.installment_2_due,sch.installment_3_due,
                  COALESCE(SUM(CASE WHEN COALESCE(fh.is_one_time,0)=0
                       AND LOWER(fh.name) NOT LIKE 'late fee%%' THEN l.original_amount ELSE 0 END),0) total_fee,
                  COALESCE(SUM(CASE WHEN COALESCE(fh.is_one_time,0)=0
                       AND LOWER(fh.name) NOT LIKE 'late fee%%'
                       THEN l.original_amount-l.balance ELSE 0 END),0) settled
           FROM students s
           JOIN installment_schedules sch ON sch.academic_year=? AND sch.class_name=s.class
           LEFT JOIN charge_ledger l ON l.student_id=s.id AND l.academic_year=? AND l.status<>'CANCELLED'
           LEFT JOIN fee_heads fh ON fh.id=l.fee_head_id
           WHERE s.is_active=1 AND (?='' OR s.class=?)
             AND (s.name LIKE ? OR s.scholar_no LIKE ? OR s.father_name LIKE ?)
           GROUP BY s.id,sch.academic_year,sch.class_name
           ORDER BY s.class,s.name""",
        (year, year, class_name, class_name, term, term, term),
    ).fetchall()
    result = []
    for row in rows:
        due_dates = tuple(_parse_date(row[f"installment_{index}_due"]) for index in range(1, 4))
        due_count = sum(value < target_date for value in due_dates)
        if due_count == 0:
            continue
        total = Decimal(str(row["total_fee"] or 0))
        settled = Decimal(str(row["settled"] or 0))
        expected = sum(installment_amounts(total)[:due_count], Decimal("0"))
        shortfall = max(expected - settled, Decimal("0"))
        if shortfall > Decimal("0.005"):
            item = dict(row)
            item.update({"installments_due": due_count, "expected": expected,
                         "settled": settled, "shortfall": shortfall,
                         "last_due_date": due_dates[due_count - 1].strftime(DATE_FORMAT)})
            result.append(item)
    return result
