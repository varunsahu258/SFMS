"""Payment-instrument controls, revenue recognition, and cashier shifts."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from audit import log_action
from utils import now_str, today_str

CHEQUE_STATUSES = ("PENDING", "CLEARED", "BOUNCED", "CANCELLED")
_TERMINAL_CHEQUE_STATUSES = set(CHEQUE_STATUSES) - {"PENDING"}


def normalize_reference(value: str | None) -> str:
    """Normalize cheque/UPI references for comparison and uniqueness."""
    return re.sub(r"\s+", "", str(value or "")).upper()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _audit_duplicate(conn: sqlite3.Connection, payment_id: int, field: str, value: str) -> None:
    log_action(
        conn, None, "TAMPER_PAYMENT_REFERENCE_DUPLICATE", "payments", payment_id,
        None, f"Duplicate legacy {field}: {value}; normalized unique field left empty",
    )


def _deduplicate_legacy(conn: sqlite3.Connection, column: str) -> None:
    """Keep the first normalized reference and flag later legacy duplicates."""
    rows = conn.execute(
        f"SELECT id,{column} FROM payments WHERE {column} IS NOT NULL AND {column}<>'' ORDER BY id"
    ).fetchall()
    seen: dict[str, int] = {}
    for payment_id, raw_value in rows:
        normalized = normalize_reference(raw_value)
        if not normalized:
            conn.execute(f"UPDATE payments SET {column}=NULL WHERE id=?", (payment_id,))
        elif normalized in seen:
            _audit_duplicate(conn, payment_id, column, normalized)
            conn.execute(f"UPDATE payments SET {column}=NULL WHERE id=?", (payment_id,))
        else:
            seen[normalized] = payment_id
            conn.execute(f"UPDATE payments SET {column}=? WHERE id=?", (normalized, payment_id))


def migrate_payment_controls(conn: sqlite3.Connection) -> None:
    """Add normalized instruments, cheque workflow, and shift tables without dropping legacy data."""
    controls_installed = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ux_payments_cheque_number'"
    ).fetchone() is not None
    # The legacy trigger blocked every UPDATE. Temporarily remove it for the
    # first backfill, then replace it with a financial-field immutability guard.
    conn.execute("DROP TRIGGER IF EXISTS trg_payments_no_update")
    _add_column(conn, "payments", "cheque_number TEXT")
    _add_column(conn, "payments", "upi_reference TEXT")
    _add_column(conn, "payments", "cheque_status TEXT")
    _add_column(conn, "payments", "cheque_cleared_date TEXT")
    _add_column(conn, "payments", "cheque_bank_reference TEXT")

    if not controls_installed:
        # Preserve cheque_tracker and note values in authoritative normalized columns.
        conn.execute(
            """
            UPDATE payments
            SET cheque_number=(SELECT UPPER(REPLACE(REPLACE(REPLACE(REPLACE(TRIM(ct.cheque_no),' ',''),char(9),''),char(10),''),char(13),''))
                               FROM cheque_tracker ct WHERE ct.payment_id=payments.id LIMIT 1),
                cheque_status=COALESCE((SELECT CASE WHEN UPPER(ct.status) IN ('PENDING','CLEARED','BOUNCED','CANCELLED') THEN UPPER(ct.status) ELSE 'PENDING' END FROM cheque_tracker ct
                                        WHERE ct.payment_id=payments.id LIMIT 1),'PENDING')
            WHERE UPPER(payment_mode)='CHEQUE' AND cheque_number IS NULL
            """
        )
        conn.execute(
            """
            UPDATE payments SET upi_reference=UPPER(REPLACE(REPLACE(REPLACE(REPLACE(TRIM(note),' ',''),char(9),''),char(10),''),char(13),''))
            WHERE UPPER(payment_mode)='UPI' AND upi_reference IS NULL
                  AND TRIM(COALESCE(note,''))<>''
            """
        )
        _deduplicate_legacy(conn, "cheque_number")
        _deduplicate_legacy(conn, "upi_reference")

    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_cheque_number
            ON payments(cheque_number) WHERE cheque_number IS NOT NULL AND cheque_number<>'';
        CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_upi_reference
            ON payments(upi_reference) WHERE upi_reference IS NOT NULL AND upi_reference<>'';
        CREATE INDEX IF NOT EXISTS idx_payments_cheque_status
            ON payments(cheque_status, payment_date);

        DROP TRIGGER IF EXISTS trg_payments_instrument_validate_insert;
        CREATE TRIGGER trg_payments_instrument_validate_insert
        BEFORE INSERT ON payments BEGIN
            SELECT CASE WHEN UPPER(NEW.payment_mode)='CHEQUE' AND COALESCE(NEW.note,'') NOT LIKE 'VOID of %' AND
                (NEW.cheque_number IS NULL OR NEW.cheque_number='' OR NEW.cheque_number<>UPPER(REPLACE(REPLACE(REPLACE(REPLACE(NEW.cheque_number,' ',''),char(9),''),char(10),''),char(13),'')))
                THEN RAISE(ABORT,'normalized cheque number required') END;
            SELECT CASE WHEN UPPER(NEW.payment_mode)='CHEQUE' AND COALESCE(NEW.cheque_status,'')<>'PENDING'
                AND COALESCE(NEW.note,'') NOT LIKE 'VOID of %'
                THEN RAISE(ABORT,'new cheque must be pending') END;
            SELECT CASE WHEN UPPER(NEW.payment_mode)='UPI' AND COALESCE(NEW.note,'') NOT LIKE 'VOID of %' AND
                (NEW.upi_reference IS NULL OR NEW.upi_reference='' OR NEW.upi_reference<>UPPER(REPLACE(REPLACE(REPLACE(REPLACE(NEW.upi_reference,' ',''),char(9),''),char(10),''),char(13),'')))
                THEN RAISE(ABORT,'normalized UPI reference required') END;
            SELECT CASE WHEN NEW.cheque_status IS NOT NULL AND NEW.cheque_status NOT IN ('PENDING','CLEARED','BOUNCED','CANCELLED')
                THEN RAISE(ABORT,'invalid cheque status') END;
        END;

        CREATE TRIGGER trg_payments_no_update
        BEFORE UPDATE ON payments
        WHEN NEW.id IS NOT OLD.id OR NEW.student_id IS NOT OLD.student_id
          OR NEW.receipt_no IS NOT OLD.receipt_no OR NEW.fee_head_id IS NOT OLD.fee_head_id
          OR NEW.amount_due IS NOT OLD.amount_due OR NEW.amount_paid IS NOT OLD.amount_paid
          OR NEW.balance IS NOT OLD.balance OR NEW.payment_date IS NOT OLD.payment_date
          OR NEW.collected_by IS NOT OLD.collected_by OR NEW.payment_mode IS NOT OLD.payment_mode
          OR NEW.note IS NOT OLD.note OR NEW.hash IS NOT OLD.hash
          OR NEW.cheque_number IS NOT OLD.cheque_number OR NEW.upi_reference IS NOT OLD.upi_reference
        BEGIN
            SELECT RAISE(ABORT,'payment financial fields are immutable');
        END;

        DROP TRIGGER IF EXISTS trg_payments_instrument_validate_update;
        CREATE TRIGGER trg_payments_instrument_validate_update
        BEFORE UPDATE OF cheque_number,upi_reference,cheque_status,cheque_cleared_date,cheque_bank_reference ON payments BEGIN
            SELECT CASE WHEN NEW.cheque_number IS NOT OLD.cheque_number OR NEW.upi_reference IS NOT OLD.upi_reference
                THEN RAISE(ABORT,'payment references are immutable') END;
            SELECT CASE WHEN NEW.cheque_status NOT IN ('PENDING','CLEARED','BOUNCED','CANCELLED')
                THEN RAISE(ABORT,'invalid cheque status') END;
            SELECT CASE WHEN OLD.cheque_status<>'PENDING' AND NEW.cheque_status<>OLD.cheque_status
                THEN RAISE(ABORT,'terminal cheque status is immutable') END;
            SELECT CASE WHEN NEW.cheque_status='CLEARED' AND
                (COALESCE(NEW.cheque_cleared_date,'')='' OR COALESCE(NEW.cheque_bank_reference,'')='')
                THEN RAISE(ABORT,'cleared date and bank reference required') END;
        END;

        CREATE TABLE IF NOT EXISTS cashier_shifts (
            shift_id INTEGER PRIMARY KEY,
            cashier_user_id INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            system_cash_total REAL,
            actual_cash_entered REAL,
            variance REAL,
            handover_to INTEGER,
            approved_by INTEGER,
            FOREIGN KEY(cashier_user_id) REFERENCES users(id),
            FOREIGN KEY(handover_to) REFERENCES users(id),
            FOREIGN KEY(approved_by) REFERENCES users(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_cashier_open_shift
            ON cashier_shifts(cashier_user_id) WHERE closed_at IS NULL;
        """
    )


def payment_revenue_amount(payment_mode, cheque_status, amount_paid, note="") -> float:
    """Return recognized revenue; pending/bounced/cancelled cheques recognize zero."""
    amount = float(amount_paid or 0)
    if str(note or "").startswith("VOID of "):
        return amount
    mode = str(payment_mode or "").upper()
    return amount if mode != "CHEQUE" or str(cheque_status or "").upper() == "CLEARED" else 0.0


def uncleared_cheque_amount(payment_mode, cheque_status, amount_paid, note="") -> float:
    """Return the positive amount awaiting cheque clearance."""
    if str(note or "").startswith("VOID of "):
        return 0.0
    return float(amount_paid or 0) if str(payment_mode or "").upper() == "CHEQUE" and str(cheque_status or "").upper() == "PENDING" else 0.0


def set_cheque_status(conn: sqlite3.Connection, payment_id: int, status: str, user_id: int,
                      cleared_date: str = "", bank_reference: str = "") -> None:
    """Move a pending cheque to one terminal status and audit the decision."""
    status = str(status).strip().upper()
    if status not in _TERMINAL_CHEQUE_STATUSES:
        raise ValueError("Status must be CLEARED, BOUNCED, or CANCELLED.")
    row = conn.execute(
        "SELECT cheque_status,cheque_number FROM payments WHERE id=? AND UPPER(payment_mode)='CHEQUE'",
        (payment_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Cheque payment was not found.")
    old_status = str(row[0] or "PENDING").upper()
    if old_status != "PENDING":
        raise ValueError(f"Cheque is already {old_status}.")
    if status == "CLEARED":
        cleared_date = cleared_date.strip() or today_str()
        try:
            datetime.strptime(cleared_date, "%d-%m-%Y")
        except ValueError as exc:
            raise ValueError("Cleared date must be DD-MM-YYYY.") from exc
        bank_reference = normalize_reference(bank_reference)
        if not bank_reference:
            raise ValueError("Bank reference is required when clearing a cheque.")
    else:
        cleared_date = ""
        bank_reference = normalize_reference(bank_reference) or status
    conn.execute(
        "UPDATE payments SET cheque_status=?,cheque_cleared_date=?,cheque_bank_reference=? WHERE id=?",
        (status, cleared_date or None, bank_reference or None, payment_id),
    )
    conn.execute(
        "UPDATE cheque_tracker SET status=?,updated_at=? WHERE payment_id=?",
        (status, now_str(), payment_id),
    )
    log_action(conn, user_id, f"CHEQUE_{status}", "payments", payment_id, old_status,
               f"{status}; cheque={row[1]}; date={cleared_date}; bank_ref={bank_reference}")


def list_pending_cheques(conn) -> list[sqlite3.Row]:
    """Return pending cheque payments for lifecycle management."""
    return conn.execute(
        """
        SELECT p.id,p.receipt_no,p.cheque_number,p.amount_paid,p.payment_date,
               s.name AS student,u.username AS collected_by,ct.bank
        FROM payments p JOIN students s ON s.id=p.student_id
        LEFT JOIN users u ON u.id=p.collected_by
        LEFT JOIN cheque_tracker ct ON ct.payment_id=p.id
        WHERE UPPER(p.payment_mode)='CHEQUE' AND p.cheque_status='PENDING'
              AND COALESCE(p.note,'') NOT LIKE 'VOID of %'
        ORDER BY p.payment_date,p.id
        """
    ).fetchall()


def open_shift(conn: sqlite3.Connection, cashier_user_id: int) -> int:
    """Open one shift for a cashier; parallel open shifts are forbidden."""
    cursor = conn.execute(
        "INSERT INTO cashier_shifts(cashier_user_id,opened_at) VALUES(?,?)",
        (cashier_user_id, now_str()),
    )
    log_action(conn, cashier_user_id, "CASHIER_SHIFT_OPEN", "cashier_shifts", cursor.lastrowid)
    return int(cursor.lastrowid)


def close_shift(conn: sqlite3.Connection, shift_id: int, actual_cash_entered: float,
                handover_to: int | None, approved_by: int) -> dict:
    """Close a shift using recognized CASH transactions collected during its window."""
    shift = conn.execute(
        "SELECT cashier_user_id,opened_at,closed_at FROM cashier_shifts WHERE shift_id=?", (shift_id,)
    ).fetchone()
    if shift is None or shift[2] is not None:
        raise ValueError("Open cashier shift was not found.")
    closed_at = now_str()
    system_total = conn.execute(
        """
        SELECT COALESCE(SUM(p.amount_paid),0) FROM payments p
        JOIN receipts r ON r.receipt_no=p.receipt_no
        WHERE p.collected_by=? AND UPPER(p.payment_mode)='CASH'
          AND datetime(substr(r.printed_at,7,4)||'-'||substr(r.printed_at,4,2)||'-'||substr(r.printed_at,1,2)||substr(r.printed_at,11))
              BETWEEN datetime(substr(?,7,4)||'-'||substr(?,4,2)||'-'||substr(?,1,2)||substr(?,11))
                  AND datetime(substr(?,7,4)||'-'||substr(?,4,2)||'-'||substr(?,1,2)||substr(?,11))
        """,
        (shift[0], shift[1], shift[1], shift[1], shift[1], closed_at, closed_at, closed_at, closed_at),
    ).fetchone()[0]
    actual = float(actual_cash_entered)
    variance = actual - float(system_total or 0)
    conn.execute(
        """UPDATE cashier_shifts SET closed_at=?,system_cash_total=?,actual_cash_entered=?,
           variance=?,handover_to=?,approved_by=? WHERE shift_id=?""",
        (closed_at, system_total, actual, variance, handover_to, approved_by, shift_id),
    )
    log_action(conn, approved_by, "CASHIER_SHIFT_CLOSE", "cashier_shifts", shift_id, None,
               f"system={system_total}; actual={actual}; variance={variance}; handover_to={handover_to}")
    return {"shift_id": shift_id, "system_cash_total": float(system_total or 0),
            "actual_cash_entered": actual, "variance": variance}
