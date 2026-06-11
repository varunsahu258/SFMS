"""Three-installment schedule and overdue eligibility coverage."""

from decimal import Decimal
import sqlite3

from installment_service import (
    installment_amounts, overdue_installment_students, save_installment_schedule,
    validate_installment_dates,
)
from ledger import install_ledger_schema
from migrations import migration_v013_late_fees, migration_v015_installment_schedules


def installment_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE students(id INTEGER PRIMARY KEY,scholar_no TEXT,name TEXT,father_name TEXT,
          class TEXT,section TEXT,phone TEXT,is_active INTEGER);
        CREATE TABLE academic_years(id INTEGER PRIMARY KEY,label TEXT,is_active INTEGER);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT,register_type TEXT,is_active INTEGER,is_one_time INTEGER DEFAULT 0);
        CREATE TABLE fee_structure(id INTEGER PRIMARY KEY,academic_year TEXT,class TEXT,fee_head_id INTEGER,amount REAL,due_date TEXT);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,fee_head_id INTEGER,payment_mode TEXT,cheque_status TEXT);
        CREATE TABLE discounts(id INTEGER PRIMARY KEY);
        CREATE TABLE exemptions(id INTEGER PRIMARY KEY);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,
          record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        INSERT INTO users VALUES(1);
        INSERT INTO academic_years VALUES(1,'2026-27',1);
        INSERT INTO students VALUES(1,'S1','Asha','Ramesh','Class 1','A','9999999999',1);
        INSERT INTO fee_heads VALUES(1,'Tuition Fee','BIG',1,0);
        INSERT INTO fee_structure VALUES(1,'2026-27','Class 1',1,10000,'01-04-2026');
    """)
    install_ledger_schema(conn)
    migration_v013_late_fees(conn)
    migration_v015_installment_schedules(conn)
    save_installment_schedule(conn, '2026-27', 'Class 1',
                              ('30-06-2026','30-09-2026','31-12-2026'), 1)
    return conn


def test_installment_percentages_balance_to_total():
    assert installment_amounts("10001") == (Decimal("4800.48"), Decimal("2600.26"), Decimal("2600.26"))


def test_installment_dates_must_be_chronological():
    try:
        validate_installment_dates(("30-09-2026", "30-06-2026", "31-12-2026"))
    except ValueError as exc:
        assert "increasing order" in str(exc)
    else:
        raise AssertionError("Expected invalid installment order to fail")


def test_overdue_students_use_cumulative_48_26_26_targets():
    conn = installment_db()
    first = overdue_installment_students(conn, "01-07-2026")
    assert len(first) == 1
    assert first[0]["installments_due"] == 1
    assert first[0]["shortfall"] == Decimal("4800.00")
    second = overdue_installment_students(conn, "01-10-2026")
    assert second[0]["installments_due"] == 2
    assert second[0]["shortfall"] == Decimal("7400.00")


def test_late_fee_is_applied_only_once_for_same_installment():
    from ui_late_fees import apply_late_fee_assessments

    conn = installment_db()
    first = apply_late_fee_assessments(
        conn, [1], Decimal("50"), "02-07-2026", "Installment late fee", "BIG", 1, {1: 1},
    )
    second = apply_late_fee_assessments(
        conn, [1], Decimal("50"), "02-07-2026", "Installment late fee", "BIG", 1, {1: 1},
    )
    assert len(first) == 1
    assert second == []
    assert conn.execute("SELECT COUNT(*) FROM late_fee_assessments").fetchone()[0] == 1
