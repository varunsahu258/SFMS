"""Receipt HMAC and integrity-coverage regression tests."""

import base64
import os
import sqlite3

import pytest

from integrity import verify_all_hashes, verify_single_receipt
from receipt_integrity import install_receipt_hmac_schema, sign_receipt


KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


def make_conn():
    os.environ["SFMS_INTEGRITY_KEY"] = KEY
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE receipts(id INTEGER PRIMARY KEY,receipt_no TEXT UNIQUE,student_id INTEGER,total_paid REAL,receipt_type TEXT,printed_at TEXT,printed_by INTEGER,reprint_count INTEGER,last_reprint_at TEXT,last_reprint_by INTEGER);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,student_id INTEGER,receipt_no TEXT,fee_head_id INTEGER,amount_due REAL,amount_paid REAL,balance REAL,payment_date TEXT,collected_by INTEGER,payment_mode TEXT,note TEXT,hash TEXT,cheque_number TEXT,upi_reference TEXT,cheque_status TEXT,cheque_cleared_date TEXT,cheque_bank_reference TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        INSERT INTO users VALUES(1,'cashier'); INSERT INTO students VALUES(1,'Student'); INSERT INTO fee_heads VALUES(1,'Tuition');
        """
    )
    install_receipt_hmac_schema(conn)
    return conn


def add_receipt(conn, receipt_id, number, total=100, with_payment=True, sign=True):
    conn.execute("INSERT INTO receipts VALUES(?,?,1,?,'BIG','01-05-2026 10:00:00',1,0,NULL,NULL)", (receipt_id, number, total))
    if with_payment:
        conn.execute(
            "INSERT INTO payments VALUES(?,1,?,1,100,100,0,'01-05-2026',1,'UPI','', '',NULL,? ,NULL,NULL,NULL)",
            (receipt_id, number, f"UPI{receipt_id}"),
        )
    if sign:
        sign_receipt(conn, receipt_id)


def test_valid_receipt_passes():
    conn = make_conn()
    add_receipt(conn, 1, "RCP-2026-000001")
    assert verify_single_receipt(1, conn) == (True, "ok")


def test_tampered_receipt_total_fails():
    conn = make_conn()
    add_receipt(conn, 1, "RCP-2026-000001")
    conn.execute("UPDATE receipts SET total_paid=900 WHERE id=1")
    ok, reason = verify_single_receipt(1, conn)
    assert not ok
    assert "signed field snapshot" in reason


def test_orphan_payment_is_flagged():
    conn = make_conn()
    conn.execute("INSERT INTO payments VALUES(1,1,'RCP-2026-000099',1,100,100,0,'01-05-2026',1,'CASH','', '',NULL,NULL,NULL,NULL,NULL)")
    result = verify_all_hashes(conn)
    assert result["orphan_payments"] == ["RCP-2026-000099"]


def test_sequence_gap_is_flagged():
    conn = make_conn()
    add_receipt(conn, 1, "RCP-2026-000001")
    add_receipt(conn, 3, "RCP-2026-000003")
    result = verify_all_hashes(conn)
    assert result["sequence_gaps"] == ["RCP-2026-000002"]


def test_key_helper_writes_outside_database_tree(tmp_path, monkeypatch):
    import receipt_integrity

    db_dir = tmp_path / "database"
    db_dir.mkdir()
    monkeypatch.setattr(receipt_integrity, "DB_PATH", str(db_dir / "sfms.db"))
    with pytest.raises(ValueError):
        receipt_integrity.generate_integrity_key_file(str(db_dir / "keys" / "integrity.key"))
    destination = tmp_path / "secrets" / "integrity.key"
    path = receipt_integrity.generate_integrity_key_file(str(destination))
    assert path == str(destination.resolve())
    assert len(base64.urlsafe_b64decode(destination.read_text().strip())) == 32
    with pytest.raises(FileExistsError):
        receipt_integrity.generate_integrity_key_file(str(destination))


def test_integrity_key_is_created_and_reused_without_environment(tmp_path, monkeypatch):
    import receipt_integrity

    key_path = tmp_path / "config" / "integrity.key"
    monkeypatch.delenv(receipt_integrity.ENV_KEY, raising=False)
    monkeypatch.setattr(receipt_integrity, "INTEGRITY_KEY_PATH", key_path)

    first = receipt_integrity.integrity_key()
    second = receipt_integrity.integrity_key()

    assert first == second
    assert len(first) == 32
    assert key_path.is_file()


def test_integrity_environment_override_does_not_create_key_file(tmp_path, monkeypatch):
    import receipt_integrity

    key_path = tmp_path / "config" / "integrity.key"
    expected = b"e" * 32
    monkeypatch.setenv(receipt_integrity.ENV_KEY, base64.urlsafe_b64encode(expected).decode("ascii"))
    monkeypatch.setattr(receipt_integrity, "INTEGRITY_KEY_PATH", key_path)

    assert receipt_integrity.integrity_key() == expected
    assert not key_path.exists()
