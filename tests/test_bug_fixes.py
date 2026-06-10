from __future__ import annotations

import base64
import sys
import json
import os
import queue
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import types

if "bcrypt" not in sys.modules:
    sys.modules["bcrypt"] = types.SimpleNamespace(checkpw=lambda *_args, **_kwargs: False)
if "cryptography.exceptions" not in sys.modules:
    crypto = types.ModuleType("cryptography")
    exceptions = types.ModuleType("cryptography.exceptions")
    class InvalidTag(Exception):
        pass
    exceptions.InvalidTag = InvalidTag
    sys.modules.setdefault("cryptography", crypto)
    sys.modules["cryptography.exceptions"] = exceptions
    primitives = types.ModuleType("cryptography.hazmat.primitives")
    hashes = types.ModuleType("cryptography.hazmat.primitives.hashes")
    class SHA256: pass
    hashes.SHA256 = SHA256
    ciphers_aead = types.ModuleType("cryptography.hazmat.primitives.ciphers.aead")
    class AESGCM:
        def __init__(self, *_args): pass
        def encrypt(self, *_args): return b""
        def decrypt(self, *_args): return b""
    ciphers_aead.AESGCM = AESGCM
    kdf_pbkdf2 = types.ModuleType("cryptography.hazmat.primitives.kdf.pbkdf2")
    class PBKDF2HMAC:
        def __init__(self, *_args, **_kwargs): pass
        def derive(self, *_args): return b"0" * 32
    kdf_pbkdf2.PBKDF2HMAC = PBKDF2HMAC
    sys.modules["cryptography.hazmat"] = types.ModuleType("cryptography.hazmat")
    sys.modules["cryptography.hazmat.primitives"] = primitives
    sys.modules["cryptography.hazmat.primitives.hashes"] = hashes
    sys.modules["cryptography.hazmat.primitives.ciphers"] = types.ModuleType("cryptography.hazmat.primitives.ciphers")
    sys.modules["cryptography.hazmat.primitives.ciphers.aead"] = ciphers_aead
    sys.modules["cryptography.hazmat.primitives.kdf"] = types.ModuleType("cryptography.hazmat.primitives.kdf")
    sys.modules["cryptography.hazmat.primitives.kdf.pbkdf2"] = kdf_pbkdf2

import pytest


def _drain_events():
    from app_events import ui_event_queue

    while True:
        try:
            ui_event_queue.get_nowait()
        except queue.Empty:
            break


def test_timeout_signal_is_queued_and_tk_runs_only_when_polled(monkeypatch):
    import auth
    import main
    from app_events import SESSION_TIMEOUT, ui_event_queue

    _drain_events()
    auth.CURRENT_SESSION = auth.Session(
        token="t", user_id=1, username="admin", role="ADMIN",
        login_time=datetime.now() - timedelta(minutes=10),
        last_active=datetime.now() - timedelta(minutes=10),
    )

    class Conn:
        def execute(self, *_args, **_kwargs):
            return self
        def fetchone(self):
            return {"value": "1"}
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(auth, "_connect", lambda: Conn())
    with mock.patch("auth.messagebox.showinfo") as auth_msg:
        auth.check_timeout()
    auth_msg.assert_not_called()
    event = ui_event_queue.get_nowait()
    assert event.type == SESSION_TIMEOUT
    ui_event_queue.put(event)

    class Root:
        def after(self, *_args, **_kwargs):
            pass

    with mock.patch("main.messagebox.showinfo") as main_msg, \
         mock.patch("main._launch_login_window") as login, \
         mock.patch("main.destroy_authenticated_windows") as destroy, \
         mock.patch("auth.logout") as logout:
        main.poll_ui_events(Root())
    logout.assert_called_once()
    destroy.assert_called_once()
    main_msg.assert_called_once()
    login.assert_called_once()
    auth.CURRENT_SESSION = None


def _audit_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
    conn.execute("CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER)")
    return conn


def test_auto_backup_failure_audits_and_third_failure_signals(monkeypatch):
    from app_events import BACKUP_WARNING, ui_event_queue
    from backup import record_auto_backup_failure

    _drain_events()
    conn = _audit_db()
    monkeypatch.setattr("audit._LOGGER.exception", lambda *a, **k: None)
    assert record_auto_backup_failure(conn, RuntimeError("boom")) == 1
    assert conn.execute("SELECT action,new_value FROM audit_log WHERE action='BACKUP_FAILED'").fetchone() is not None
    record_auto_backup_failure(conn, RuntimeError("two"))
    record_auto_backup_failure(conn, RuntimeError("three"))
    assert conn.execute("SELECT value FROM settings WHERE key='consecutive_backup_failures'").fetchone()[0] == "3"
    assert ui_event_queue.get_nowait().type == BACKUP_WARNING


def test_auto_backup_success_resets_counter_and_audits():
    from backup import record_auto_backup_success

    conn = _audit_db()
    conn.execute("INSERT INTO settings(key,value) VALUES('consecutive_backup_failures','2')")
    record_auto_backup_success(conn, "/tmp/backup.db.enc")
    assert conn.execute("SELECT value FROM settings WHERE key='consecutive_backup_failures'").fetchone()[0] == "0"
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='BACKUP_SUCCESS'").fetchone()[0] == 1


def test_receipt_sequence_returns_unique_numbers_under_concurrency(tmp_path):
    from utils import get_next_receipt_no

    db = tmp_path / "seq.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE receipts(id INTEGER PRIMARY KEY, receipt_no TEXT)")
    barrier = threading.Barrier(2)
    results = []

    def worker():
        con = sqlite3.connect(db, timeout=5, isolation_level=None)
        barrier.wait()
        con.execute("BEGIN IMMEDIATE")
        try:
            value = get_next_receipt_no(con)
            con.commit()
            results.append(value)
        finally:
            con.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [1, 2]


def test_generate_icon_creates_valid_ico(tmp_path, monkeypatch):
    import scripts.generate_icon as generate_icon

    target = tmp_path / "assets" / "icon.ico"
    assert not target.exists()
    generate_icon.generate_icon(target)
    assert target.exists()
    assert target.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_reprint_flow_writes_exactly_one_receipt_reprint_audit(tmp_path, monkeypatch):
    pytest.importorskip("reportlab")
    pytest.importorskip("qrcode")
    import auth
    import receipt_printer
    from receipt_printer import print_receipt

    db = tmp_path / "reprint.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, role TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT, class TEXT, section TEXT);
        CREATE TABLE receipts(id INTEGER PRIMARY KEY, receipt_no TEXT UNIQUE, student_id INTEGER,total_paid REAL,receipt_type TEXT,printed_at TEXT,printed_by INTEGER,reprint_count INTEGER DEFAULT 0,last_reprint_at TEXT,last_reprint_by INTEGER);
        CREATE TABLE payments(id INTEGER PRIMARY KEY, receipt_no TEXT, student_id INTEGER, amount_paid REAL, payment_date TEXT, fee_head_id INTEGER, amount_due REAL, balance REAL, payment_mode TEXT, cheque_number TEXT, upi_reference TEXT, collected_by INTEGER, payment_intent TEXT, allocated_academic_year_id INTEGER, allocated_term TEXT, note TEXT);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE receipt_print_history(id INTEGER PRIMARY KEY,receipt_id INTEGER,print_type TEXT,filename TEXT UNIQUE,file_sha256 TEXT,printed_at TEXT,printed_by INTEGER);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY,timestamp TEXT,user_id INTEGER,action TEXT,table_name TEXT,record_id TEXT,old_value TEXT,new_value TEXT,tamper_attempt INTEGER);
        """
    )
    conn.execute("INSERT INTO settings VALUES('school_name','School')")
    conn.execute("INSERT INTO users VALUES(1,'admin','ADMIN')")
    conn.execute("INSERT INTO students VALUES(1,'Student','1','A')")
    conn.execute("INSERT INTO fee_heads VALUES(1,'Tuition')")
    conn.execute("INSERT INTO receipts(id,receipt_no,student_id,total_paid,receipt_type,printed_at,printed_by,reprint_count) VALUES(1,'RCP-2026-000001',1,100,'BIG','now',1,0)")
    conn.execute("INSERT INTO payments(receipt_no,student_id,amount_paid,payment_date,fee_head_id,amount_due,balance,payment_mode,collected_by,payment_intent) VALUES('RCP-2026-000001',1,100,'01-06-2026',1,100,0,'CASH',1,'REGULAR')")
    conn.commit()
    monkeypatch.setattr(receipt_printer, "RECEIPTS_DIR", str(tmp_path / "receipts"))
    monkeypatch.setattr(receipt_printer, "_open_pdf", lambda _path: None)
    auth.CURRENT_SESSION = auth.Session("t", 1, "admin", "ADMIN", datetime.now(), datetime.now())
    try:
        print_receipt(conn, "RCP-2026-000001", reprint=True, reprint_reason="parent requested")
    finally:
        auth.CURRENT_SESSION = None
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='RECEIPT_REPRINT' AND record_id='1'").fetchone()[0] == 1
    payload = json.loads(conn.execute("SELECT new_value FROM audit_log WHERE action='RECEIPT_REPRINT'").fetchone()[0])
    assert payload["reason"] == "parent requested"
    assert payload["reprint_count"] == 1


def _valid_restore_db(path: Path) -> None:
    from migrations import MIGRATIONS
    from receipt_integrity import compute_receipt_hmac

    os.environ["SFMS_INTEGRITY_KEY"] = base64.urlsafe_b64encode(b"x" * 32).decode("ascii")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, role TEXT);
        CREATE TABLE students(id INTEGER PRIMARY KEY);
        CREATE TABLE fee_heads(id INTEGER PRIMARY KEY);
        CREATE TABLE fee_structure(id INTEGER PRIMARY KEY);
        CREATE TABLE payments(id INTEGER PRIMARY KEY);
        CREATE TABLE receipts(id INTEGER PRIMARY KEY);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY);
        CREATE TABLE receipt_hashes(receipt_id INTEGER PRIMARY KEY,receipt_no TEXT UNIQUE NOT NULL,hmac_value TEXT,signed_fields_json TEXT,signed_at TEXT,algorithm TEXT NOT NULL DEFAULT 'HMAC-SHA256');
        CREATE TABLE backups_log(id INTEGER PRIMARY KEY);
        CREATE TABLE schema_migrations(migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
        """
    )
    for trigger in ["trg_payments_no_delete", "trg_payments_no_update"]:
        conn.execute(f"CREATE TRIGGER {trigger} BEFORE DELETE ON payments BEGIN SELECT 1; END")
    for trigger in ["trg_audit_no_delete", "trg_audit_no_update"]:
        conn.execute(f"CREATE TRIGGER {trigger} BEFORE DELETE ON audit_log BEGIN SELECT 1; END")
    for trigger in ["trg_receipts_no_delete", "trg_receipts_restricted_update"]:
        conn.execute(f"CREATE TRIGGER {trigger} BEFORE DELETE ON receipts BEGIN SELECT 1; END")
    for trigger in ["trg_hash_no_delete", "trg_hash_no_update"]:
        conn.execute(f"CREATE TRIGGER {trigger} BEFORE DELETE ON receipt_hashes BEGIN SELECT 1; END")
    conn.execute("INSERT INTO users(id,username,role) VALUES(1,'admin','ADMIN')")
    for migration_id, _ in MIGRATIONS:
        conn.execute("INSERT INTO schema_migrations VALUES(?, 'now')", (migration_id,))
    fields = {"receipt_id": 1, "receipt_no": "R1", "payments": []}
    signed = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    conn.execute("INSERT INTO receipt_hashes(receipt_id,receipt_no,hmac_value,signed_fields_json,algorithm) VALUES(1,'R1',?,?, 'HMAC-SHA256')", (compute_receipt_hmac(fields), signed))
    conn.commit()
    conn.close()


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda conn: conn.execute("DROP TABLE students"), "Missing required table"),
        (lambda conn: conn.execute("DROP TRIGGER trg_hash_no_update"), "Missing required trigger"),
        (lambda conn: conn.execute("DELETE FROM users"), "no ADMIN"),
        (lambda conn: conn.execute("INSERT INTO settings VALUES('backup_key','$2b$bad')"), "Suspicious bcrypt"),
        (lambda conn: conn.execute("UPDATE receipt_hashes SET hmac_value='bad'"), "HMAC mismatch"),
        (lambda conn: conn.execute("INSERT INTO schema_migrations VALUES('v999_future','now')"), "newer than this application"),
    ],
)
def test_restore_validation_reports_individual_failures(tmp_path, mutate, expected):
    from backup import validate_backup_for_restore

    db = tmp_path / "backup.db"
    _valid_restore_db(db)
    conn = sqlite3.connect(db)
    mutate(conn)
    conn.commit()
    conn.close()
    ok, reasons = validate_backup_for_restore(str(db))
    assert not ok
    assert any(expected in reason for reason in reasons)


def test_restore_validation_rejects_corrupt_sqlite_file(tmp_path):
    from backup import validate_backup_for_restore

    db = tmp_path / "corrupt.db"
    db.write_bytes(b"not sqlite")
    ok, reasons = validate_backup_for_restore(str(db))
    assert not ok
    assert reasons
