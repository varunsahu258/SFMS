"""Backup, encryption, restore, compaction, and archive services for SFMS."""

from __future__ import annotations

import base64
import os
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ModuleNotFoundError:  # optional encryption dependency in lightweight installs
    class InvalidTag(Exception):
        pass
    hashes = AESGCM = PBKDF2HMAC = None

from app_events import signal_backup_warning
from audit import log_operational_event
from config import BACKUPS_DIR, DB_PATH, INTEGRITY_KEY_PATH
from notifications import backup_overdue
from utils import now_str

ENCRYPTION_SETTING = "backup_encryption_enabled"
KDF_SALT_SETTING = "backup_kdf_salt"
WRAPPED_DEK_SETTING = "backup_wrapped_dek"
WRAP_NONCE_SETTING = "backup_wrap_nonce"
PBKDF2_ITERATIONS = 260_000
SALT_SIZE = 16
NONCE_SIZE = 12
DEK_SIZE = 32
MAX_BACKUP_FILES = 30
CONSECUTIVE_FAILURES_SETTING = "consecutive_backup_failures"
LAST_SUCCESS_SETTING = "last_successful_backup_at"
BACKUP_MAGIC = b"SFMSENC2"
RECOVERY_BACKUP_MAGIC = b"SFMSENC3"
KEY_LENGTH_SIZE = 2

REQUIRED_TABLES = [
    "users", "students", "fee_heads", "fee_structure", "payments", "receipts",
    "settings", "audit_log", "receipt_hashes", "backups_log", "schema_migrations",
]
REQUIRED_TRIGGERS = [
    "trg_payments_no_delete", "trg_payments_no_update", "trg_audit_no_delete",
    "trg_audit_no_update", "trg_receipts_no_delete", "trg_hash_no_delete",
    "trg_hash_no_update", "trg_receipts_restricted_update",
]
REQUIRED_INDEXES = ["sqlite_autoindex_users_1", "sqlite_autoindex_settings_1"]
_UNLOCKED_DEK: bytes | None = None


def _setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row[0] or default) if row else default


def _set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
    )


def _creator(conn: sqlite3.Connection, created_by) -> tuple[int | None, str]:
    if isinstance(created_by, int):
        row = conn.execute("SELECT username FROM users WHERE id = ?", (created_by,)).fetchone()
        return created_by, str(row[0]) if row else str(created_by)
    return None, str(created_by or "SYSTEM")


def _derive_key(password: str, salt: bytes) -> bytes:
    if not password:
        raise ValueError("Backup password is required.")
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                      iterations=PBKDF2_ITERATIONS).derive(password.encode("utf-8"))


def _decode_setting(conn, key: str) -> bytes:
    value = _setting(conn, key)
    if not value:
        raise ValueError("Set the master backup password before creating backups.")
    return base64.b64decode(value)


def setup_backup_password(conn: sqlite3.Connection, password: str) -> None:
    """Create and wrap a random backup DEK; no password-derived material is stored."""
    global _UNLOCKED_DEK
    if not str(password):
        raise ValueError("Backup password is required.")
    if _setting(conn, WRAPPED_DEK_SETTING):
        raise ValueError("A backup password already exists; use password rotation.")
    salt, nonce, dek = os.urandom(SALT_SIZE), os.urandom(NONCE_SIZE), os.urandom(DEK_SIZE)
    wrapped = AESGCM(_derive_key(password, salt)).encrypt(nonce, dek, b"SFMS-DEK")
    _set_setting(conn, KDF_SALT_SETTING, base64.b64encode(salt).decode("ascii"))
    _set_setting(conn, WRAP_NONCE_SETTING, base64.b64encode(nonce).decode("ascii"))
    _set_setting(conn, WRAPPED_DEK_SETTING, base64.b64encode(wrapped).decode("ascii"))
    _set_setting(conn, ENCRYPTION_SETTING, "1")
    _UNLOCKED_DEK = dek


def _unwrap_dek(conn: sqlite3.Connection, password: str) -> bytes:
    try:
        return AESGCM(_derive_key(password, _decode_setting(conn, KDF_SALT_SETTING))).decrypt(
            _decode_setting(conn, WRAP_NONCE_SETTING),
            _decode_setting(conn, WRAPPED_DEK_SETTING), b"SFMS-DEK"
        )
    except (InvalidTag, ValueError, TypeError) as exc:
        raise ValueError("Wrong password") from exc


def unlock_backup_key(conn: sqlite3.Connection, password: str) -> None:
    global _UNLOCKED_DEK
    _UNLOCKED_DEK = _unwrap_dek(conn, password)


def rotate_backup_password(conn: sqlite3.Connection, old_password: str, new_password: str) -> None:
    """Re-wrap the unchanged DEK, preserving decryptability of every old backup."""
    global _UNLOCKED_DEK
    if not str(new_password):
        raise ValueError("Backup password is required.")
    dek = _unwrap_dek(conn, old_password)
    salt, nonce = os.urandom(SALT_SIZE), os.urandom(NONCE_SIZE)
    wrapped = AESGCM(_derive_key(new_password, salt)).encrypt(nonce, dek, b"SFMS-DEK")
    _set_setting(conn, KDF_SALT_SETTING, base64.b64encode(salt).decode("ascii"))
    _set_setting(conn, WRAP_NONCE_SETTING, base64.b64encode(nonce).decode("ascii"))
    _set_setting(conn, WRAPPED_DEK_SETTING, base64.b64encode(wrapped).decode("ascii"))
    _UNLOCKED_DEK = dek



def backup_status(conn: sqlite3.Connection) -> dict[str, str | int]:
    """Return dashboard-friendly backup status details."""
    return {
        "last_successful_backup_at": _setting(conn, LAST_SUCCESS_SETTING, "Never"),
        "consecutive_backup_failures": int(_setting(conn, CONSECUTIVE_FAILURES_SETTING, "0") or 0),
    }


def record_auto_backup_success(conn: sqlite3.Connection, backup_path: str | None) -> None:
    """Reset failure state and audit a successful automatic backup."""
    if not backup_path:
        return
    timestamp = now_str()
    _set_setting(conn, CONSECUTIVE_FAILURES_SETTING, "0")
    _set_setting(conn, LAST_SUCCESS_SETTING, timestamp)
    log_operational_event(
        "BACKUP_SUCCESS", None,
        {"table": "backups_log", "record_id": backup_path, "succeeded_at": timestamp},
        conn=conn,
    )
    conn.commit()


def record_auto_backup_failure(conn: sqlite3.Connection, exc: Exception) -> int:
    """Persist an automatic-backup failure and signal UI after repeated failures."""
    attempted_at = now_str()
    failures = int(_setting(conn, CONSECUTIVE_FAILURES_SETTING, "0") or 0) + 1
    _set_setting(conn, CONSECUTIVE_FAILURES_SETTING, str(failures))
    log_operational_event(
        "BACKUP_FAILED", None,
        {
            "table": "backups_log",
            "record_id": attempted_at,
            "attempted_at": attempted_at,
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:500],
        },
        conn=conn,
    )
    conn.commit()
    if failures >= 3:
        signal_backup_warning(failures)
    return failures

def _connection(conn=None):
    if conn is not None:
        return conn, False
    return sqlite3.connect(DB_PATH), True


def _recovery_payload(database: bytes, integrity_key_value: bytes) -> bytes:
    if len(integrity_key_value) > 65535:
        raise ValueError("Integrity key is too large for the recovery envelope.")
    return len(integrity_key_value).to_bytes(KEY_LENGTH_SIZE, "big") + integrity_key_value + database


def _split_recovery_payload(payload: bytes) -> tuple[bytes, bytes]:
    if len(payload) < KEY_LENGTH_SIZE:
        raise ValueError("Backup recovery payload is invalid.")
    key_length = int.from_bytes(payload[:KEY_LENGTH_SIZE], "big")
    boundary = KEY_LENGTH_SIZE + key_length
    if key_length < 32 or len(payload) <= boundary:
        raise ValueError("Backup recovery payload is invalid.")
    return payload[boundary:], payload[KEY_LENGTH_SIZE:boundary]


def encrypt_backup(filepath, password=None, *, conn=None) -> str:
    """Encrypt the database and integrity key in a self-contained recovery envelope."""
    global _UNLOCKED_DEK
    source = Path(filepath)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    connection, close = _connection(conn)
    try:
        dek = _unwrap_dek(connection, password) if password is not None else _UNLOCKED_DEK
        if dek is None:
            raise ValueError("Backup key is locked. Enter the master backup password.")
        from receipt_integrity import integrity_key_for_database

        recovery_payload = _recovery_payload(
            source.read_bytes(), integrity_key_for_database(connection)
        )
        salt = _decode_setting(connection, KDF_SALT_SETTING)
        wrap_nonce = _decode_setting(connection, WRAP_NONCE_SETTING)
        wrapped_dek = _decode_setting(connection, WRAPPED_DEK_SETTING)
        if len(wrapped_dek) > 65535:
            raise ValueError("Wrapped backup key is too large.")
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = AESGCM(dek).encrypt(nonce, recovery_payload, RECOVERY_BACKUP_MAGIC)
        encrypted_path = Path(f"{source}.enc")
        encrypted_path.write_bytes(
            RECOVERY_BACKUP_MAGIC
            + salt
            + wrap_nonce
            + len(wrapped_dek).to_bytes(KEY_LENGTH_SIZE, "big")
            + wrapped_dek
            + nonce
            + ciphertext
        )
        source.unlink()
        return str(encrypted_path)
    finally:
        if close:
            connection.close()


def _decrypt_recovery_envelope(payload: bytes, password: str, conn=None) -> tuple[bytes, bytes]:
    offset = len(RECOVERY_BACKUP_MAGIC)
    minimum = offset + SALT_SIZE + NONCE_SIZE + KEY_LENGTH_SIZE + NONCE_SIZE + 16
    if len(payload) <= minimum:
        raise ValueError("Backup recovery envelope is invalid.")
    salt = payload[offset:offset + SALT_SIZE]
    offset += SALT_SIZE
    wrap_nonce = payload[offset:offset + NONCE_SIZE]
    offset += NONCE_SIZE
    wrapped_length = int.from_bytes(payload[offset:offset + KEY_LENGTH_SIZE], "big")
    offset += KEY_LENGTH_SIZE
    wrapped_dek = payload[offset:offset + wrapped_length]
    offset += wrapped_length
    nonce = payload[offset:offset + NONCE_SIZE]
    ciphertext = payload[offset + NONCE_SIZE:]
    try:
        dek = AESGCM(_derive_key(password, salt)).decrypt(wrap_nonce, wrapped_dek, b"SFMS-DEK")
    except (InvalidTag, ValueError, TypeError):
        if conn is None:
            raise ValueError("Wrong password") from None
        try:
            dek = _unwrap_dek(conn, password)
        except ValueError:
            raise ValueError("Wrong password") from None
    try:
        plaintext = AESGCM(dek).decrypt(nonce, ciphertext, RECOVERY_BACKUP_MAGIC)
    except InvalidTag as exc:
        raise ValueError("Backup authentication failed; the file may be damaged or tampered with.") from exc
    return _split_recovery_payload(plaintext)


def _decrypt_backup_bytes(enc_filepath, password, *, conn=None) -> tuple[bytes, bytes | None]:
    encrypted = Path(enc_filepath)
    if not encrypted.is_file():
        raise FileNotFoundError(str(encrypted))
    payload = encrypted.read_bytes()
    if payload.startswith(RECOVERY_BACKUP_MAGIC):
        connection, close = _connection(conn) if conn is not None else (None, False)
        try:
            return _decrypt_recovery_envelope(payload, password, connection)
        finally:
            if close and connection is not None:
                connection.close()
    if not payload.startswith(BACKUP_MAGIC) or len(payload) <= len(BACKUP_MAGIC) + NONCE_SIZE:
        raise ValueError("Wrong password")
    connection, close = _connection(conn)
    try:
        dek = _unwrap_dek(connection, password)
        nonce = payload[len(BACKUP_MAGIC):len(BACKUP_MAGIC) + NONCE_SIZE]
        try:
            plaintext = AESGCM(dek).decrypt(
                nonce, payload[len(BACKUP_MAGIC) + NONCE_SIZE:], BACKUP_MAGIC
            )
        except InvalidTag as exc:
            raise ValueError("Wrong password") from exc
        return plaintext, None
    finally:
        if close:
            connection.close()


def decrypt_backup(enc_filepath, password, *, conn=None) -> str:
    """Decrypt a backup to a temporary database; recovery key installation is deferred."""
    plaintext, _integrity_key_value = _decrypt_backup_bytes(enc_filepath, password, conn=conn)
    handle = tempfile.NamedTemporaryFile(prefix="sfms_restore_", suffix=".db", delete=False)
    try:
        handle.write(plaintext)
        return handle.name
    finally:
        handle.close()


def _prune_backups() -> None:
    """Keep only the newest configured number of regular backup files."""
    directory = Path(BACKUPS_DIR)
    candidates = [
        path for path in directory.glob("sfms_backup_*.db*")
        if path.is_file()
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for old_file in candidates[MAX_BACKUP_FILES:]:
        old_file.unlink(missing_ok=True)


def _create_backup(conn: sqlite3.Connection, created_by, backup_type: str) -> str:
    """Create, optionally encrypt, log, audit, and prune one SQLite backup."""
    Path(BACKUPS_DIR).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destination_path = Path(BACKUPS_DIR) / f"sfms_backup_{timestamp}.db"
    with sqlite3.connect(destination_path) as destination:
        conn.backup(destination)

    if _setting(conn, ENCRYPTION_SETTING, "1") != "1":
        destination_path.unlink(missing_ok=True)
        raise ValueError("Unencrypted backups are disabled. Configure backup encryption first.")
    if not _setting(conn, WRAPPED_DEK_SETTING):
        destination_path.unlink(missing_ok=True)
        raise ValueError("Set the master backup password before creating backups.")
    try:
        final_path = encrypt_backup(destination_path, conn=conn)
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise

    user_id, creator_name = _creator(conn, created_by)
    conn.execute(
        """
        INSERT INTO backups_log (filename, created_at, created_by, type)
        VALUES (?, ?, ?, ?)
        """,
        (final_path, now_str(), creator_name, backup_type),
    )
    log_operational_event(
        "BACKUP_CREATED", user_id,
        {"table": "backups_log", "record_id": final_path,
         "type": backup_type, "created_by": creator_name}, conn=conn,
    )
    if backup_type == "AUTO":
        record_auto_backup_success(conn, final_path)
    else:
        conn.commit()
    _prune_backups()
    return final_path


def manual_backup(conn, created_by_user_id) -> str:
    """Create a manual backup for the supplied user ID or creator label."""
    return _create_backup(conn, created_by_user_id, "MANUAL")


def auto_backup(conn) -> str | None:
    """Create an automatic backup and copy it off-machine when Drive is configured."""
    if not backup_overdue(conn):
        return None
    path = _create_backup(conn, "SYSTEM", "AUTO")
    try:
        from gdrive import upload_to_drive

        upload_to_drive(path)
    except Exception as exc:
        log_operational_event(
            "DRIVE_BACKUP_FAILED",
            None,
            {
                "table": "backups_log",
                "record_id": path,
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:500],
            },
            conn=conn,
        )
        conn.commit()
    return path



def _names(conn: sqlite3.Connection, object_type: str) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (object_type,))}


def validate_backup_for_restore(backup_path: str) -> tuple[bool, list[str]]:
    """Validate a backup database thoroughly before it can replace the live DB."""
    reasons: list[str] = []
    path = Path(backup_path)
    if not path.is_file():
        return False, [f"Backup file does not exist: {backup_path}"]
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                if not integrity or str(integrity[0]).lower() != "ok":
                    reasons.append(f"Integrity check failed: {integrity[0] if integrity else 'no result'}")
            except sqlite3.Error as exc:
                reasons.append(f"Integrity check failed: {exc}")
            try:
                fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
                if fk_rows:
                    reasons.append(f"Foreign-key check failed: {len(fk_rows)} violation(s)")
            except sqlite3.Error as exc:
                reasons.append(f"Foreign-key check failed: {exc}")

            tables = _names(conn, "table")
            for table in REQUIRED_TABLES:
                if table not in tables:
                    reasons.append(f"Missing required table: {table}")
            triggers = _names(conn, "trigger")
            for trigger in REQUIRED_TRIGGERS:
                if trigger not in triggers:
                    reasons.append(f"Missing required trigger: {trigger}")
            indexes = _names(conn, "index")
            for index in REQUIRED_INDEXES:
                if index not in indexes:
                    reasons.append(f"Missing required index: {index}")

            if "schema_migrations" in tables:
                from migrations import MIGRATIONS

                known = [migration_id for migration_id, _ in MIGRATIONS]
                applied = [row[0] for row in conn.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id")]
                unknown = [migration_id for migration_id in applied if migration_id not in known]
                if unknown:
                    reasons.append(f"Backup schema is newer than this application: {', '.join(unknown)}")
                elif applied and applied != known[:len(applied)]:
                    reasons.append("Schema migration history is not a valid upgrade path")
            if "users" in tables:
                admin = conn.execute("SELECT 1 FROM users WHERE role='ADMIN' LIMIT 1").fetchone()
                if admin is None:
                    reasons.append("Backup contains no ADMIN user")
            if "settings" in tables:
                rows = conn.execute("SELECT key,value FROM settings WHERE lower(key) LIKE '%key%' OR lower(key) LIKE '%token%' OR lower(key) LIKE '%secret%'").fetchall()
                for row in rows:
                    if str(row["value"] or "").startswith("$2b$"):
                        reasons.append(f"Suspicious bcrypt hash stored in sensitive setting: {row['key']}")
            if {"receipts", "receipt_hashes"}.issubset(tables):
                try:
                    from receipt_integrity import ALGORITHM, compute_receipt_hmac

                    rows = conn.execute(
                        "SELECT receipt_id,hmac_value,signed_fields_json,algorithm FROM receipt_hashes ORDER BY receipt_id LIMIT 20"
                    ).fetchall()
                    for row in rows:
                        if row["algorithm"] != ALGORITHM:
                            reasons.append(f"Receipt {row['receipt_id']} uses unsupported hash algorithm")
                            continue
                        expected = compute_receipt_hmac(json.loads(row["signed_fields_json"] or "{}"))
                        if expected != row["hmac_value"]:
                            reasons.append(f"Receipt HMAC mismatch for receipt_id {row['receipt_id']}")
                except Exception as exc:
                    reasons.append(f"Receipt HMAC spot-check failed: {exc}")
    except sqlite3.Error as exc:
        reasons.append(f"Unable to open backup database: {exc}")
    return not reasons, reasons

def preview_backup(db_filepath) -> dict:
    """Open a backup read-only and return safe summary counts and year labels."""
    path = Path(db_filepath).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        payments = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        receipts = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        years = [row[0] for row in conn.execute("SELECT label FROM academic_years ORDER BY label")]
    return {
        "students": int(students),
        "payments": int(payments),
        "receipts": int(receipts),
        "academic_years": years,
        "backup_date": datetime.fromtimestamp(path.stat().st_mtime).strftime("%d-%m-%Y %H:%M:%S"),
    }


def restore_backup(backup_filepath, password=None) -> bool:
    """Preview, confirm, replace the live database, and restart the application."""
    selected = Path(backup_filepath)
    temporary_path: str | None = None
    recovered_integrity_key: bytes | None = None
    if str(selected).lower().endswith(".enc"):
        plaintext, recovered_integrity_key = _decrypt_backup_bytes(selected, password)
        handle = tempfile.NamedTemporaryFile(prefix="sfms_restore_", suffix=".db", delete=False)
        try:
            handle.write(plaintext)
            temporary_path = handle.name
        finally:
            handle.close()
        database_path = Path(temporary_path)
    else:
        database_path = selected
    try:
        valid, validation_errors = validate_backup_for_restore(str(database_path))
        if not valid:
            messagebox.showerror(
                "Restore Backup Validation Failed",
                "The selected backup cannot be restored:\n\n" + "\n".join(validation_errors),
            )
            return False
        preview = preview_backup(database_path)
        message = (
            f"Backup date: {preview['backup_date']}\n"
            f"Students: {preview['students']}\n"
            f"Payments: {preview['payments']}\n"
            f"Receipts: {preview['receipts']}\n"
            f"Academic years: {', '.join(preview['academic_years']) or 'None'}\n\n"
            "Replace the live database and restart SFMS?"
        )
        if not messagebox.askyesno("Restore Backup Preview", message):
            return False
        live_path = Path(DB_PATH)
        live_path.parent.mkdir(parents=True, exist_ok=True)
        replacement = live_path.with_name(f"{live_path.name}.restore_tmp")
        shutil.copy2(database_path, replacement)
        for suffix in ("-wal", "-shm"):
            Path(f"{live_path}{suffix}").unlink(missing_ok=True)
        os.replace(replacement, live_path)
        if recovered_integrity_key is not None:
            encoded_key = base64.urlsafe_b64encode(recovered_integrity_key).decode("ascii") + "\n"
            key_path = Path(INTEGRITY_KEY_PATH)
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_replacement = key_path.with_name(f"{key_path.name}.restore_tmp")
            key_replacement.write_text(encoded_key, encoding="ascii")
            try:
                key_replacement.chmod(0o600)
            except OSError:
                pass
            os.replace(key_replacement, key_path)
        os.execv(sys.executable, [sys.executable] + sys.argv)
        return True
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)


def compact_db(conn) -> tuple[int, int]:
    """VACUUM the live database and return byte sizes before and after."""
    before = os.path.getsize(DB_PATH)
    conn.commit()
    conn.execute("VACUUM")
    after = os.path.getsize(DB_PATH)
    return before, after


def archive_year(conn, academic_year_label) -> str:
    """Copy the complete live database to a year-labelled archive without clearing it."""
    label = "".join(character for character in str(academic_year_label) if character.isalnum() or character in ("-", "_"))
    if not label:
        raise ValueError("Select an academic year.")
    Path(BACKUPS_DIR).mkdir(parents=True, exist_ok=True)
    archive_path = Path(BACKUPS_DIR) / f"archive_{label}.db"
    with sqlite3.connect(archive_path) as destination:
        conn.backup(destination)
    return str(archive_path)
