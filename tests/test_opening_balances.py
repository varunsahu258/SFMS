"""Opening-balance migration and academic-year dues coverage."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from ledger import install_ledger_schema
from opening_balance_service import record_opening_balance, student_balance_summary
from ui_dues import aggregate_student_dues


def opening_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,class TEXT,section TEXT);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT,register_type TEXT,is_active INTEGER,is_one_time INTEGER DEFAULT 0);
        CREATE TABLE fee_structure(id INTEGER PRIMARY KEY,academic_year TEXT,class TEXT,fee_head_id INTEGER,amount REAL,due_date TEXT);
        CREATE TABLE academic_years(id INTEGER PRIMARY KEY,label TEXT UNIQUE,start_date TEXT,end_date TEXT,is_active INTEGER);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,fee_head_id INTEGER,payment_mode TEXT,cheque_status TEXT);
        CREATE TABLE discounts(id INTEGER PRIMARY KEY,student_id INTEGER,fee_head_id INTEGER,amount REAL,reason TEXT,approved_by INTEGER,created_at TEXT);
        CREATE TABLE exemptions(id INTEGER PRIMARY KEY,student_id INTEGER,academic_year TEXT,fee_head_ids TEXT,reason TEXT,approved_by INTEGER,created_at TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER DEFAULT 0);
        CREATE TABLE opening_balances(
            id INTEGER PRIMARY KEY,student_id INTEGER,academic_year TEXT,amount REAL,due_date TEXT,note TEXT,
            charge_id INTEGER UNIQUE,created_at TEXT,created_by INTEGER,UNIQUE(student_id,academic_year));
        INSERT INTO users VALUES(1,'admin');
        INSERT INTO students VALUES(1,'Asha','1','A');
        INSERT INTO academic_years VALUES(1,'2025-26','01-04-2025','31-03-2026',0);
        INSERT INTO academic_years VALUES(2,'2026-27','01-04-2026','31-03-2027',1);
        """
    )
    install_ledger_schema(conn)
    return conn


def test_opening_balance_becomes_prior_year_ledger_charge_and_is_audited():
    conn = opening_db()

    opening_id = record_opening_balance(
        conn, 1, "2025-26", Decimal("750"), "31-03-2026", "Manual register carry forward", 1
    )

    row = conn.execute(
        """SELECT o.academic_year,o.amount,o.due_date,c.original_amount,c.academic_year,fh.is_active
           FROM opening_balances o JOIN student_charges c ON c.id=o.charge_id
           JOIN fee_heads fh ON fh.id=c.fee_head_id WHERE o.id=?""", (opening_id,),
    ).fetchone()
    assert tuple(row) == ("2025-26", 750, "31-03-2026", 750, "2025-26", 1)
    assert conn.execute("SELECT action FROM audit_log").fetchone()[0] == "OPENING_BALANCE_CREATE"
    assert student_balance_summary(conn, 1) == {
        "current_year": "2026-27", "current_due": Decimal("0"),
        "previous_due": Decimal("750.0"), "total_due": Decimal("750.0"),
    }


def test_duplicate_or_current_year_opening_balance_is_rejected():
    conn = opening_db()
    record_opening_balance(conn, 1, "2025-26", Decimal("100"), "31-03-2026", "", 1)
    for year in ("2025-26", "2026-27"):
        try:
            record_opening_balance(conn, 1, year, Decimal("50"), "31-03-2026", "", 1)
        except ValueError:
            pass
        else:
            raise AssertionError(f"opening balance for {year} should be rejected")


def test_dues_aggregation_splits_current_previous_and_total_by_academic_year():
    rows = [
        {"student_id": 1, "student": "Asha", "student_class": "1", "academic_year": "2025-26", "outstanding": 300, "due_date": "01-03-2026"},
        {"student_id": 1, "student": "Asha", "student_class": "1", "academic_year": "2026-27", "outstanding": 500, "due_date": "01-07-2026"},
    ]

    result = aggregate_student_dues(rows, "2026-27")[0]

    assert result["previous_year_due"] == 300
    assert result["current_year_due"] == 500
    assert result["total_due"] == 800
    assert result["academic_year_totals"] == {"2025-26": 300, "2026-27": 500}


def test_dues_export_groups_rows_by_academic_year_and_register_keeps_year_column():
    import inspect
    import report_generator
    import ui_dues_register

    report_source = inspect.getsource(report_generator.classwise_dues_report)
    assert 'LedgerService(conn).get_all_outstanding()' in report_source
    assert '("academic_year", "Academic Year"' in report_source
    assert 'groups.setdefault((int(row["student_id"]), year)' in report_source

    register_source = inspect.getsource(ui_dues_register.student_dues_register)
    assert 'c.academic_year' in register_source
    assert '"academic_year": row["academic_year"]' in register_source
