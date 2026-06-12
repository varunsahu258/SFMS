"""Audited opening balances imported while moving from manual fee records."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from audit import log_action
from ledger import active_academic_year
from ledger_service import LedgerService
from utils import now_str

OPENING_BALANCE_HEAD = "Opening Balance (Manual Records)"


def _opening_head_id(conn: sqlite3.Connection) -> int:
    """Return the fee head used to collect migrated manual balances."""
    row = conn.execute(
        "SELECT id FROM fee_heads WHERE name=? ORDER BY id LIMIT 1", (OPENING_BALANCE_HEAD,)
    ).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        """INSERT INTO fee_heads(name,register_type,is_active,is_one_time)
           VALUES(?,'BOTH',1,1)""",
        (OPENING_BALANCE_HEAD,),
    )
    return int(cursor.lastrowid)


def record_opening_balance(
    conn: sqlite3.Connection,
    student_id: int,
    academic_year: str,
    amount: Decimal,
    due_date: str,
    note: str,
    user_id: int,
) -> int:
    """Create one immutable prior-year opening balance and its ledger charge."""
    year = str(academic_year or "").strip()
    if not year:
        raise ValueError("Select or enter the academic year for the old balance.")
    current_year = active_academic_year(conn)
    if current_year and year == current_year:
        raise ValueError("Opening balances must belong to an academic year before the current year.")
    value = Decimal(str(amount))
    if value <= 0:
        raise ValueError("Opening balance must be greater than zero.")
    if conn.execute(
        "SELECT 1 FROM opening_balances WHERE student_id=? AND academic_year=?",
        (student_id, year),
    ).fetchone():
        raise ValueError("An opening balance already exists for this student and academic year.")

    timestamp = now_str()
    head_id = _opening_head_id(conn)
    charge = conn.execute(
        """INSERT INTO student_charges(
               student_id,academic_year,fee_structure_id,fee_head_id,
               original_amount,due_date,status,created_at
           ) VALUES(?,?,NULL,?,?,?,'OPEN',?)""",
        (student_id, year, head_id, str(value), due_date or None, timestamp),
    )
    opening = conn.execute(
        """INSERT INTO opening_balances(
               student_id,academic_year,amount,due_date,note,charge_id,created_at,created_by
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (student_id, year, str(value), due_date or None, note.strip(), charge.lastrowid, timestamp, user_id),
    )
    log_action(
        conn, user_id, "OPENING_BALANCE_CREATE", "opening_balances", opening.lastrowid,
        None,
        f"student_id={student_id};academic_year={year};amount={value};due_date={due_date};charge_id={charge.lastrowid}",
    )
    return int(opening.lastrowid)


def student_balance_summary(conn: sqlite3.Connection, student_id: int) -> dict:
    """Return previous-year, current-year, and complete outstanding totals."""
    current_year = active_academic_year(conn)
    service = LedgerService(conn)
    all_due = service.get_outstanding(student_id)
    current_due = service.get_outstanding(student_id, academic_year_id=current_year) if current_year else Decimal("0")
    return {
        "current_year": current_year,
        "current_due": current_due,
        "previous_due": max(Decimal("0"), all_due - current_due),
        "total_due": all_due,
    }
