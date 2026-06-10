"""Versioned, idempotent SFMS database migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from config import (
    BACKUP_INTERVAL_DEFAULT, LOGO_PATH, SCHOOL_ADDRESS, SCHOOL_NAME,
    SESSION_TIMEOUT_DEFAULT, SETTING_BACKUP_INTERVAL_HOURS, SETTING_LOGO_PATH,
    SETTING_SCHOOL_ADDRESS, SETTING_SCHOOL_NAME, SETTING_SESSION_TIMEOUT_MINUTES,
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
        "master_backup_password_hash": "",
        "gdrive_token_json": "",
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


MIGRATIONS: tuple[Migration, ...] = (
    ("v001_base_settings", migration_v001_base_settings),
    ("v002_setup_defaults", migration_v002_setup_defaults),
    ("v003_receipt_hmac", migration_v003_receipt_hmac),
)


def run_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply every unapplied migration in stable version order."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations(
               migration_id TEXT PRIMARY KEY,
               applied_at TEXT NOT NULL
           )"""
    )
    applied = {row[0] for row in conn.execute("SELECT migration_id FROM schema_migrations")}
    completed: list[str] = []
    for migration_id, migration in sorted(MIGRATIONS, key=lambda item: item[0]):
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
