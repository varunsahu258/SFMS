"""Coverage for register separation, receipt totals, and new read-only modules."""

from __future__ import annotations

import inspect
import sqlite3
from decimal import Decimal


def test_small_collection_reuses_exact_main_collection_workflow():
    from ui_collection_main import CollectionMainWindow
    from ui_collection_small import CollectionSmallWindow

    assert issubclass(CollectionSmallWindow, CollectionMainWindow)
    assert CollectionSmallWindow.register_types == ("SMALL", "BOTH")
    assert CollectionSmallWindow.receipt_type == "SMALL"
    assert CollectionSmallWindow.collection_note == "SMALL COLLECTION"


def test_collection_report_can_preserve_register_labels():
    from report_generator import collection_report_rows
    from tests.test_collection_reports import report_db

    conn = report_db()
    rows = collection_report_rows(conn, "01-06-2026", "03-06-2026", ("CASH", "UPI"), include_register=True)
    assert {row["register_type"] for row in rows} == {"BIG"}


def test_receipt_uses_whole_student_balance_not_only_paid_heads():
    import receipt_printer

    source = inspect.getsource(receipt_printer._receipt_data)
    draw_source = inspect.getsource(receipt_printer._draw_copy)
    assert 'SUM(balance)' in source
    assert 'overall_due_before_payment' in source
    assert 'data.get("overall_balance")' in draw_source


def test_late_fee_migration_and_assessment_create_separate_charge(monkeypatch):
    import auth
    from migrations import migration_v013_late_fees
    from ui_late_fees import apply_late_fee_assessments

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE academic_years(label TEXT PRIMARY KEY,is_active INTEGER);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT UNIQUE,register_type TEXT,is_active INTEGER);
        CREATE TABLE student_charges(id INTEGER PRIMARY KEY,student_id INTEGER,academic_year TEXT,
          fee_structure_id INTEGER,fee_head_id INTEGER,original_amount REAL,due_date TEXT,status TEXT,created_at TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,
          record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        INSERT INTO users VALUES(1); INSERT INTO students VALUES(1,'Asha');
        INSERT INTO academic_years VALUES('2026-27',1);
    """)
    migration_v013_late_fees(conn)
    monkeypatch.setattr(auth, "CURRENT_SESSION", None)
    ids = apply_late_fee_assessments(conn, [1], Decimal("25"), "11-06-2026", "Late payment", "BIG", 1)
    assert len(ids) == 1
    row = conn.execute("SELECT amount,reason FROM late_fee_assessments").fetchone()
    assert tuple(row) == (25.0, "Late payment")
    assert conn.execute("SELECT original_amount FROM student_charges").fetchone()[0] == 25.0


def test_read_only_receipt_and_student_modules_have_no_edit_actions():
    from ui_receipt_history import ReceiptHistoryWindow
    from ui_student_view import StudentViewWindow

    assert "print_committed_receipt" not in inspect.getsource(ReceiptHistoryWindow)
    assert "UPDATE students" not in inspect.getsource(StudentViewWindow)
