"""Versioned setup migration and role-gate tests."""

import sqlite3
import sys
import types

sys.modules.setdefault("bcrypt", types.SimpleNamespace(checkpw=lambda *_: True))

from migrations import run_migrations
from ui_login import should_show_setup


def test_existing_accountant_does_not_open_setup_wizard():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,role TEXT,is_active INTEGER);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE receipts(id INTEGER PRIMARY KEY,receipt_no TEXT UNIQUE);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        INSERT INTO users VALUES(1,'accountant','ACCOUNTANT',1);
        """
    )
    run_migrations(conn, through="v004_receipt_print_tracking")
    assert conn.execute("SELECT value FROM settings WHERE key='setup_complete'").fetchone()[0] == "0"
    assert conn.execute("SELECT value FROM settings WHERE key='ui_theme'").fetchone()[0] == "default"
    assert should_show_setup(conn, "ACCOUNTANT", 1) is False
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='SETUP_INCOMPLETE_NON_ADMIN_LOGIN'").fetchone()[0] == 1
    assert should_show_setup(conn, "ADMIN", 2) is True
    assert [row[0] for row in conn.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id")] == [
        "v001_base_settings", "v002_setup_defaults", "v003_receipt_hmac",
        "v004_receipt_print_tracking"
    ]


def test_accountant_permission_migration_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    from migrations import migration_v010_accountant_permissions

    migration_v010_accountant_permissions(conn)
    migration_v010_accountant_permissions(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(user_permissions)")}
    assert {"user_id", "permission_key", "allowed", "updated_at", "updated_by"} <= columns


def test_receipt_issuer_setting_migration_adds_default():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
    from migrations import migration_v011_receipt_issuer_setting

    migration_v011_receipt_issuer_setting(conn)
    assert conn.execute(
        "SELECT value FROM settings WHERE key='receipt_issuer_name'"
    ).fetchone()[0] == "Sonali Sahu"


def test_admission_migration_preserves_referenced_fee_structure_rows():
    from migrations import migration_v014_admissions

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE students(id INTEGER PRIMARY KEY);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE fee_structure(id INTEGER PRIMARY KEY,fee_head_id INTEGER,
          FOREIGN KEY(fee_head_id) REFERENCES fee_heads(id));
        CREATE TABLE student_charges(id INTEGER PRIMARY KEY,fee_structure_id INTEGER,
          FOREIGN KEY(fee_structure_id) REFERENCES fee_structure(id));
        INSERT INTO fee_heads VALUES(1,'Admission Fee');
        INSERT INTO fee_structure VALUES(1,1);
        INSERT INTO student_charges VALUES(1,1);
    """)
    migration_v014_admissions(conn)
    migration_v014_admissions(conn)
    assert conn.execute("SELECT is_one_time FROM fee_heads WHERE id=1").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM fee_structure").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM student_charges").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM admissions").fetchone()[0] == 0


def test_installment_schedule_migration_is_idempotent():
    from migrations import migration_v013_late_fees, migration_v015_installment_schedules

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE students(id INTEGER PRIMARY KEY);
        CREATE TABLE student_charges(id INTEGER PRIMARY KEY);
    """)
    migration_v013_late_fees(conn)
    migration_v015_installment_schedules(conn)
    migration_v015_installment_schedules(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(installment_schedules)")}
    assert {"academic_year", "class_name", "installment_1_due", "installment_2_due", "installment_3_due"} <= columns
    late_columns = {row[1] for row in conn.execute("PRAGMA table_info(late_fee_assessments)")}
    assert {"academic_year", "installment_no", "register_type"} <= late_columns
