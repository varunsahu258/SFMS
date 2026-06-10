"""Database schema creation and first-run seeding for SFMS."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime

import bcrypt

from config import (
    ACADEMIC_YEAR_START_MONTH,
    ACTION_DISCOUNT_CREATED,
    ACTION_EXEMPTION_CREATED,
    BACKUP_INTERVAL_DEFAULT,
    CHEQUE_STATUS_PENDING,
    DB_PATH,
    DEFAULT_ADMIN_ACTIVE,
    DEFAULT_ADMIN_ROLE,
    DEFAULT_ADMIN_USERNAME,
    LOGO_PATH,
    REGISTER_BIG,
    REGISTER_BOTH,
    REGISTER_SMALL,
    ROLE_ACCOUNTANT,
    ROLE_ADMIN,
    SCHOOL_ADDRESS,
    SCHOOL_NAME,
    SESSION_TIMEOUT_DEFAULT,
    SETTING_BACKUP_INTERVAL_HOURS,
    SETTING_LOGO_PATH,
    SETTING_SCHOOL_ADDRESS,
    SETTING_SCHOOL_NAME,
    SETTING_SESSION_TIMEOUT_MINUTES,
    STATUS_ACTIVE,
    TRG_AUDIT_DELETE_MSG,
    TRG_AUDIT_UPDATE_MSG,
    TRG_HASH_DELETE_MSG,
    TRG_HASH_UPDATE_MSG,
    TRG_PAYMENTS_DELETE_MSG,
    TRG_PAYMENTS_UPDATE_MSG,
    TRG_RECEIPTS_DELETE_MSG,
)
from security_utils import validate_bootstrap_password
from utils import ensure_receipt_sequence, now_str


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply required SQLite pragmas for every database connection."""
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")


def _create_tables(conn: sqlite3.Connection) -> None:
    """Create all required SFMS tables if they do not already exist."""
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password_hash TEXT,
            role TEXT CHECK(role IN ('{ROLE_ADMIN}','{ROLE_ACCOUNTANT}')),
            is_active INTEGER DEFAULT 1,
            failed_attempts INTEGER DEFAULT 0,
            locked_at TEXT,
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            class TEXT,
            section TEXT,
            aadhaar TEXT UNIQUE,
            phone TEXT,
            guardian_name TEXT,
            is_active INTEGER DEFAULT 1,
            status TEXT DEFAULT '{STATUS_ACTIVE}',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS fee_heads (
            id INTEGER PRIMARY KEY,
            name TEXT,
            register_type TEXT CHECK(register_type IN ('{REGISTER_BIG}','{REGISTER_SMALL}','{REGISTER_BOTH}')),
            is_active INT DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS fee_structure (
            id INTEGER PRIMARY KEY,
            academic_year TEXT,
            class TEXT,
            fee_head_id INTEGER,
            amount REAL,
            due_date TEXT,
            FOREIGN KEY (fee_head_id) REFERENCES fee_heads(id)
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY,
            student_id INTEGER,
            receipt_no TEXT,
            fee_head_id INTEGER,
            amount_due REAL,
            amount_paid REAL,
            balance REAL,
            payment_date TEXT,
            collected_by INTEGER,
            payment_mode TEXT,
            note TEXT,
            hash TEXT,
            cheque_number TEXT,
            upi_reference TEXT,
            cheque_status TEXT CHECK(cheque_status IN ('PENDING','CLEARED','BOUNCED','CANCELLED') OR cheque_status IS NULL),
            cheque_cleared_date TEXT,
            cheque_bank_reference TEXT,
            payment_intent TEXT NOT NULL DEFAULT 'REGULAR' CHECK(payment_intent IN ('REGULAR','ADVANCE','VOID')),
            allocated_academic_year_id INTEGER,
            allocated_term TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (fee_head_id) REFERENCES fee_heads(id),
            FOREIGN KEY (collected_by) REFERENCES users(id),
            FOREIGN KEY (allocated_academic_year_id) REFERENCES academic_years(id)
        );

        CREATE TABLE IF NOT EXISTS receipt_sequence (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            last_receipt_no INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY,
            receipt_no TEXT UNIQUE,
            student_id INTEGER,
            total_paid REAL,
            receipt_type TEXT,
            printed_at TEXT,
            printed_by INTEGER,
            reprint_count INTEGER DEFAULT 0,
            last_reprint_at TEXT,
            last_reprint_by INTEGER,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (printed_by) REFERENCES users(id),
            FOREIGN KEY (last_reprint_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS discounts (
            id INTEGER PRIMARY KEY,
            student_id INTEGER,
            fee_head_id INTEGER,
            amount REAL,
            reason TEXT,
            approved_by INTEGER,
            created_at TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (fee_head_id) REFERENCES fee_heads(id),
            FOREIGN KEY (approved_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS exemptions (
            id INTEGER PRIMARY KEY,
            student_id INTEGER,
            academic_year TEXT,
            fee_head_ids TEXT,
            reason TEXT,
            approved_by INTEGER,
            created_at TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (approved_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS cheque_tracker (
            id INTEGER PRIMARY KEY,
            payment_id INTEGER,
            cheque_no TEXT,
            bank TEXT,
            amount REAL,
            collected_on TEXT,
            status TEXT DEFAULT '{CHEQUE_STATUS_PENDING}',
            updated_at TEXT,
            FOREIGN KEY (payment_id) REFERENCES payments(id)
        );

        CREATE TABLE IF NOT EXISTS academic_years (
            id INTEGER PRIMARY KEY,
            label TEXT UNIQUE,
            start_date TEXT,
            end_date TEXT,
            is_active INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            user_id INTEGER,
            action TEXT,
            table_name TEXT,
            record_id TEXT,
            old_value TEXT,
            new_value TEXT,
            tamper_attempt INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS receipt_hashes (
            receipt_id INTEGER PRIMARY KEY,
            receipt_no TEXT UNIQUE NOT NULL,
            hmac_value TEXT,
            signed_fields_json TEXT,
            signed_at TEXT,
            algorithm TEXT NOT NULL DEFAULT 'HMAC-SHA256' CHECK(algorithm='HMAC-SHA256'),
            legacy_sha256_hash TEXT,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id)
        );

        CREATE TABLE IF NOT EXISTS backups_log (
            id INTEGER PRIMARY KEY,
            filename TEXT,
            created_at TEXT,
            created_by TEXT,
            type TEXT
        );
        """
    )


def _create_triggers(conn: sqlite3.Connection) -> None:
    """Create all required immutability and audit triggers."""
    conn.executescript(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_payments_no_delete
        BEFORE DELETE ON payments
        BEGIN
            SELECT RAISE(ABORT, '{TRG_PAYMENTS_DELETE_MSG}');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_payments_no_update
        BEFORE UPDATE ON payments
        BEGIN
            SELECT RAISE(ABORT, '{TRG_PAYMENTS_UPDATE_MSG}');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
        BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, '{TRG_AUDIT_DELETE_MSG}');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
        BEFORE UPDATE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, '{TRG_AUDIT_UPDATE_MSG}');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_receipts_no_delete
        BEFORE DELETE ON receipts
        BEGIN
            SELECT RAISE(ABORT, '{TRG_RECEIPTS_DELETE_MSG}');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_hash_no_delete
        BEFORE DELETE ON receipt_hashes
        BEGIN
            SELECT RAISE(ABORT, '{TRG_HASH_DELETE_MSG}');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_hash_no_update
        BEFORE UPDATE ON receipt_hashes
        BEGIN
            SELECT RAISE(ABORT, '{TRG_HASH_UPDATE_MSG}');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_discount_audit
        AFTER INSERT ON discounts
        BEGIN
            INSERT INTO audit_log (
                timestamp, user_id, action, table_name, record_id,
                old_value, new_value, tamper_attempt
            ) VALUES (
                strftime('%d-%m-%Y %H:%M:%S', 'now', 'localtime'),
                NEW.approved_by,
                '{ACTION_DISCOUNT_CREATED}',
                'discounts',
                NEW.id,
                NULL,
                'student_id=' || NEW.student_id || ';fee_head_id=' || NEW.fee_head_id || ';amount=' || NEW.amount,
                0
            );
        END;

        CREATE TRIGGER IF NOT EXISTS trg_exemption_audit
        AFTER INSERT ON exemptions
        BEGIN
            INSERT INTO audit_log (
                timestamp, user_id, action, table_name, record_id,
                old_value, new_value, tamper_attempt
            ) VALUES (
                strftime('%d-%m-%Y %H:%M:%S', 'now', 'localtime'),
                NEW.approved_by,
                '{ACTION_EXEMPTION_CREATED}',
                'exemptions',
                NEW.id,
                NULL,
                'student_id=' || NEW.student_id || ';academic_year=' || NEW.academic_year || ';fee_head_ids=' || NEW.fee_head_ids,
                0
            );
        END;
        """
    )


def _academic_year_values() -> tuple[str, str, str]:
    """Return the current academic-year label, start date, and end date."""
    today = datetime.now()
    start_year = today.year if today.month >= ACADEMIC_YEAR_START_MONTH else today.year - 1
    end_year = start_year + 1
    label = f"{start_year}-{str(end_year)[-2:]}"
    start_date = f"01-04-{start_year}"
    end_date = f"31-03-{end_year}"
    return label, start_date, end_date


def admin_exists(conn: sqlite3.Connection) -> bool:
    """Return whether at least one administrator account exists."""
    return conn.execute("SELECT 1 FROM users WHERE role = ? LIMIT 1", (DEFAULT_ADMIN_ROLE,)).fetchone() is not None


def bootstrap_required(conn: sqlite3.Connection) -> bool:
    """Return True when first-time setup must create the initial administrator."""
    return not admin_exists(conn)


def create_initial_admin(conn: sqlite3.Connection, username: str, password: str) -> int:
    """Create the first administrator after validating the bootstrap password policy."""
    username = str(username or "").strip()
    if not username:
        raise ValueError("Administrator username is required.")
    ok, message = validate_bootstrap_password(password)
    if not ok:
        raise ValueError(message)
    if admin_exists(conn):
        raise ValueError("An administrator account already exists.")
    password_hash = bcrypt.hashpw(str(password).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    cursor = conn.execute(
        "INSERT INTO users(username,password_hash,role,is_active,failed_attempts) VALUES(?,?,?,?,0)",
        (username, password_hash, DEFAULT_ADMIN_ROLE, DEFAULT_ADMIN_ACTIVE),
    )
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('setup_complete','1') "
        "ON CONFLICT(key) DO UPDATE SET value='1'"
    )
    return int(cursor.lastrowid)


def bootstrap_from_environment(conn: sqlite3.Connection) -> int | None:
    """Create the first admin from SFMS_BOOTSTRAP_PASSWORD for headless installs."""
    password = os.environ.pop("SFMS_BOOTSTRAP_PASSWORD", None)
    if password is None or not bootstrap_required(conn):
        return None
    username = os.environ.get("SFMS_BOOTSTRAP_USERNAME", DEFAULT_ADMIN_USERNAME)
    return create_initial_admin(conn, username, password)


def _seed_first_run(conn: sqlite3.Connection) -> None:
    """Create first-install year records and optional headless admin bootstrap."""
    bootstrap_from_environment(conn)
    year_count = conn.execute("SELECT COUNT(*) FROM academic_years").fetchone()[0]
    if not year_count:
        label, start_date, end_date = _academic_year_values()
        conn.execute(
            "INSERT INTO academic_years(label,start_date,end_date,is_active) VALUES(?,?,?,1)",
            (label, start_date, end_date),
        )


def init_db() -> None:
    """Initialize the SQLite database, triggers, seed data, and charge ledger."""
    from ledger import migrate_legacy_ledger
    from payment_controls import migrate_payment_controls
    from migrations import run_migrations

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        _apply_pragmas(conn)
        _create_tables(conn)
        _create_triggers(conn)
        _seed_first_run(conn)
        # Install structural migrations first. Immutability controls are installed
        # only after legacy rows have been normalized by their migrations.
        run_migrations(conn, through="v004_receipt_print_tracking")
        migrate_payment_controls(conn)
        migrate_legacy_ledger(conn)
        ensure_receipt_sequence(conn)
        run_migrations(conn)
