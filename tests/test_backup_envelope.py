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


def test_recovery_envelope_restores_database_and_integrity_key_without_live_db(tmp_path, monkeypatch):
    import base64
    import receipt_integrity

    conn = settings_db()
    key = b"r" * 32
    monkeypatch.setenv(receipt_integrity.ENV_KEY, base64.urlsafe_b64encode(key).decode("ascii"))
    backup.setup_backup_password(conn, "recovery-password")
    plain = tmp_path / "disaster.db"
    database_bytes = b"SQLite format 3\x00recoverable"
    plain.write_bytes(database_bytes)

    encrypted = backup.encrypt_backup(plain, conn=conn)
    restored_bytes, restored_key = backup._decrypt_backup_bytes(encrypted, "recovery-password")

    assert restored_bytes == database_bytes
    assert restored_key == key


def test_recovery_envelope_detects_tampering(tmp_path, monkeypatch):
    import base64
    import receipt_integrity

    conn = settings_db()
    monkeypatch.setenv(
        receipt_integrity.ENV_KEY,
        base64.urlsafe_b64encode(b"t" * 32).decode("ascii"),
    )
    backup.setup_backup_password(conn, "recovery-password")
    plain = tmp_path / "tamper.db"
    plain.write_bytes(b"SQLite format 3\x00test")
    encrypted = Path(backup.encrypt_backup(plain, conn=conn))
    payload = bytearray(encrypted.read_bytes())
    payload[-1] ^= 1
    encrypted.write_bytes(payload)

    with pytest.raises(ValueError, match="damaged or tampered"):
        backup._decrypt_backup_bytes(encrypted, "recovery-password")
