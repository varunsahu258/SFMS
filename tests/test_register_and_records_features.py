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


def _financial_register_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE academic_years(id INTEGER PRIMARY KEY,label TEXT,is_active INTEGER);
        CREATE TABLE students(id INTEGER PRIMARY KEY,scholar_no TEXT,name TEXT,father_name TEXT,
          class TEXT,section TEXT,phone TEXT,is_active INTEGER,status TEXT,created_at TEXT);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT,register_type TEXT,is_active INTEGER,is_one_time INTEGER DEFAULT 0);
        CREATE TABLE fee_structure(id INTEGER PRIMARY KEY,academic_year TEXT,class TEXT,fee_head_id INTEGER,amount REAL,due_date TEXT);
        CREATE TABLE student_charges(id INTEGER PRIMARY KEY,student_id INTEGER,academic_year TEXT,
          fee_structure_id INTEGER,fee_head_id INTEGER,original_amount REAL,due_date TEXT,status TEXT,created_at TEXT,
          UNIQUE(student_id,academic_year,fee_structure_id));
        CREATE TABLE charge_adjustments(id INTEGER PRIMARY KEY,charge_id INTEGER,adjustment_type TEXT,amount REAL,
          reason TEXT,created_at TEXT);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,receipt_no TEXT,payment_date TEXT,
          payment_mode TEXT,note TEXT,cheque_status TEXT,allocated_academic_year_id INTEGER,allocated_term TEXT);
        CREATE TABLE payment_allocations(id INTEGER PRIMARY KEY,payment_id INTEGER,charge_id INTEGER,
          amount_allocated REAL,allocation_type TEXT);
        INSERT INTO academic_years VALUES(1,'2026-27',1);
        INSERT INTO students VALUES(1,'S-1','Asha','Ramesh','Class 1','A','9999999999',1,'ACTIVE','01-04-2026 09:00:00');
        INSERT INTO fee_heads VALUES(1,'Tuition Fee','BIG',1,0);
        INSERT INTO student_charges VALUES(1,1,'2026-27',NULL,1,1000,'10-04-2026','OPEN','01-04-2026 09:00:00');
        INSERT INTO charge_adjustments VALUES(1,1,'DISCOUNT',100,'Sibling discount','05-04-2026 10:00:00');
        INSERT INTO payments VALUES(1,1,'R-001','06-04-2026','UPI','April payment','',NULL,NULL);
        INSERT INTO payment_allocations VALUES(1,1,1,400,'PAYMENT');
    """)
    return conn


def test_student_dues_register_is_chronological_and_includes_receipts_discounts_and_totals():
    from ui_dues_register import student_dues_register

    register = student_dues_register(_financial_register_db(), 1)
    assert register["student"]["father_name"] == "Ramesh"
    assert [event["type"] for event in register["events"]] == ["CHARGE", "DISCOUNT", "PAYMENT"]
    assert register["events"][-1]["reference"] == "R-001"
    assert register["totals"]["charged"] == 1000
    assert register["totals"]["paid"] == 400
    assert register["totals"]["adjustments"] == 100
    assert register["totals"]["outstanding"] == Decimal("500")


def test_new_admission_fee_is_a_one_time_student_charge(monkeypatch):
    import auth
    from migrations import migration_v014_admissions
    from ui_admissions import create_admission

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE academic_years(id INTEGER PRIMARY KEY,label TEXT,is_active INTEGER);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,father_name TEXT,guardian_name TEXT,
          class TEXT,is_active INTEGER,status TEXT,created_at TEXT);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT,register_type TEXT,is_active INTEGER);
        CREATE TABLE fee_structure(id INTEGER PRIMARY KEY,academic_year TEXT,class TEXT,fee_head_id INTEGER,amount REAL,due_date TEXT);
        CREATE TABLE student_charges(id INTEGER PRIMARY KEY,student_id INTEGER,academic_year TEXT,
          fee_structure_id INTEGER,fee_head_id INTEGER,original_amount REAL,due_date TEXT,status TEXT,created_at TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,
          record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        INSERT INTO users VALUES(1);
        INSERT INTO academic_years VALUES(1,'2026-27',1);
        INSERT INTO students VALUES(1,'Existing Student','Parent','Parent','Class 1',1,'ACTIVE','01-04-2026');
    """)
    migration_v014_admissions(conn)
    monkeypatch.setattr(auth, "CURRENT_SESSION", None)
    student_id, charge_id = create_admission(
        conn, {"name": "New Student", "father_name": "New Parent", "class": "Class 1"},
        Decimal("750"), "11-06-2026", "BIG", 1,
    )
    assert student_id == 2 and charge_id is not None
    assert conn.execute("SELECT COUNT(*) FROM student_charges WHERE student_id=1").fetchone()[0] == 0
    charge = conn.execute("SELECT student_id,original_amount,fee_structure_id FROM student_charges").fetchone()
    assert tuple(charge) == (2, 750.0, None)
    assert conn.execute("SELECT COUNT(*) FROM fee_structure").fetchone()[0] == 0
    assert conn.execute("SELECT is_one_time FROM fee_heads").fetchone()[0] == 1


def test_student_view_tree_uses_supported_treeview_options():
    from ui_student_view import StudentViewWindow

    source = inspect.getsource(StudentViewWindow._build_widgets)
    assert 'ttk.Treeview(body, columns=columns, show="headings", width=' not in source
