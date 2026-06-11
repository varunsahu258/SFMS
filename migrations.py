"""Versioned, idempotent SFMS database migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from config import (
    BACKUP_INTERVAL_DEFAULT, LOGO_PATH, RECEIPT_ISSUER_NAME, SCHOOL_ADDRESS, SCHOOL_NAME,
    SESSION_TIMEOUT_DEFAULT, SETTING_BACKUP_INTERVAL_HOURS, SETTING_LOGO_PATH,
    SETTING_RECEIPT_ISSUER_NAME, SETTING_SCHOOL_ADDRESS, SETTING_SCHOOL_NAME, SETTING_SESSION_TIMEOUT_MINUTES,
)
from utils import now_str

Migration = tuple[str, Callable[[sqlite3.Connection], None]]


def _setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))


def migration_v001_base_settings(conn: sqlite3.Connection) -> None:
    """Backfill settings that old databases missed because a user already existed."""
    defaults = {
        SETTING_SCHOOL_NAME: SCHOOL_NAME,
        SETTING_SCHOOL_ADDRESS: SCHOOL_ADDRESS,
        SETTING_LOGO_PATH: LOGO_PATH,
        SETTING_SESSION_TIMEOUT_MINUTES: str(SESSION_TIMEOUT_DEFAULT),
        SETTING_BACKUP_INTERVAL_HOURS: str(BACKUP_INTERVAL_DEFAULT),
        "ui_language": "en",
        "backup_encryption_enabled": "0",
    }
    for key, value in defaults.items():
        _setting(conn, key, value)


def migration_v002_setup_defaults(conn: sqlite3.Connection) -> None:
    """Ensure upgraded databases receive setup and appearance gates."""
    _setting(conn, "setup_complete", "0")
    _setting(conn, "ui_theme", "default")


def migration_v003_receipt_hmac(conn: sqlite3.Connection) -> None:
    """Install the keyed receipt-integrity storage format."""
    from receipt_integrity import install_receipt_hmac_schema

    install_receipt_hmac_schema(conn)


def migration_v004_receipt_print_tracking(conn: sqlite3.Connection) -> None:
    """Add immutable print history and durable post-commit failure records."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS receipt_print_history(
            id INTEGER PRIMARY KEY,
            receipt_id INTEGER NOT NULL,
            print_type TEXT NOT NULL CHECK(print_type IN ('ORIGINAL','REPRINT')),
            filename TEXT NOT NULL UNIQUE,
            file_sha256 TEXT NOT NULL,
            printed_at TEXT NOT NULL,
            printed_by INTEGER,
            FOREIGN KEY(receipt_id) REFERENCES receipts(id),
            FOREIGN KEY(printed_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS receipt_print_failures(
            id INTEGER PRIMARY KEY,
            receipt_id INTEGER NOT NULL,
            failed_at TEXT NOT NULL,
            error_message TEXT NOT NULL,
            FOREIGN KEY(receipt_id) REFERENCES receipts(id)
        );
        CREATE TRIGGER IF NOT EXISTS trg_print_history_no_update
        BEFORE UPDATE ON receipt_print_history BEGIN
            SELECT RAISE(ABORT,'receipt print history cannot be updated');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_print_history_no_delete
        BEFORE DELETE ON receipt_print_history BEGIN
            SELECT RAISE(ABORT,'receipt print history cannot be deleted');
        END;
        """
    )


def migration_v005_immutability_controls(conn: sqlite3.Connection) -> None:
    """Protect receipts and operational history while allowing explicit metadata updates."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "last_login" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
    conn.execute(
        "INSERT OR IGNORE INTO settings(key,value) VALUES('max_payment_amount','9999999.00')"
    )
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS trg_receipts_restricted_update;
        CREATE TRIGGER trg_receipts_restricted_update
        BEFORE UPDATE ON receipts
        WHEN NEW.id IS NOT OLD.id OR NEW.receipt_no IS NOT OLD.receipt_no
          OR NEW.student_id IS NOT OLD.student_id OR NEW.total_paid IS NOT OLD.total_paid
          OR NEW.receipt_type IS NOT OLD.receipt_type OR NEW.printed_at IS NOT OLD.printed_at
          OR NEW.printed_by IS NOT OLD.printed_by
        BEGIN
            SELECT RAISE(ABORT,'receipts: only reprint metadata may be updated');
        END;

        DROP TRIGGER IF EXISTS trg_discounts_no_delete;
        CREATE TRIGGER trg_discounts_no_delete BEFORE DELETE ON discounts BEGIN
            SELECT RAISE(ABORT,'discounts: deletion not permitted');
        END;
        DROP TRIGGER IF EXISTS trg_discounts_no_update;
        CREATE TRIGGER trg_discounts_no_update BEFORE UPDATE ON discounts BEGIN
            SELECT RAISE(ABORT,'discounts: update not permitted');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_discount_update_audit AFTER UPDATE ON discounts BEGIN
            INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,old_value,new_value,tamper_attempt)
            VALUES(strftime('%d-%m-%Y %H:%M:%S','now','localtime'),NEW.approved_by,
                   'DISCOUNT_UPDATED','discounts',NEW.id,
                   'student_id='||OLD.student_id||';fee_head_id='||OLD.fee_head_id||';amount='||OLD.amount,
                   'student_id='||NEW.student_id||';fee_head_id='||NEW.fee_head_id||';amount='||NEW.amount,1);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_discount_delete_audit AFTER DELETE ON discounts BEGIN
            INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,old_value,new_value,tamper_attempt)
            VALUES(strftime('%d-%m-%Y %H:%M:%S','now','localtime'),OLD.approved_by,
                   'DISCOUNT_DELETED','discounts',OLD.id,
                   'student_id='||OLD.student_id||';fee_head_id='||OLD.fee_head_id||';amount='||OLD.amount,NULL,1);
        END;

        DROP TRIGGER IF EXISTS trg_exemptions_no_delete;
        CREATE TRIGGER trg_exemptions_no_delete BEFORE DELETE ON exemptions BEGIN
            SELECT RAISE(ABORT,'exemptions: deletion not permitted');
        END;
        DROP TRIGGER IF EXISTS trg_exemptions_no_update;
        CREATE TRIGGER trg_exemptions_no_update BEFORE UPDATE ON exemptions BEGIN
            SELECT RAISE(ABORT,'exemptions: update not permitted');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_exemption_update_audit AFTER UPDATE ON exemptions BEGIN
            INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,old_value,new_value,tamper_attempt)
            VALUES(strftime('%d-%m-%Y %H:%M:%S','now','localtime'),NEW.approved_by,
                   'EXEMPTION_UPDATED','exemptions',NEW.id,
                   'student_id='||OLD.student_id||';academic_year='||OLD.academic_year||';fee_head_ids='||OLD.fee_head_ids,
                   'student_id='||NEW.student_id||';academic_year='||NEW.academic_year||';fee_head_ids='||NEW.fee_head_ids,1);
        END;
        CREATE TRIGGER IF NOT EXISTS trg_exemption_delete_audit AFTER DELETE ON exemptions BEGIN
            INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,old_value,new_value,tamper_attempt)
            VALUES(strftime('%d-%m-%Y %H:%M:%S','now','localtime'),OLD.approved_by,
                   'EXEMPTION_DELETED','exemptions',OLD.id,
                   'student_id='||OLD.student_id||';academic_year='||OLD.academic_year||';fee_head_ids='||OLD.fee_head_ids,NULL,1);
        END;

        DROP TRIGGER IF EXISTS trg_cheque_tracker_no_delete;
        CREATE TRIGGER trg_cheque_tracker_no_delete BEFORE DELETE ON cheque_tracker BEGIN
            SELECT RAISE(ABORT,'cheque_records: deletion not permitted');
        END;
        DROP TRIGGER IF EXISTS trg_cheque_tracker_restricted_update;
        CREATE TRIGGER trg_cheque_tracker_restricted_update BEFORE UPDATE ON cheque_tracker
        WHEN NEW.id IS NOT OLD.id OR NEW.payment_id IS NOT OLD.payment_id
          OR NEW.cheque_no IS NOT OLD.cheque_no OR NEW.bank IS NOT OLD.bank
          OR NEW.amount IS NOT OLD.amount OR NEW.collected_on IS NOT OLD.collected_on
        BEGIN
            SELECT RAISE(ABORT,'cheque_records: only status metadata may be updated');
        END;

        DROP TRIGGER IF EXISTS trg_users_no_delete;
        CREATE TRIGGER trg_users_no_delete BEFORE DELETE ON users BEGIN
            SELECT RAISE(ABORT,'users: deletion not permitted');
        END;
        DROP TRIGGER IF EXISTS trg_users_restricted_update;
        CREATE TRIGGER trg_users_restricted_update BEFORE UPDATE ON users
        WHEN NEW.id IS NOT OLD.id OR NEW.username IS NOT OLD.username
          OR NEW.role IS NOT OLD.role OR NEW.is_active IS NOT OLD.is_active
        BEGIN
            SELECT RAISE(ABORT,'users: username, role, and status are immutable');
        END;

        DROP TRIGGER IF EXISTS trg_settings_no_delete;
        CREATE TRIGGER trg_settings_no_delete BEFORE DELETE ON settings BEGIN
            SELECT RAISE(ABORT,'settings: deletion not permitted');
        END;
        DROP TRIGGER IF EXISTS trg_settings_update_audit;
        CREATE TRIGGER trg_settings_update_audit AFTER UPDATE ON settings BEGIN
            INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,old_value,new_value,tamper_attempt)
            VALUES(strftime('%d-%m-%Y %H:%M:%S','now','localtime'),NULL,
                   'SETTING_UPDATED','settings',NEW.key,OLD.value,NEW.value,0);
        END;

        DROP TRIGGER IF EXISTS trg_backups_log_no_delete;
        CREATE TRIGGER trg_backups_log_no_delete BEFORE DELETE ON backups_log BEGIN
            SELECT RAISE(ABORT,'backup_log: deletion not permitted');
        END;
        DROP TRIGGER IF EXISTS trg_backups_log_no_update;
        CREATE TRIGGER trg_backups_log_no_update BEFORE UPDATE ON backups_log BEGIN
            SELECT RAISE(ABORT,'backup_log: update not permitted');
        END;
        """
    )


def migration_v006_advance_and_backup_keys(conn: sqlite3.Connection) -> None:
    """Persist payment intent/term and initialize envelope-encryption settings."""
    payment_columns = {row[1] for row in conn.execute("PRAGMA table_info(payments)")}
    if "payment_intent" not in payment_columns:
        conn.execute(
            "ALTER TABLE payments ADD COLUMN payment_intent TEXT NOT NULL DEFAULT 'REGULAR' "
            "CHECK(payment_intent IN ('REGULAR','ADVANCE','VOID'))"
        )
    if "allocated_academic_year_id" not in payment_columns:
        conn.execute(
            "ALTER TABLE payments ADD COLUMN allocated_academic_year_id INTEGER "
            "REFERENCES academic_years(id)"
        )
    if "allocated_term" not in payment_columns:
        conn.execute("ALTER TABLE payments ADD COLUMN allocated_term TEXT")
    for key, value in (
        ("backup_encryption_enabled", "1"),
        ("backup_kdf_salt", ""),
        ("backup_wrapped_dek", ""),
        ("backup_wrap_nonce", ""),
    ):
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
    conn.execute("UPDATE settings SET value='1' WHERE key='backup_encryption_enabled'")


def migration_v007_remove_oauth_token_setting(conn: sqlite3.Connection) -> None:
    """Remove plaintext Google OAuth token material from SQLite settings."""
    conn.execute("DROP TRIGGER IF EXISTS trg_settings_no_delete")
    conn.execute("DELETE FROM settings WHERE key IN ('gdrive_token_json','oauth_token')")
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_settings_no_delete BEFORE DELETE ON settings BEGIN
            SELECT RAISE(ABORT,'settings: deletion not permitted');
        END;
        """
    )


def migration_v008_receipt_sequence(conn: sqlite3.Connection) -> None:
    """Create and seed the serialized receipt-number sequence."""
    from utils import ensure_receipt_sequence

    ensure_receipt_sequence(conn)


def migration_v009_class_section_and_student_details(conn: sqlite3.Connection) -> None:
    """Add class/section masters and the complete student admission profile."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS classes(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sections(
            id INTEGER PRIMARY KEY,
            class_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            UNIQUE(class_id,name),
            FOREIGN KEY(class_id) REFERENCES classes(id)
        );
        """
    )
    student_columns = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
    additions = {
        "scholar_no": "TEXT",
        "ekyc_status": "TEXT DEFAULT 'PENDING'",
        "serial_no": "TEXT",
        "father_name": "TEXT",
        "mother_name": "TEXT",
        "address": "TEXT",
        "dob": "TEXT",
        "admission_date": "TEXT",
        "mobile2": "TEXT",
        "sssm_id": "TEXT",
        "gender": "TEXT",
        "category": "TEXT",
    }
    for name, definition in additions.items():
        if name not in student_columns:
            conn.execute(f"ALTER TABLE students ADD COLUMN {name} {definition}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_students_scholar_no ON students(scholar_no) WHERE scholar_no IS NOT NULL AND scholar_no<>''")
    conn.execute("UPDATE students SET father_name=guardian_name WHERE COALESCE(father_name,'')='' AND COALESCE(guardian_name,'')<>''")
    conn.execute(
        "INSERT OR IGNORE INTO classes(name,created_at) "
        "SELECT DISTINCT class,? FROM students WHERE class IS NOT NULL AND TRIM(class)<>''",
        (now_str(),),
    )
    conn.execute(
        """INSERT OR IGNORE INTO sections(class_id,name,created_at)
           SELECT c.id,s.section,? FROM students s JOIN classes c ON c.name=s.class
           WHERE s.section IS NOT NULL AND TRIM(s.section)<>''""",
        (now_str(),),
    )


def migration_v010_accountant_permissions(conn: sqlite3.Connection) -> None:
    """Create per-accountant permission overrides managed by administrators."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_permissions(
               user_id INTEGER NOT NULL,
               permission_key TEXT NOT NULL,
               allowed INTEGER NOT NULL CHECK(allowed IN (0,1)),
               updated_at TEXT NOT NULL,
               updated_by INTEGER,
               PRIMARY KEY(user_id, permission_key),
               FOREIGN KEY(user_id) REFERENCES users(id),
               FOREIGN KEY(updated_by) REFERENCES users(id)
           )"""
    )


def migration_v011_receipt_issuer_setting(conn: sqlite3.Connection) -> None:
    """Add the configurable name printed beneath the receipt signature line."""
    _setting(conn, SETTING_RECEIPT_ISSUER_NAME, RECEIPT_ISSUER_NAME)



def migration_v012_timetable(conn: sqlite3.Connection) -> None:
    """Create automatic timetable setup, version, and schedule tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tt_subjects(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            code TEXT NOT NULL UNIQUE,
            is_lab INTEGER NOT NULL DEFAULT 0 CHECK(is_lab IN (0,1)),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tt_teachers(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT,
            max_periods_day INTEGER NOT NULL DEFAULT 6 CHECK(max_periods_day > 0),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tt_teacher_availability(
            teacher_id INTEGER NOT NULL,
            day TEXT NOT NULL CHECK(day IN ('MON','TUE','WED','THU','FRI','SAT')),
            arrives TEXT NOT NULL,
            departs TEXT NOT NULL,
            PRIMARY KEY(teacher_id,day),
            FOREIGN KEY(teacher_id) REFERENCES tt_teachers(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tt_teacher_constraints(
            teacher_id INTEGER NOT NULL,
            day TEXT NOT NULL CHECK(day IN ('MON','TUE','WED','THU','FRI','SAT')),
            period_no INTEGER NOT NULL CHECK(period_no > 0),
            ctype TEXT NOT NULL CHECK(ctype IN ('UNAVAILABLE','PREFERRED_FREE','PREFERRED_TEACH')),
            PRIMARY KEY(teacher_id,day,period_no),
            FOREIGN KEY(teacher_id) REFERENCES tt_teachers(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tt_assignments(
            teacher_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            PRIMARY KEY(teacher_id,subject_id,class_name),
            FOREIGN KEY(teacher_id) REFERENCES tt_teachers(id) ON DELETE CASCADE,
            FOREIGN KEY(subject_id) REFERENCES tt_subjects(id) ON DELETE CASCADE,
            FOREIGN KEY(class_name) REFERENCES classes(name)
        );
        CREATE TABLE IF NOT EXISTS tt_subject_requirements(
            subject_id INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            periods_per_week INTEGER NOT NULL CHECK(periods_per_week >= 0),
            double_period_allowed INTEGER NOT NULL DEFAULT 0 CHECK(double_period_allowed IN (0,1)),
            PRIMARY KEY(subject_id,class_name),
            FOREIGN KEY(subject_id) REFERENCES tt_subjects(id) ON DELETE CASCADE,
            FOREIGN KEY(class_name) REFERENCES classes(name)
        );
        CREATE TABLE IF NOT EXISTS tt_schedule_config(
            id INTEGER PRIMARY KEY CHECK(id=1),
            periods_per_day INTEGER NOT NULL CHECK(periods_per_day > 0),
            working_days TEXT NOT NULL,
            period_duration_min INTEGER NOT NULL CHECK(period_duration_min > 0),
            day_start_time TEXT NOT NULL,
            break_after_period INTEGER,
            break_duration_min INTEGER NOT NULL DEFAULT 0 CHECK(break_duration_min >= 0),
            lunch_after_period INTEGER,
            lunch_duration_min INTEGER NOT NULL DEFAULT 0 CHECK(lunch_duration_min >= 0)
        );
        CREATE TABLE IF NOT EXISTS tt_versions(
            id INTEGER PRIMARY KEY,
            label TEXT NOT NULL,
            academic_year TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            generated_by INTEGER,
            is_published INTEGER NOT NULL DEFAULT 0 CHECK(is_published IN (0,1)),
            FOREIGN KEY(generated_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS tt_timetable(
            version_id INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            day TEXT NOT NULL CHECK(day IN ('MON','TUE','WED','THU','FRI','SAT')),
            period_no INTEGER NOT NULL CHECK(period_no > 0),
            subject_id INTEGER,
            teacher_id INTEGER,
            is_free INTEGER NOT NULL DEFAULT 0 CHECK(is_free IN (0,1)),
            is_locked INTEGER NOT NULL DEFAULT 0 CHECK(is_locked IN (0,1)),
            PRIMARY KEY(version_id,class_name,day,period_no),
            FOREIGN KEY(version_id) REFERENCES tt_versions(id) ON DELETE CASCADE,
            FOREIGN KEY(class_name) REFERENCES classes(name),
            FOREIGN KEY(subject_id) REFERENCES tt_subjects(id),
            FOREIGN KEY(teacher_id) REFERENCES tt_teachers(id)
        );
        CREATE INDEX IF NOT EXISTS idx_tt_timetable_teacher
            ON tt_timetable(version_id,teacher_id,day,period_no);
        INSERT OR IGNORE INTO tt_schedule_config(
            id,periods_per_day,working_days,period_duration_min,day_start_time,
            break_after_period,break_duration_min,lunch_after_period,lunch_duration_min
        ) VALUES(1,8,'MON,TUE,WED,THU,FRI,SAT',40,'08:00',2,10,5,30);
        """
    )


def migration_v013_late_fees(conn: sqlite3.Connection) -> None:
    """Add audited, one-row-per-assessment late-fee records."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS late_fee_assessments(
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL,
            charge_id INTEGER NOT NULL UNIQUE,
            amount REAL NOT NULL CHECK(amount > 0),
            due_date TEXT NOT NULL,
            reason TEXT NOT NULL,
            assessed_at TEXT NOT NULL,
            assessed_by INTEGER,
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(charge_id) REFERENCES student_charges(id),
            FOREIGN KEY(assessed_by) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_late_fees_student ON late_fee_assessments(student_id,assessed_at);
        """
    )


MIGRATIONS: tuple[Migration, ...] = (
    ("v001_base_settings", migration_v001_base_settings),
    ("v002_setup_defaults", migration_v002_setup_defaults),
    ("v003_receipt_hmac", migration_v003_receipt_hmac),
    ("v004_receipt_print_tracking", migration_v004_receipt_print_tracking),
    ("v005_immutability_controls", migration_v005_immutability_controls),
    ("v006_advance_and_backup_keys", migration_v006_advance_and_backup_keys),
    ("v007_remove_oauth_token_setting", migration_v007_remove_oauth_token_setting),
    ("v008_receipt_sequence", migration_v008_receipt_sequence),
    ("v009_class_section_and_student_details", migration_v009_class_section_and_student_details),
    ("v010_accountant_permissions", migration_v010_accountant_permissions),
    ("v011_receipt_issuer_setting", migration_v011_receipt_issuer_setting),
    ("v012_timetable", migration_v012_timetable),
    ("v013_late_fees", migration_v013_late_fees),
)


def run_migrations(conn: sqlite3.Connection, through: str | None = None) -> list[str]:
    """Apply unapplied migrations in order, optionally stopping at ``through``."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations(
               migration_id TEXT PRIMARY KEY,
               applied_at TEXT NOT NULL
           )"""
    )
    applied = {row[0] for row in conn.execute("SELECT migration_id FROM schema_migrations")}
    completed: list[str] = []
    for migration_id, migration in sorted(MIGRATIONS, key=lambda item: item[0]):
        if through is not None and migration_id > through:
            continue
        if migration_id in applied:
            continue
        with conn:
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(migration_id,applied_at) VALUES(?,?)",
                (migration_id, now_str()),
            )
        completed.append(migration_id)
    return completed
