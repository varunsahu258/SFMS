"""Envelope encryption and password rotation tests."""

import sqlite3
from pathlib import Path

import pytest

import backup


def settings_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
    return conn


def test_password_rotation_preserves_old_backup(tmp_path):
    conn = settings_db()
    backup.setup_backup_password(conn, "password-X")
    plain = tmp_path / "backup.db"
    plain.write_bytes(b"SQLite format 3\x00test")
    encrypted = backup.encrypt_backup(plain, "password-X", conn=conn)
    with pytest.raises(ValueError):
        backup.decrypt_backup(encrypted, "wrong-pass", conn=conn)
    backup.rotate_backup_password(conn, "password-X", "password-Y")
    restored = Path(backup.decrypt_backup(encrypted, "password-Y", conn=conn))
    try:
        assert restored.read_bytes() == b"SQLite format 3\x00test"
    finally:
        restored.unlink()


def test_legacy_bcrypt_hash_is_not_an_encryption_key(tmp_path):
    conn = settings_db()
    backup.setup_backup_password(conn, "owner-password")
    plain = tmp_path / "backup.db"
    plain.write_bytes(b"database")
    encrypted = backup.encrypt_backup(plain, "owner-password", conn=conn)
    with pytest.raises(ValueError):
        backup.decrypt_backup(encrypted, "$2b$12$not-the-password-hash", conn=conn)
