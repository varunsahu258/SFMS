"""Financial audit failure must roll back while operational audit stays best effort."""

import sqlite3

import pytest

from audit import log_financial_action, log_operational_event


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE payments(id INTEGER PRIMARY KEY,amount REAL);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,
            action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,
            tamper_attempt INTEGER);
        CREATE TRIGGER reject_audit BEFORE INSERT ON audit_log BEGIN
            SELECT RAISE(ABORT,'audit locked');
        END;
        """
    )
    return conn


def test_financial_audit_failure_rolls_back_payment():
    conn = make_db()
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("INSERT INTO payments(amount) VALUES(40)")
            log_financial_action(conn, "PAYMENT_COLLECTED", 1, {"record_id": 1})
    assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0


def test_operational_audit_failure_does_not_fail_login_style_event():
    conn = make_db()
    assert log_operational_event("LOGIN_SUCCESS", 1, {"record_id": 1}, conn=conn) is False
    assert conn.execute("SELECT 1").fetchone()[0] == 1
