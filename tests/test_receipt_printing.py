"""Post-commit receipt printing and immutable PDF history tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import receipt_printer
from migrations import migration_v004_receipt_print_tracking
from receipt_printing import commit_then_print


def financial_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,class TEXT,section TEXT);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE receipts(id INTEGER PRIMARY KEY,receipt_no TEXT UNIQUE,student_id INTEGER,total_paid REAL,receipt_type TEXT,printed_at TEXT,printed_by INTEGER,reprint_count INTEGER DEFAULT 0,last_reprint_at TEXT,last_reprint_by INTEGER);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,receipt_no TEXT,fee_head_id INTEGER,amount_due REAL,amount_paid REAL,balance REAL,payment_date TEXT,collected_by INTEGER,payment_mode TEXT,note TEXT,hash TEXT,cheque_number TEXT,upi_reference TEXT);
        CREATE TABLE cheque_tracker(id INTEGER PRIMARY KEY,payment_id INTEGER,cheque_no TEXT,bank TEXT);
        CREATE TABLE student_charges(id INTEGER PRIMARY KEY,original_amount REAL);
        CREATE TABLE payment_allocations(id INTEGER PRIMARY KEY,payment_id INTEGER,charge_id INTEGER);
        CREATE VIEW charge_ledger AS SELECT id AS charge_id,original_amount,0 AS balance FROM student_charges;
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        INSERT INTO users VALUES(1,'cashier');
        INSERT INTO students VALUES(1,'Student','1','A');
        INSERT INTO fee_heads VALUES(1,'Tuition');
        INSERT INTO settings VALUES('school_name','Test School');
        """
    )
    migration_v004_receipt_print_tracking(conn)
    return conn


def test_pdf_failure_happens_after_commit_and_does_not_duplicate_payment(tmp_path):
    db_path = tmp_path / "sfms.db"
    conn = financial_db(db_path)
    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_due,amount_paid,balance,payment_date,collected_by,payment_mode,note,hash) VALUES(1,1,'RCP-2026-000001',1,100,100,0,'01-05-2026',1,'CASH','','')")
    conn.execute("INSERT INTO receipts VALUES(1,'RCP-2026-000001',1,100,'BIG','01-05-2026 10:00:00',1,0,NULL,NULL)")

    def broken_printer(_conn, _receipt_no, _reprint):
        raise RuntimeError("printer unavailable")

    try:
        commit_then_print(conn, 1, "RCP-2026-000001", printer=broken_printer, db_path=str(db_path))
    except RuntimeError:
        pass
    else:
        raise AssertionError("simulated PDF failure was not raised")
    conn.close()

    with sqlite3.connect(db_path) as check:
        assert check.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert check.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
        failure = check.execute("SELECT receipt_id,error_message FROM receipt_print_failures").fetchone()
        assert failure == (1, "printer unavailable")


def test_original_and_two_reprints_are_distinct_and_never_overwritten(tmp_path, monkeypatch):
    db_path = tmp_path / "sfms.db"
    output = tmp_path / "receipts"
    conn = financial_db(db_path)
    conn.executescript(
        """
        INSERT INTO student_charges VALUES(1,100);
        INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_due,amount_paid,balance,payment_date,collected_by,payment_mode,note,hash) VALUES(1,1,'RCP-2026-000001',1,100,100,0,'01-05-2026',1,'CASH','','');
        INSERT INTO payment_allocations VALUES(1,1,1);
        INSERT INTO receipts VALUES(1,'RCP-2026-000001',1,100,'BIG','01-05-2026 10:00:00',1,0,NULL,NULL);
        """
    )
    conn.commit()
    monkeypatch.setattr(receipt_printer, "RECEIPTS_DIR", str(output))
    monkeypatch.setattr(receipt_printer, "_open_pdf", lambda _path: None)

    original = receipt_printer.print_receipt(conn, "RCP-2026-000001")
    first = receipt_printer.print_receipt(conn, "RCP-2026-000001", reprint=True)
    second = receipt_printer.print_receipt(conn, "RCP-2026-000001", reprint=True)

    paths = [Path(original), Path(first), Path(second)]
    assert [path.name for path in paths] == [
        "RCP-2026-000001_original.pdf",
        "RCP-2026-000001_reprint_001.pdf",
        "RCP-2026-000001_reprint_002.pdf",
    ]
    assert all(path.exists() for path in paths)
    history = conn.execute(
        "SELECT print_type,filename,file_sha256 FROM receipt_print_history ORDER BY id"
    ).fetchall()
    assert len(history) == 3
    assert len({row[1] for row in history}) == 3
    assert len({row[2] for row in history}) == 3

    # An original can never overwrite the first original file.
    try:
        receipt_printer.print_receipt(conn, "RCP-2026-000001")
    except FileExistsError:
        pass
    else:
        raise AssertionError("duplicate original should have been rejected")


def test_main_collection_receipt_data_keeps_one_payment_and_all_fee_heads(tmp_path):
    conn = financial_db(tmp_path / "main_receipt.db")
    conn.execute("INSERT INTO fee_heads VALUES(2,'Annual Fee')")
    conn.execute("INSERT INTO student_charges VALUES(1,100)")
    conn.execute("INSERT INTO student_charges VALUES(2,200)")
    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_due,amount_paid,balance,payment_date,collected_by,payment_mode,note,hash) VALUES(1,1,'MAIN-1',1,300,120,180,'01-06-2026',1,'CASH','MAIN COLLECTION','')")
    conn.execute("INSERT INTO payment_allocations VALUES(1,1,1)")
    conn.execute("INSERT INTO payment_allocations VALUES(2,1,2)")
    conn.execute("INSERT INTO receipts VALUES(1,'MAIN-1',1,120,'BIG','01-06-2026',1,0,NULL,NULL)")

    data = receipt_printer._receipt_data(conn, "MAIN-1")

    assert len(data["payments"]) == 1
    assert data["payments"][0]["amount_due"] == 300
    assert data["payments"][0]["balance"] == 180
    assert data["all_fee_heads"] == ["Tuition", "Annual Fee"]

    output = tmp_path / "main_receipts"
    original_dir = receipt_printer.RECEIPTS_DIR
    original_open = receipt_printer._open_pdf
    receipt_printer.RECEIPTS_DIR = str(output)
    receipt_printer._open_pdf = lambda _path: None
    try:
        path = Path(receipt_printer.print_receipt(conn, "MAIN-1"))
    finally:
        receipt_printer.RECEIPTS_DIR = original_dir
        receipt_printer._open_pdf = original_open
    assert path.exists()
    assert path.stat().st_size > 0


def test_receipt_uses_total_student_outstanding_and_earliest_due_date():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE charge_ledger("
        "student_id INTEGER,fee_head_id INTEGER,balance REAL,due_date TEXT,status TEXT)"
    )
    conn.executemany(
        "INSERT INTO charge_ledger VALUES(?,?,?,?,?)",
        (
            (1, 1, 125, "01-07-2026", "OPEN"),
            (1, 2, 275, "15-06-2026", "OPEN"),
            (1, 3, 500, "01-06-2026", "CANCELLED"),
            (2, 1, 900, "01-05-2026", "OPEN"),
        ),
    )

    balance, due_date = receipt_printer._outstanding_summary(
        conn, 1, [{"balance": 125}]
    )

    assert balance == 400
    assert due_date == "15-06-2026"


def test_receipt_due_lines_show_balance_then_date_then_late_fee_note():
    lines = receipt_printer._receipt_due_lines(
        {"overall_balance": 400, "overall_due_date": "15-06-2026"}
    )

    assert lines == [
        "Total Outstanding Balance: Rs. 400.00",
        "Due Date: 15-06-2026",
        receipt_printer.LATE_FEE_NOTICE,
    ]
