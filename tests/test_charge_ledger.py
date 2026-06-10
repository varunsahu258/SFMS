"""Regression tests for academic-year charge isolation and immutable allocations."""

from __future__ import annotations

import sqlite3

import pytest

from ledger import (
    add_adjustment,
    allocate_payment,
    charge_outstanding,
    charge_rows,
    ensure_student_charges,
    install_ledger_schema,
    migrate_legacy_ledger,
)
from payment_controls import migrate_payment_controls


def schema(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,class TEXT,is_active INTEGER,status TEXT,aadhaar TEXT);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT,register_type TEXT,is_active INTEGER);
        CREATE TABLE fee_structure(id INTEGER PRIMARY KEY,academic_year TEXT,class TEXT,fee_head_id INTEGER,amount REAL,due_date TEXT);
        CREATE TABLE academic_years(id INTEGER PRIMARY KEY,label TEXT,start_date TEXT,end_date TEXT,is_active INTEGER);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,receipt_no TEXT,fee_head_id INTEGER,amount_due REAL,amount_paid REAL,balance REAL,payment_date TEXT,collected_by INTEGER,payment_mode TEXT,note TEXT,hash TEXT);
        CREATE TABLE cheque_tracker(id INTEGER PRIMARY KEY,payment_id INTEGER,cheque_no TEXT,bank TEXT,amount REAL,collected_on TEXT,status TEXT,updated_at TEXT);
        CREATE TABLE receipts(id INTEGER PRIMARY KEY,receipt_no TEXT UNIQUE,student_id INTEGER,total_paid REAL,receipt_type TEXT,printed_at TEXT,printed_by INTEGER,reprint_count INTEGER,last_reprint_at TEXT,last_reprint_by INTEGER);
        CREATE TABLE receipt_hashes(receipt_no TEXT PRIMARY KEY,sha256_hash TEXT,created_at TEXT);
        CREATE TABLE discounts(id INTEGER PRIMARY KEY,student_id INTEGER,fee_head_id INTEGER,amount REAL,reason TEXT,approved_by INTEGER,created_at TEXT);
        CREATE TABLE exemptions(id INTEGER PRIMARY KEY,student_id INTEGER,academic_year TEXT,fee_head_ids TEXT,reason TEXT,approved_by INTEGER,created_at TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        INSERT INTO users VALUES(1,'admin');
        INSERT INTO students VALUES(1,'Student','Class 1',1,'ACTIVE','123456789012');
        INSERT INTO fee_heads VALUES(1,'Tuition','BIG',1);
        INSERT INTO academic_years VALUES(1,'2025-26','01-04-2025','31-03-2026',0);
        INSERT INTO academic_years VALUES(2,'2026-27','01-04-2026','31-03-2027',1);
        INSERT INTO fee_structure VALUES(1,'2025-26','Class 1',1,10000,'01-04-2025');
        INSERT INTO fee_structure VALUES(2,'2026-27','Class 1',1,12000,'01-04-2026');
        """
    )
    migrate_payment_controls(conn)
    install_ledger_schema(conn)


def insert_payment(conn, payment_id: int, receipt: str, amount: float, date: str) -> None:
    conn.execute(
        "INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_due,amount_paid,balance,payment_date,collected_by,payment_mode,note,hash) VALUES(?,?,?,1,?,?,?, ?,1,'CASH','', 'hash')",
        (payment_id, 1, receipt, amount, amount, 0, date),
    )


def test_cross_year_partial_discount_and_void_are_isolated() -> None:
    conn = sqlite3.connect(":memory:")
    schema(conn)
    ensure_student_charges(conn, "2025-26", 1)
    ensure_student_charges(conn, "2026-27", 1)
    old_charge = charge_rows(conn, 1, "2025-26")[0]
    new_charge = charge_rows(conn, 1, "2026-27")[0]

    insert_payment(conn, 1, "OLD", 10000, "01-06-2025")
    allocate_payment(conn, 1, old_charge["charge_id"], 10000)
    assert charge_rows(conn, 1, "2025-26")[0]["balance"] == 0
    assert charge_rows(conn, 1, "2026-27")[0]["balance"] == 12000

    conn.execute("INSERT INTO discounts VALUES(1,1,1,500,'owner approved',1,'01-05-2026','2026-27',?)", (new_charge["charge_id"],))
    add_adjustment(conn, new_charge["charge_id"], "DISCOUNT", 500, "discounts", 1, "owner approved", 1)
    assert charge_rows(conn, 1, "2026-27")[0]["balance"] == 11500

    insert_payment(conn, 2, "NEW", 4000, "01-06-2026")
    allocate_payment(conn, 2, new_charge["charge_id"], 4000)
    assert charge_rows(conn, 1, "2026-27")[0]["balance"] == 7500

    insert_payment(conn, 3, "VOID", -4000, "02-06-2026")
    allocate_payment(conn, 3, new_charge["charge_id"], 4000, "REVERSAL")
    assert charge_rows(conn, 1, "2026-27")[0]["balance"] == 11500
    assert charge_rows(conn, 1, "2025-26")[0]["balance"] == 0


def test_legacy_payment_is_backfilled_only_into_its_date_year() -> None:
    conn = sqlite3.connect(":memory:")
    schema(conn)
    insert_payment(conn, 1, "LEGACY", 10000, "01-06-2025")
    migrate_legacy_ledger(conn)
    assert charge_rows(conn, 1, "2025-26")[0]["balance"] == 0
    assert charge_rows(conn, 1, "2026-27")[0]["balance"] == 12000


def test_overallocation_is_rejected_and_inactive_students_are_not_charged():
    conn = sqlite3.connect(":memory:")
    schema(conn)
    conn.execute("INSERT INTO students VALUES(2,'Archived','Class 1',0,'LEFT','')")
    ensure_student_charges(conn, "2026-27")
    assert conn.execute("SELECT COUNT(*) FROM student_charges WHERE student_id=2").fetchone()[0] == 0
    charge = charge_rows(conn, 1, "2026-27")[0]
    insert_payment(conn, 1, "RCP-OVER", 12500, "01-05-2026")
    with pytest.raises(ValueError, match="exceeds"):
        allocate_payment(conn, 1, charge["charge_id"], 12500)
    assert conn.execute("SELECT COUNT(*) FROM payment_allocations WHERE payment_id=1").fetchone()[0] == 0


def test_partial_and_full_payment_voids_restore_derived_outstanding():
    """The real void routine inserts positive reversals and restores each charge."""
    import sys
    import types

    sys.modules.setdefault("bcrypt", types.SimpleNamespace(checkpw=lambda *_args: False))
    from ui_void_payment import create_void_receipt

    conn = sqlite3.connect(":memory:")
    schema(conn)
    conn.execute("UPDATE fee_structure SET amount=100 WHERE academic_year='2026-27'")
    ensure_student_charges(conn, "2026-27", 1)
    charge_id = charge_rows(conn, 1, "2026-27")[0]["charge_id"]

    # Deliberately wrong legacy balance snapshots prove that the ledger ignores them.
    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_due,amount_paid,balance,payment_date,collected_by,payment_mode,note,hash) VALUES(1,1,'PART',1,100,40,60,'01-05-2026',1,'CASH','','hash')")
    conn.execute("INSERT INTO receipts VALUES(1,'PART',1,40,'BIG','01-05-2026',1,0,NULL,NULL)")
    allocate_payment(conn, 1, charge_id, 40, "PAYMENT")
    assert charge_outstanding(conn, charge_id) == 60
    create_void_receipt(
        conn, {"student_id": 1, "receipt_type": "BIG"},
        [dict(conn.execute("SELECT * FROM payments WHERE id=1").fetchone())],
        "PART", "test partial void", 1,
    )
    assert charge_outstanding(conn, charge_id) == 100

    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_due,amount_paid,balance,payment_date,collected_by,payment_mode,note,hash) VALUES(3,1,'FULL',1,100,100,0,'03-05-2026',1,'CASH','','hash')")
    conn.execute("INSERT INTO receipts VALUES(3,'FULL',1,100,'BIG','03-05-2026',1,0,NULL,NULL)")
    allocate_payment(conn, 3, charge_id, 100, "PAYMENT")
    assert charge_outstanding(conn, charge_id) == 0
    create_void_receipt(
        conn, {"student_id": 1, "receipt_type": "BIG"},
        [dict(conn.execute("SELECT * FROM payments WHERE id=3").fetchone())],
        "FULL", "test full void", 1,
    )
    assert charge_outstanding(conn, charge_id) == 100

    reversals = conn.execute(
        "SELECT amount_allocated FROM payment_allocations WHERE allocation_type='REVERSAL' ORDER BY id"
    ).fetchall()
    assert [row[0] for row in reversals] == [40, 100]
    assert conn.execute(
        "SELECT COUNT(*) FROM payments WHERE note LIKE 'VOID of %' AND balance=0"
    ).fetchone()[0] == 2
