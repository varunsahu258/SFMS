"""Authoritative ledger balance and advance-allocation regression tests."""

import sqlite3
from decimal import Decimal

from ledger_service import LedgerService


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,class TEXT,aadhaar TEXT);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE academic_years(id INTEGER PRIMARY KEY,label TEXT,is_active INTEGER);
        CREATE TABLE student_charges(id INTEGER PRIMARY KEY,student_id INTEGER,academic_year TEXT,
            fee_head_id INTEGER,original_amount REAL,due_date TEXT,status TEXT);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,fee_head_id INTEGER,
            payment_mode TEXT,cheque_status TEXT,payment_intent TEXT,
            allocated_academic_year_id INTEGER,allocated_term TEXT);
        CREATE TABLE payment_allocations(id INTEGER PRIMARY KEY,payment_id INTEGER,charge_id INTEGER,
            amount_allocated REAL,allocation_type TEXT);
        CREATE TABLE charge_adjustments(id INTEGER PRIMARY KEY,charge_id INTEGER,
            adjustment_type TEXT,amount REAL);
        INSERT INTO students VALUES(1,'Student','1','1234');
        INSERT INTO fee_heads VALUES(1,'Tuition');
        INSERT INTO academic_years VALUES(1,'2025-26',0),(2,'2026-27',1);
        INSERT INTO student_charges VALUES(1,1,'2026-27',1,100,'01-06-2026','OPEN');
        INSERT INTO student_charges VALUES(2,1,'2025-26',1,100,'01-06-2025','OPEN');
        """
    )
    return conn


def test_charge_partial_discount_and_reversal_are_derived_once():
    conn = make_db()
    service = LedgerService(conn)
    assert service.get_outstanding(1, 1, 2) == Decimal("100.0")
    conn.execute("INSERT INTO payments VALUES(1,1,1,'CASH',NULL,'REGULAR',2,'01-06-2026')")
    conn.execute("INSERT INTO payment_allocations VALUES(1,1,1,40,'PAYMENT')")
    assert service.get_outstanding(1, 1, 2) == Decimal("60.0")
    conn.execute("INSERT INTO charge_adjustments VALUES(1,1,'DISCOUNT',20)")
    assert service.get_outstanding(1, 1, 2) == Decimal("40.0")
    conn.execute("INSERT INTO payments VALUES(2,1,1,'CASH',NULL,'VOID',2,'01-06-2026')")
    conn.execute("INSERT INTO payment_allocations VALUES(2,2,1,40,'REVERSAL')")
    # Formula is 100 - 40 - 20 + 40 = 80; the approved discount remains valid.
    assert service.get_outstanding(1, 1, 2) == Decimal("80.0")


def test_advance_is_term_and_year_scoped_and_cross_year_does_not_leak():
    conn = make_db()
    conn.execute("INSERT INTO payments VALUES(1,1,1,'CASH',NULL,'ADVANCE',2,'01-06-2026')")
    conn.execute("INSERT INTO payment_allocations VALUES(1,1,1,30,'ADVANCE')")
    service = LedgerService(conn)
    assert service.get_outstanding(1, 1, 2) == Decimal("70.0")
    assert service.get_outstanding(1, 1, 1) == Decimal("100.0")
    conn.execute("INSERT INTO payments VALUES(2,1,1,'CASH',NULL,'REGULAR',1,'01-06-2025')")
    conn.execute("INSERT INTO payment_allocations VALUES(2,2,2,100,'PAYMENT')")
    assert service.get_outstanding(1, 1, 2) == Decimal("70.0")
    assert service.get_outstanding(1, 1, 1) == Decimal("0.0")


def test_dues_ui_aggregation_returns_one_total_per_student():
    from ui_dues import aggregate_student_dues

    rows = [
        {"student_id": 1, "student": "Asha", "student_class": "1", "student_section": "A", "scholar_no": "S1", "aadhaar": "", "phone": "999", "mobile2": "", "outstanding": 100, "due_date": "15-06-2026"},
        {"student_id": 1, "student": "Asha", "student_class": "1", "student_section": "A", "scholar_no": "S1", "aadhaar": "", "phone": "999", "mobile2": "", "outstanding": 250, "due_date": "01-06-2026"},
    ]
    result = aggregate_student_dues(rows)
    assert len(result) == 1
    assert result[0]["total_due"] == 350
    assert result[0]["oldest_due_date"] == "01-06-2026"
