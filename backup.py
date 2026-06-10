"""Backup, encryption, restore, compaction, and archive services for SFMS."""

from __future__ import annotations

import base64
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from audit import log_operational_event
from config import BACKUPS_DIR, DB_PATH
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
BACKUP_MAGIC = b"SFMSENC2"
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


def _connection(conn=None):
    if conn is not None:
        return conn, False
    return sqlite3.connect(DB_PATH), True


def encrypt_backup(filepath, password=None, *, conn=None) -> str:
    """Encrypt a backup with the random DEK, never with a password hash."""
    global _UNLOCKED_DEK
    source = Path(filepath)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    connection, close = _connection(conn)
    try:
        dek = _unwrap_dek(connection, password) if password is not None else _UNLOCKED_DEK
        if dek is None:
            raise ValueError("Backup key is locked. Enter the master backup password.")
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = AESGCM(dek).encrypt(nonce, source.read_bytes(), BACKUP_MAGIC)
        encrypted_path = Path(f"{source}.enc")
        encrypted_path.write_bytes(BACKUP_MAGIC + nonce + ciphertext)
        source.unlink()
        return str(encrypted_path)
    finally:
        if close:
            connection.close()


def decrypt_backup(enc_filepath, password, *, conn=None) -> str:
    """Decrypt an encrypted backup after unwrapping the stable DEK."""
    encrypted = Path(enc_filepath)
    if not encrypted.is_file():
        raise FileNotFoundError(str(encrypted))
    payload = encrypted.read_bytes()
    if not payload.startswith(BACKUP_MAGIC) or len(payload) <= len(BACKUP_MAGIC)+NONCE_SIZE:
        raise ValueError("Wrong password")
    connection, close = _connection(conn)
    try:
        dek = _unwrap_dek(connection, password)
        nonce = payload[len(BACKUP_MAGIC):len(BACKUP_MAGIC)+NONCE_SIZE]
        try:
            plaintext = AESGCM(dek).decrypt(nonce, payload[len(BACKUP_MAGIC)+NONCE_SIZE:], BACKUP_MAGIC)
        except InvalidTag as exc:
            raise ValueError("Wrong password") from exc
    finally:
        if close:
            connection.close()
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
    conn.commit()
    _prune_backups()
    return final_path


def manual_backup(conn, created_by_user_id) -> str:
    """Create a manual backup for the supplied user ID or creator label."""
    return _create_backup(conn, created_by_user_id, "MANUAL")


def auto_backup(conn) -> str | None:
    """Create a system auto-backup only when the configured interval is overdue."""
    if not backup_overdue(conn):
        return None
    return _create_backup(conn, "SYSTEM", "AUTO")


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
    if str(selected).lower().endswith(".enc"):
        temporary_path = decrypt_backup(selected, password)
        database_path = Path(temporary_path)
    else:
        database_path = selected
    try:
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
