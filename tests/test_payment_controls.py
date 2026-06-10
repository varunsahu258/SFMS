"""Regression tests for payment references, cheque recognition, and cashier shifts."""

import sqlite3

import pytest

from ledger import allocate_payment, charge_outstanding, install_ledger_schema
from payment_controls import (
    close_shift,
    migrate_payment_controls,
    normalize_reference,
    open_shift,
    payment_revenue_amount,
    set_cheque_status,
    uncleared_cheque_amount,
)
from utils import now_str, today_str


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,class TEXT,is_active INTEGER);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT,register_type TEXT);
        CREATE TABLE fee_structure(id INTEGER PRIMARY KEY,academic_year TEXT,class TEXT,fee_head_id INTEGER,amount REAL,due_date TEXT);
        CREATE TABLE academic_years(id INTEGER PRIMARY KEY,label TEXT,start_date TEXT,end_date TEXT,is_active INTEGER);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,receipt_no TEXT,fee_head_id INTEGER,amount_due REAL,amount_paid REAL,balance REAL,payment_date TEXT,collected_by INTEGER,payment_mode TEXT,note TEXT,hash TEXT);
        CREATE TABLE receipts(id INTEGER PRIMARY KEY,receipt_no TEXT UNIQUE,student_id INTEGER,total_paid REAL,receipt_type TEXT,printed_at TEXT,printed_by INTEGER,reprint_count INTEGER,last_reprint_at TEXT,last_reprint_by INTEGER);
        CREATE TABLE cheque_tracker(id INTEGER PRIMARY KEY,payment_id INTEGER,cheque_no TEXT,bank TEXT,amount REAL,collected_on TEXT,status TEXT,updated_at TEXT);
        CREATE TABLE discounts(id INTEGER PRIMARY KEY,student_id INTEGER,fee_head_id INTEGER,amount REAL,reason TEXT,approved_by INTEGER,created_at TEXT);
        CREATE TABLE exemptions(id INTEGER PRIMARY KEY,student_id INTEGER,academic_year TEXT,fee_head_ids TEXT,reason TEXT,approved_by INTEGER,created_at TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        INSERT INTO users VALUES(1,'cashier'); INSERT INTO users VALUES(2,'admin');
        INSERT INTO students VALUES(1,'Student','1',1); INSERT INTO fee_heads VALUES(1,'Tuition','BIG');
        INSERT INTO academic_years VALUES(1,'2026-27','01-04-2026','31-03-2027',1);
        INSERT INTO fee_structure VALUES(1,'2026-27','1',1,100,'01-04-2026');
        """
    )
    migrate_payment_controls(conn)
    install_ledger_schema(conn)
    conn.execute("INSERT INTO student_charges(student_id,academic_year,fee_structure_id,fee_head_id,original_amount,due_date,status,created_at) VALUES(1,'2026-27',1,1,100,'01-04-2026','OPEN',?)", (now_str(),))
    return conn


def test_normalized_references_are_unique():
    conn = make_conn()
    assert normalize_reference(" ab 12 ") == "AB12"
    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_paid,payment_date,collected_by,payment_mode,note,hash,cheque_number,cheque_status) VALUES(1,1,'A',1,10,?,1,'CHEQUE','','h','AB12','PENDING')", (today_str(),))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_paid,payment_date,collected_by,payment_mode,note,hash,cheque_number,cheque_status) VALUES(2,1,'B',1,10,?,1,'CHEQUE','','h','AB12','PENDING')", (today_str(),))
    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_paid,payment_date,collected_by,payment_mode,note,hash,upi_reference) VALUES(3,1,'C',1,10,?,1,'UPI','','h','UPI77')", (today_str(),))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_paid,payment_date,collected_by,payment_mode,note,hash,upi_reference) VALUES(4,1,'D',1,10,?,1,'UPI','','h','UPI77')", (today_str(),))


def test_pending_cheque_is_unrecognized_until_cleared():
    conn = make_conn()
    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_paid,payment_date,collected_by,payment_mode,note,hash,cheque_number,cheque_status) VALUES(1,1,'CHQ',1,40,?,1,'CHEQUE','','h','CHQ1','PENDING')", (today_str(),))
    conn.execute("INSERT INTO cheque_tracker(payment_id,cheque_no,bank,amount,collected_on,status,updated_at) VALUES(1,'CHQ1','Bank',40,?,'PENDING',?)", (today_str(),now_str()))
    allocate_payment(conn,1,1,40)
    assert charge_outstanding(conn,1) == 100
    assert payment_revenue_amount('CHEQUE','PENDING',40) == 0
    assert uncleared_cheque_amount('CHEQUE','PENDING',40) == 40
    set_cheque_status(conn,1,'CLEARED',2,today_str(),' bank ref 9 ')
    assert charge_outstanding(conn,1) == 60
    assert payment_revenue_amount('CHEQUE','CLEARED',40) == 40



def test_bounced_cheque_never_reduces_dues_or_revenue():
    conn = make_conn()
    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_paid,payment_date,collected_by,payment_mode,note,hash,cheque_number,cheque_status) VALUES(1,1,'BOUNCE',1,40,?,1,'CHEQUE','','h','CHQ2','PENDING')", (today_str(),))
    conn.execute("INSERT INTO cheque_tracker(payment_id,cheque_no,status) VALUES(1,'CHQ2','PENDING')")
    allocate_payment(conn,1,1,40)
    set_cheque_status(conn,1,'BOUNCED',2)
    assert charge_outstanding(conn,1) == 100
    assert payment_revenue_amount('CHEQUE','BOUNCED',40) == 0


def test_cashier_shift_close_calculates_cash_variance():
    conn = make_conn()
    shift_id = open_shift(conn,1)
    stamp = now_str()
    conn.execute("INSERT INTO receipts(id,receipt_no,student_id,total_paid,receipt_type,printed_at,printed_by,reprint_count) VALUES(1,'CASH1',1,80,'BIG',?,1,0)", (stamp,))
    conn.execute("INSERT INTO payments(id,student_id,receipt_no,fee_head_id,amount_paid,payment_date,collected_by,payment_mode,note,hash) VALUES(1,1,'CASH1',1,80,?,1,'CASH','','h')", (today_str(),))
    result = close_shift(conn,shift_id,75,2,2)
    assert result['system_cash_total'] == 80
    assert result['variance'] == -5


def test_legacy_migration_preserves_rows_and_flags_duplicate_references():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,receipt_no TEXT,fee_head_id INTEGER,amount_due REAL,amount_paid REAL,balance REAL,payment_date TEXT,collected_by INTEGER,payment_mode TEXT,note TEXT,hash TEXT);
        CREATE TABLE cheque_tracker(id INTEGER PRIMARY KEY,payment_id INTEGER,cheque_no TEXT,bank TEXT,amount REAL,collected_on TEXT,status TEXT,updated_at TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        INSERT INTO payments VALUES(1,1,'A',1,10,10,0,'01-05-2026',1,'CHEQUE','','h');
        INSERT INTO payments VALUES(2,1,'B',1,10,10,0,'01-05-2026',1,'CHEQUE','','h');
        INSERT INTO cheque_tracker VALUES(1,1,' ab 12 ','Bank',10,'01-05-2026','PENDING','');
        INSERT INTO cheque_tracker VALUES(2,2,'AB12','Bank',10,'01-05-2026','PENDING','');
        """
    )
    migrate_payment_controls(conn)
    assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 2
    assert conn.execute("SELECT cheque_number FROM payments WHERE id=1").fetchone()[0] == 'AB12'
    assert conn.execute("SELECT cheque_number FROM payments WHERE id=2").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='TAMPER_PAYMENT_REFERENCE_DUPLICATE'").fetchone()[0] == 1
    migrate_payment_controls(conn)  # idempotent; must not repopulate the rejected duplicate.
    assert conn.execute("SELECT cheque_number FROM payments WHERE id=2").fetchone()[0] is None
