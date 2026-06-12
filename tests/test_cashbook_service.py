"""Regression tests for cashbook service helpers."""

from __future__ import annotations

import sqlite3

from cashbook_service import (
    add_transaction,
    collection_candidates,
    ensure_student_extra_columns,
    import_collection_receipts,
    install_cashbook_schema,
    summary,
    vehicle_expenses_by_head,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);
        CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER DEFAULT 1);
        CREATE TABLE students(
            id INTEGER PRIMARY KEY,
            name TEXT,
            father_name TEXT,
            mother_name TEXT,
            class TEXT,
            section TEXT,
            aadhaar TEXT,
            phone TEXT,
            address TEXT,
            gender TEXT,
            status TEXT,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE payments(
            id INTEGER PRIMARY KEY,
            receipt_no TEXT,
            student_id INTEGER,
            payment_mode TEXT,
            cheque_number TEXT,
            upi_reference TEXT
        );
        CREATE TABLE receipts(
            id INTEGER PRIMARY KEY,
            receipt_no TEXT,
            total_paid REAL,
            receipt_type TEXT,
            printed_at TEXT,
            student_id INTEGER
        );
        """
    )
    ensure_student_extra_columns(conn)
    install_cashbook_schema(conn)
    return conn


def test_manual_income_expense_summary_and_vehicle_heads() -> None:
    conn = _conn()
    income_head = conn.execute("SELECT id FROM cashbook_heads WHERE name='Donation'").fetchone()[0]
    vehicle_head = conn.execute("SELECT id FROM cashbook_heads WHERE name='Vehicle Fuel'").fetchone()[0]

    add_transaction(
        conn,
        txn_date="12-06-2026",
        txn_type="INCOME",
        head_id=income_head,
        description="Donation",
        amount="1000.00",
        payment_method="CASH",
        account_name="CASH",
    )
    add_transaction(
        conn,
        txn_date="12-06-2026",
        txn_type="EXPENSE",
        head_id=vehicle_head,
        description="Diesel",
        amount="250.00",
        payment_method="UPI",
        account_name="CBI",
    )

    state = summary(conn, "12-06-2026", "12-06-2026")
    assert state["income_total"] == 1000
    assert state["expense_total"] == 250
    assert state["balance"] == 750
    vehicle = vehicle_expenses_by_head(conn, "12-06-2026", "12-06-2026")
    assert [(row["name"], row["total"]) for row in vehicle] == [("Vehicle Fuel", 250)]


def test_import_collection_receipts_is_idempotent() -> None:
    conn = _conn()
    student_id = conn.execute("INSERT INTO students(name,class,status) VALUES('Asha','Class 1','ACTIVE')").lastrowid
    conn.execute(
        "INSERT INTO receipts(id,receipt_no,total_paid,receipt_type,printed_at,student_id) VALUES(1,'RCP-2026-000001',500,'MAIN RECEIPT','12-06-2026 10:00:00',?)",
        (student_id,),
    )
    conn.execute(
        "INSERT INTO payments(receipt_no,student_id,payment_mode,upi_reference) VALUES('RCP-2026-000001',?,'UPI','UTR123')",
        (student_id,),
    )

    candidates = collection_candidates(conn, include_main=True, include_small=False, include_exemption=False)
    assert [row["receipt_id"] for row in candidates] == [1]
    assert import_collection_receipts(conn, [1], "CBI", None) == 1
    assert import_collection_receipts(conn, [1], "CBI", None) == 0
    state = summary(conn, "12-06-2026", "12-06-2026")
    assert state["income_total"] == 500
