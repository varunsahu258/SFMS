"""Academic-year charge and payment-allocation ledger for SFMS."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from utils import now_str

DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(str(value).split(" ")[0], date_format)
        except ValueError:
            continue
    return None


def active_academic_year(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT label FROM academic_years WHERE is_active=1 LIMIT 1").fetchone()
    return str(row[0]) if row else ""


def install_ledger_schema(conn: sqlite3.Connection) -> None:
    """Install charge/allocation tables, constraints, views, and legacy columns."""
    # Earlier builds represented reversals as negative allocations. Normalize them
    # before reinstating immutability triggers so all current reversals are positive.
    if "payment_allocations" in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        conn.execute("DROP TRIGGER IF EXISTS trg_allocations_no_update")
        conn.execute("UPDATE payment_allocations SET amount_allocated=ABS(amount_allocated) WHERE allocation_type='REVERSAL' AND amount_allocated<0")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS student_charges (
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL,
            academic_year TEXT NOT NULL,
            fee_structure_id INTEGER,
            fee_head_id INTEGER NOT NULL,
            original_amount REAL NOT NULL CHECK(original_amount >= 0),
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED','CANCELLED')),
            created_at TEXT NOT NULL,
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(fee_structure_id) REFERENCES fee_structure(id),
            FOREIGN KEY(fee_head_id) REFERENCES fee_heads(id),
            UNIQUE(student_id, fee_structure_id)
        );

        CREATE TABLE IF NOT EXISTS payment_allocations (
            id INTEGER PRIMARY KEY,
            payment_id INTEGER NOT NULL,
            charge_id INTEGER NOT NULL,
            amount_allocated REAL NOT NULL CHECK(amount_allocated > 0),
            allocation_type TEXT NOT NULL DEFAULT 'PAYMENT'
                CHECK(allocation_type IN ('PAYMENT','ADVANCE','REVERSAL','MIGRATION')),
            created_at TEXT NOT NULL,
            FOREIGN KEY(payment_id) REFERENCES payments(id),
            FOREIGN KEY(charge_id) REFERENCES student_charges(id),
            UNIQUE(payment_id, charge_id)
        );

        CREATE TABLE IF NOT EXISTS charge_adjustments (
            id INTEGER PRIMARY KEY,
            charge_id INTEGER NOT NULL,
            adjustment_type TEXT NOT NULL CHECK(adjustment_type IN ('DISCOUNT','EXEMPTION')),
            amount REAL NOT NULL CHECK(amount > 0),
            source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            reason TEXT,
            approved_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(charge_id) REFERENCES student_charges(id),
            FOREIGN KEY(approved_by) REFERENCES users(id),
            UNIQUE(source_table, source_id, charge_id)
        );

        CREATE TABLE IF NOT EXISTS exemption_charges (
            exemption_id INTEGER NOT NULL,
            charge_id INTEGER NOT NULL,
            PRIMARY KEY(exemption_id, charge_id),
            FOREIGN KEY(exemption_id) REFERENCES exemptions(id),
            FOREIGN KEY(charge_id) REFERENCES student_charges(id)
        );

        CREATE INDEX IF NOT EXISTS idx_charges_student_year
            ON student_charges(student_id, academic_year, fee_head_id);
        CREATE INDEX IF NOT EXISTS idx_allocations_charge ON payment_allocations(charge_id);
        CREATE INDEX IF NOT EXISTS idx_allocations_payment ON payment_allocations(payment_id);
        CREATE INDEX IF NOT EXISTS idx_adjustments_charge ON charge_adjustments(charge_id);

        DROP TRIGGER IF EXISTS trg_allocations_validate_insert;
        CREATE TRIGGER trg_allocations_validate_insert
        BEFORE INSERT ON payment_allocations BEGIN
            SELECT CASE WHEN NEW.amount_allocated <= 0
                THEN RAISE(ABORT, 'allocation amount must be positive') END;
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM payments p JOIN student_charges c
                  ON c.student_id=p.student_id AND c.fee_head_id=p.fee_head_id
                WHERE p.id=NEW.payment_id AND c.id=NEW.charge_id
            ) THEN RAISE(ABORT, 'payment allocation does not match charge') END;
            SELECT CASE WHEN NEW.allocation_type <> 'REVERSAL' AND NEW.amount_allocated >
                COALESCE((SELECT balance-pending_cheques FROM charge_ledger
                          WHERE charge_id=NEW.charge_id),0) + 0.005
                THEN RAISE(ABORT, 'payment allocation exceeds available charge balance') END;
            SELECT CASE WHEN NEW.allocation_type = 'REVERSAL' AND NEW.amount_allocated >
                COALESCE((SELECT
                    COALESCE(SUM(CASE WHEN allocation_type <> 'REVERSAL' THEN amount_allocated ELSE 0 END),0)
                    - COALESCE(SUM(CASE WHEN allocation_type = 'REVERSAL' THEN ABS(amount_allocated) ELSE 0 END),0)
                    FROM payment_allocations WHERE charge_id=NEW.charge_id),0) + 0.005
                THEN RAISE(ABORT, 'reversal exceeds allocated payments') END;
        END;
        CREATE TRIGGER IF NOT EXISTS trg_allocations_no_update
        BEFORE UPDATE ON payment_allocations BEGIN
            SELECT RAISE(ABORT, 'payment allocations cannot be updated');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_allocations_no_delete
        BEFORE DELETE ON payment_allocations BEGIN
            SELECT RAISE(ABORT, 'payment allocations cannot be deleted');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_adjustments_no_update
        BEFORE UPDATE ON charge_adjustments BEGIN
            SELECT RAISE(ABORT, 'charge adjustments cannot be updated');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_adjustments_no_delete
        BEFORE DELETE ON charge_adjustments BEGIN
            SELECT RAISE(ABORT, 'charge adjustments cannot be deleted');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_charges_financial_fields_no_update
        BEFORE UPDATE OF student_id, academic_year, fee_structure_id, fee_head_id,
                         original_amount, due_date, created_at ON student_charges BEGIN
            SELECT RAISE(ABORT, 'student charge financial fields cannot be updated');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_charges_no_delete
        BEFORE DELETE ON student_charges BEGIN
            SELECT RAISE(ABORT, 'student charges cannot be deleted');
        END;

        DROP VIEW IF EXISTS charge_ledger;
        CREATE VIEW charge_ledger AS
        WITH allocation_totals AS (
            SELECT a.charge_id,
                   SUM(CASE WHEN a.allocation_type <> 'REVERSAL'
                                  AND (UPPER(COALESCE(p.payment_mode,'')) <> 'CHEQUE'
                                       OR p.cheque_status='CLEARED')
                            THEN a.amount_allocated ELSE 0 END) AS successful_paid,
                   SUM(CASE WHEN a.allocation_type='REVERSAL'
                            THEN ABS(a.amount_allocated) ELSE 0 END) AS reversed,
                   SUM(CASE WHEN a.allocation_type <> 'REVERSAL'
                                  AND UPPER(COALESCE(p.payment_mode,''))='CHEQUE'
                                  AND p.cheque_status='PENDING'
                            THEN a.amount_allocated ELSE 0 END) AS pending_cheques
            FROM payment_allocations a JOIN payments p ON p.id=a.payment_id
            GROUP BY a.charge_id
        ), adjustment_totals AS (
            SELECT charge_id,
                   SUM(CASE WHEN adjustment_type='DISCOUNT' THEN amount ELSE 0 END) AS discounts,
                   SUM(CASE WHEN adjustment_type='EXEMPTION' THEN amount ELSE 0 END) AS exemptions
            FROM charge_adjustments GROUP BY charge_id
        )
        SELECT c.id AS charge_id,c.student_id,c.academic_year,c.fee_structure_id,
               c.fee_head_id,c.original_amount,c.due_date,c.status,
               COALESCE(a.successful_paid,0) AS paid,
               COALESCE(a.reversed,0) AS reversed,
               COALESCE(a.pending_cheques,0) AS pending_cheques,
               COALESCE(x.discounts,0) AS discounts,
               COALESCE(x.exemptions,0) AS exemptions,
               COALESCE(x.discounts,0)+COALESCE(x.exemptions,0) AS adjustments,
               c.original_amount-COALESCE(a.successful_paid,0)-COALESCE(x.discounts,0)
                 -COALESCE(x.exemptions,0)+COALESCE(a.reversed,0) AS balance
        FROM student_charges c
        LEFT JOIN allocation_totals a ON a.charge_id=c.id
        LEFT JOIN adjustment_totals x ON x.charge_id=c.id;
        """
    )
    if "academic_year" not in _columns(conn, "discounts"):
        conn.execute("ALTER TABLE discounts ADD COLUMN academic_year TEXT")
    if "charge_id" not in _columns(conn, "discounts"):
        conn.execute("ALTER TABLE discounts ADD COLUMN charge_id INTEGER REFERENCES student_charges(id)")


def ensure_student_charges(
    conn: sqlite3.Connection,
    academic_year: str | None = None,
    student_id: int | None = None,
) -> None:
    """Create one immutable charge for each matching student fee-structure row."""
    conditions = ["fs.academic_year = COALESCE(?, fs.academic_year)", "s.is_active = 1"]
    params: list = [academic_year]
    if student_id is not None:
        conditions.append("s.id = ?")
        params.append(student_id)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO student_charges (
            student_id, academic_year, fee_structure_id, fee_head_id,
            original_amount, due_date, status, created_at
        )
        SELECT s.id, fs.academic_year, fs.id, fs.fee_head_id,
               fs.amount, fs.due_date, 'OPEN', ?
        FROM students s
        JOIN fee_structure fs ON fs.class=s.class
        WHERE {' AND '.join(conditions)}
        """,
        (now_str(), *params),
    )


def charge_rows(
    conn: sqlite3.Connection,
    student_id: int,
    academic_year: str | None = None,
    register_types: tuple[str, ...] | None = None,
) -> list[sqlite3.Row]:
    """Return authoritative charge-ledger rows for one student and year."""
    year = academic_year or active_academic_year(conn)
    ensure_student_charges(conn, year, student_id)
    params: list = [student_id, year]
    register_sql = ""
    if register_types:
        register_sql = f" AND fh.register_type IN ({','.join('?' for _ in register_types)})"
        params.extend(register_types)
    return conn.execute(
        f"""
        SELECT l.*, fh.name AS fee_head, fh.register_type
        FROM charge_ledger l
        JOIN fee_heads fh ON fh.id=l.fee_head_id
        WHERE l.student_id=? AND l.academic_year=? AND l.status <> 'CANCELLED'
              {register_sql}
        ORDER BY l.due_date, fh.name, l.charge_id
        """,
        params,
    ).fetchall()


def charge_outstanding(conn: sqlite3.Connection, charge_id: int) -> float:
    """Return a charge's derived balance; payment-row balance snapshots are ignored."""
    row = conn.execute("SELECT balance FROM charge_ledger WHERE charge_id=?", (charge_id,)).fetchone()
    if row is None:
        raise ValueError("Student charge was not found.")
    return float(row[0] or 0)


def outstanding_total(conn: sqlite3.Connection, student_id: int, academic_year: str | None = None) -> float:
    """Return positive outstanding charges without mixing academic years."""
    rows = charge_rows(conn, student_id, academic_year)
    return sum(max(float(row["balance"] or 0), 0.0) for row in rows)


def all_outstanding_total(conn: sqlite3.Connection, student_id: int) -> float:
    """Return positive outstanding charges across every academic year."""
    current_year = active_academic_year(conn)
    if current_year:
        ensure_student_charges(conn, current_year, student_id)
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN balance>0 THEN balance ELSE 0 END),0) FROM charge_ledger WHERE student_id=? AND status<>'CANCELLED'",
        (student_id,),
    ).fetchone()
    return float(row[0] or 0)


def allocate_payment(
    conn: sqlite3.Connection,
    payment_id: int,
    charge_id: int,
    amount: float,
    allocation_type: str = "PAYMENT",
) -> None:
    """Append an immutable allocation after validating student/head consistency."""
    payment = conn.execute(
        "SELECT student_id, fee_head_id FROM payments WHERE id=?", (payment_id,)
    ).fetchone()
    charge = conn.execute(
        "SELECT student_id, fee_head_id FROM student_charges WHERE id=?", (charge_id,)
    ).fetchone()
    if payment is None or charge is None:
        raise ValueError("Payment or student charge was not found.")
    if payment[0] != charge[0] or payment[1] != charge[1]:
        raise ValueError("Payment allocation does not match the student charge.")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Allocation amount must be positive.")
    if allocation_type == "REVERSAL":
        row = conn.execute("SELECT paid,reversed FROM charge_ledger WHERE charge_id=?", (charge_id,)).fetchone()
        if row is None or amount > float(row[0] or 0) - float(row[1] or 0) + 0.005:
            raise ValueError("Reversal exceeds the charge's allocated payments.")
    else:
        balance_row = conn.execute("SELECT balance-pending_cheques FROM charge_ledger WHERE charge_id=?", (charge_id,)).fetchone()
        if balance_row is None or amount > float(balance_row[0] or 0) + 0.005:
            raise ValueError("Payment allocation exceeds the charge's outstanding balance.")
    conn.execute(
        """
        INSERT INTO payment_allocations
            (payment_id, charge_id, amount_allocated, allocation_type, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (payment_id, charge_id, amount, allocation_type, now_str()),
    )


def add_adjustment(
    conn: sqlite3.Connection,
    charge_id: int,
    adjustment_type: str,
    amount: float,
    source_table: str,
    source_id: int,
    reason: str,
    approved_by: int,
) -> None:
    """Append a discount/exemption against one explicit charge."""
    row = conn.execute("SELECT balance FROM charge_ledger WHERE charge_id=?", (charge_id,)).fetchone()
    if row is None:
        raise ValueError("Student charge was not found.")
    balance = max(float(row[0] or 0), 0.0)
    if amount <= 0 or amount > balance + 0.005:
        raise ValueError("Adjustment cannot exceed the charge's outstanding balance.")
    conn.execute(
        """
        INSERT INTO charge_adjustments
            (charge_id, adjustment_type, amount, source_table, source_id,
             reason, approved_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (charge_id, adjustment_type, amount, source_table, source_id, reason, approved_by, now_str()),
    )


def _year_for_date(conn: sqlite3.Connection, value) -> str | None:
    payment_date = _parse_date(value)
    if payment_date is None:
        return None
    for label, start, end in conn.execute("SELECT label,start_date,end_date FROM academic_years"):
        start_date, end_date = _parse_date(start), _parse_date(end)
        if start_date and end_date and start_date <= payment_date <= end_date:
            return str(label)
    return None


def _legacy_charge(
    conn: sqlite3.Connection, student_id: int, academic_year: str, fee_head_id: int,
    amount_due: float | None = None,
) -> int | None:
    """Find or conservatively create one legacy charge without using another year's money."""
    existing = conn.execute(
        "SELECT id FROM student_charges WHERE student_id=? AND academic_year=? AND fee_head_id=? ORDER BY id",
        (student_id, academic_year, fee_head_id),
    ).fetchall()
    if len(existing) == 1:
        return int(existing[0][0])
    params: list = [academic_year, fee_head_id]
    amount_sql = ""
    if amount_due is not None and amount_due >= 0:
        amount_sql = " AND ABS(fs.amount-?)<0.005"
        params.append(amount_due)
    structures = conn.execute(
        f"SELECT fs.id,fs.amount,fs.due_date FROM fee_structure fs WHERE fs.academic_year=? AND fs.fee_head_id=?{amount_sql}",
        params,
    ).fetchall()
    if len(structures) == 1:
        structure_id, original_amount, due_date = structures[0]
    elif amount_due is not None and amount_due >= 0:
        structure_id, original_amount, due_date = None, amount_due, None
    else:
        return None
    conn.execute(
        "INSERT OR IGNORE INTO student_charges(student_id,academic_year,fee_structure_id,fee_head_id,original_amount,due_date,status,created_at) VALUES (?,?,?,?,?,?,'OPEN',?)",
        (student_id, academic_year, structure_id, fee_head_id, original_amount, due_date, now_str()),
    )
    row = conn.execute(
        "SELECT id FROM student_charges WHERE student_id=? AND academic_year=? AND fee_head_id=? ORDER BY id DESC LIMIT 1",
        (student_id, academic_year, fee_head_id),
    ).fetchone()
    return int(row[0]) if row else None


def _migration_warning(conn: sqlite3.Connection, table: str, record_id: int, detail: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM audit_log WHERE action='LEDGER_MIGRATION_UNALLOCATED' AND table_name=? AND record_id=? LIMIT 1",
        (table, str(record_id)),
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,old_value,new_value,tamper_attempt) VALUES (?,NULL,'LEDGER_MIGRATION_UNALLOCATED',?,?,NULL,?,1)",
            (now_str(), table, str(record_id), detail),
        )


def migrate_legacy_ledger(conn: sqlite3.Connection) -> None:
    """Backfill unambiguous legacy payments/adjustments without cross-year leakage."""
    install_ledger_schema(conn)
    current_year = active_academic_year(conn)
    if current_year:
        ensure_student_charges(conn, current_year)
    payments = conn.execute(
        """
        SELECT p.* FROM payments p
        WHERE NOT EXISTS (SELECT 1 FROM payment_allocations a WHERE a.payment_id=p.id)
        ORDER BY p.id
        """
    ).fetchall()
    for payment in payments:
        note = str(payment["note"] or "") if hasattr(payment, "keys") else str(payment[10] or "")
        payment_id = payment["id"] if hasattr(payment, "keys") else payment[0]
        if note.startswith("VOID of "):
            original = note[len("VOID of "):]
            allocations = conn.execute(
                """
                SELECT a.charge_id, a.amount_allocated
                FROM payment_allocations a JOIN payments p ON p.id=a.payment_id
                WHERE p.receipt_no=?
                """,
                (original,),
            ).fetchall()
            if not allocations:
                _migration_warning(conn, "payments", payment_id, "Void references a receipt with no allocated payment")
            for allocation in allocations:
                conn.execute(
                    "INSERT OR IGNORE INTO payment_allocations(payment_id,charge_id,amount_allocated,allocation_type,created_at) VALUES (?,?,?,?,?)",
                    (payment_id, allocation[0], abs(float(allocation[1])), "REVERSAL", now_str()),
                )
            continue
        payment_date = payment["payment_date"] if hasattr(payment, "keys") else payment[7]
        year = _year_for_date(conn, payment_date)
        if not year:
            _migration_warning(conn, "payments", payment_id, "No academic year contains the payment date")
            continue
        student_id = payment["student_id"] if hasattr(payment, "keys") else payment[1]
        fee_head_id = payment["fee_head_id"] if hasattr(payment, "keys") else payment[3]
        amount_due = payment["amount_due"] if hasattr(payment, "keys") else payment[4]
        charge_id = _legacy_charge(conn, student_id, year, fee_head_id, float(amount_due or 0))
        if charge_id is not None:
            amount = payment["amount_paid"] if hasattr(payment, "keys") else payment[5]
            kind = "ADVANCE" if note == "ADVANCE" else "MIGRATION"
            try:
                conn.execute(
                    "INSERT INTO payment_allocations(payment_id,charge_id,amount_allocated,allocation_type,created_at) VALUES (?,?,?,?,?)",
                    (payment_id, charge_id, float(amount or 0), kind, now_str()),
                )
            except sqlite3.IntegrityError as exc:
                _migration_warning(conn, "payments", payment_id, f"Legacy allocation rejected: {exc}")
        else:
            _migration_warning(conn, "payments", payment_id, "No unambiguous student charge matched the legacy payment")

    for discount in conn.execute(
        "SELECT id,student_id,fee_head_id,amount,reason,approved_by,created_at,academic_year,charge_id FROM discounts WHERE charge_id IS NULL"
    ).fetchall():
        year = discount[7] or _year_for_date(conn, discount[6])
        if not year:
            continue
        charge_id = _legacy_charge(conn, discount[1], year, discount[2])
        if charge_id is not None:
            conn.execute("UPDATE discounts SET academic_year=?, charge_id=? WHERE id=?", (year, charge_id, discount[0]))
            try:
                add_adjustment(conn, charge_id, "DISCOUNT", float(discount[3]), "discounts", discount[0], discount[4], discount[5])
            except ValueError as exc:
                _migration_warning(conn, "discounts", discount[0], str(exc))

    for exemption in conn.execute(
        "SELECT id,student_id,academic_year,fee_head_ids,reason,approved_by,created_at FROM exemptions"
    ).fetchall():
        try:
            head_ids = [int(value) for value in json.loads(exemption[3] or "[]")]
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for fee_head_id in head_ids:
            charge_id = _legacy_charge(conn, exemption[1], exemption[2], fee_head_id)
            if charge_id is None:
                _migration_warning(conn, "exemptions", exemption[0], f"No unambiguous charge for fee_head_id={fee_head_id}")
                continue
            charge = conn.execute("SELECT balance FROM charge_ledger WHERE charge_id=?", (charge_id,)).fetchone()
            if charge is None or float(charge[0] or 0) <= 0:
                continue
            conn.execute("INSERT OR IGNORE INTO exemption_charges(exemption_id,charge_id) VALUES (?,?)", (exemption[0], charge_id))
            try:
                add_adjustment(conn, charge_id, "EXEMPTION", float(charge[0]), "exemptions", exemption[0], exemption[4], exemption[5])
            except (sqlite3.IntegrityError, ValueError):
                pass
    conn.execute(
        "INSERT INTO settings(key,value) VALUES ('ledger_schema_version','1') ON CONFLICT(key) DO UPDATE SET value='1'"
    )
