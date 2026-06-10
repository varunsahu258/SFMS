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
