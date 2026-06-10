"""Regression tests for protected financial and operational records."""

import sqlite3

import pytest

from migrations import migration_v005_immutability_controls


def make_connection():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,
            role TEXT, is_active INTEGER, failed_attempts INTEGER, locked_at TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY);
        CREATE TABLE receipts(id INTEGER PRIMARY KEY, receipt_no TEXT, student_id INTEGER,
            total_paid REAL, receipt_type TEXT, printed_at TEXT, printed_by INTEGER,
            reprint_count INTEGER DEFAULT 0, last_reprint_at TEXT, last_reprint_by INTEGER);
        CREATE TABLE discounts(id INTEGER PRIMARY KEY, student_id INTEGER, fee_head_id INTEGER,
            amount REAL, reason TEXT, approved_by INTEGER, created_at TEXT);
        CREATE TABLE exemptions(id INTEGER PRIMARY KEY, student_id INTEGER, academic_year TEXT,
            fee_head_ids TEXT, reason TEXT, approved_by INTEGER, created_at TEXT);
        CREATE TABLE cheque_tracker(id INTEGER PRIMARY KEY, payment_id INTEGER, cheque_no TEXT,
            bank TEXT, amount REAL, collected_on TEXT, status TEXT, updated_at TEXT);
        CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE backups_log(id INTEGER PRIMARY KEY, filename TEXT, created_at TEXT,
            created_by TEXT, type TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY, timestamp TEXT, user_id INTEGER,
            action TEXT, table_name TEXT, record_id TEXT, old_value TEXT, new_value TEXT,
            tamper_attempt INTEGER DEFAULT 0);
        INSERT INTO users VALUES(1,'admin','old','ADMIN',1,0,NULL);
        INSERT INTO receipts VALUES(1,'RCP-2026-000001',1,100,'BIG','now',1,0,NULL,NULL);
        INSERT INTO discounts VALUES(1,1,1,10,'approved',1,'now');
        INSERT INTO exemptions VALUES(1,1,'2026-27','1','approved',1,'now');
        INSERT INTO cheque_tracker VALUES(1,1,'ABC1','BANK',100,'today','PENDING','now');
        INSERT INTO settings VALUES('school_name','Old School');
        INSERT INTO backups_log VALUES(1,'backup.db','now','1','MANUAL');
        """
    )
    migration_v005_immutability_controls(conn)
    return conn


def test_receipt_financial_fields_are_immutable_but_reprint_metadata_is_mutable():
    conn = make_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE receipts SET total_paid=1 WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE receipts SET student_id=2 WHERE id=1")
    conn.execute("UPDATE receipts SET reprint_count=reprint_count+1 WHERE id=1")
    assert conn.execute("SELECT reprint_count FROM receipts WHERE id=1").fetchone()[0] == 1


@pytest.mark.parametrize("table", ["discounts", "exemptions"])
def test_discount_and_exemption_rows_cannot_be_updated_or_deleted(table):
    conn = make_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE {table} SET reason='changed' WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"DELETE FROM {table} WHERE id=1")


def test_cheque_only_allows_lifecycle_metadata_changes():
    conn = make_connection()
    conn.execute("UPDATE cheque_tracker SET status='CLEARED', updated_at='later' WHERE id=1")
    assert conn.execute("SELECT status FROM cheque_tracker WHERE id=1").fetchone()[0] == "CLEARED"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE cheque_tracker SET amount=1 WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM cheque_tracker WHERE id=1")


def test_user_identity_role_status_and_deletion_are_blocked_but_security_metadata_is_allowed():
    conn = make_connection()
    conn.execute("UPDATE users SET password_hash='new', last_login='later' WHERE id=1")
    assert conn.execute("SELECT password_hash,last_login FROM users WHERE id=1").fetchone() == ("new", "later")
    for statement in (
        "UPDATE users SET username='other' WHERE id=1",
        "UPDATE users SET role='ACCOUNTANT' WHERE id=1",
        "UPDATE users SET is_active=0 WHERE id=1",
        "DELETE FROM users WHERE id=1",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement)


def test_settings_updates_are_audited_but_deletes_are_blocked():
    conn = make_connection()
    conn.execute("UPDATE settings SET value='New School' WHERE key='school_name'")
    assert conn.execute("SELECT value FROM settings WHERE key='school_name'").fetchone()[0] == "New School"
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='SETTING_UPDATED'").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM settings WHERE key='school_name'")


def test_backup_history_is_fully_immutable():
    conn = make_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE backups_log SET filename='other.db' WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM backups_log WHERE id=1")
