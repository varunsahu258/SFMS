"""Environment-keyed HMAC signing for complete SFMS receipts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from pathlib import Path

from config import DB_PATH, INTEGRITY_KEY_PATH
from utils import now_str

ENV_KEY = "SFMS_INTEGRITY_KEY"
ALGORITHM = "HMAC-SHA256"
SIGNED_PAYMENT_FIELDS = (
    "receipt_no", "student_id", "amount_paid", "payment_date", "fee_head_id",
    "amount_due", "balance", "payment_mode", "cheque_or_upi_ref", "collected_by",
    "receipt_type", "receipt_total",
)


def generate_integrity_key_file(filepath: str) -> str:
    """Write a new base64 32-byte key outside the database directory exactly once."""
    destination = Path(filepath).expanduser().resolve()
    db_directory = Path(DB_PATH).expanduser().resolve().parent
    if destination.parent == db_directory or db_directory in destination.parents:
        raise ValueError("Integrity key file must be outside the database directory tree.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    with destination.open("x", encoding="ascii") as handle:
        handle.write(encoded + "\n")
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    return str(destination)


def _decode_integrity_key(value: str, source: str) -> bytes:
    try:
        key = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise RuntimeError(f"{source} must contain a base64-encoded 32-byte key.") from exc
    if len(key) < 32:
        raise RuntimeError(f"{source} must decode to at least 32 bytes.")
    return key


def _load_or_create_integrity_key_file(path: Path) -> str:
    """Return the persistent key, creating it atomically on first launch."""
    path = path.expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
        with path.open("x", encoding="ascii") as handle:
            handle.write(encoded + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return encoded
    except FileExistsError:
        try:
            return path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise RuntimeError(f"Unable to read the SFMS integrity key file: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to create the SFMS integrity key file: {path}") from exc


def integrity_key() -> bytes:
    """Load the deployment override or a persistent key created on first launch."""
    value = os.environ.get(ENV_KEY, "").strip()
    if value:
        return _decode_integrity_key(value, ENV_KEY)
    value = _load_or_create_integrity_key_file(Path(INTEGRITY_KEY_PATH))
    return _decode_integrity_key(value, str(INTEGRITY_KEY_PATH))


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def install_receipt_hmac_schema(conn: sqlite3.Connection) -> None:
    """Migrate legacy receipt hashes to the versioned HMAC storage without deleting evidence."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='receipt_hashes'"
    ).fetchone()
    if not exists:
        conn.execute(
            """CREATE TABLE receipt_hashes(
                   receipt_id INTEGER PRIMARY KEY,
                   receipt_no TEXT UNIQUE NOT NULL,
                   hmac_value TEXT,
                   signed_fields_json TEXT,
                   signed_at TEXT,
                   algorithm TEXT NOT NULL DEFAULT 'HMAC-SHA256' CHECK(algorithm='HMAC-SHA256'),
                   legacy_sha256_hash TEXT,
                   FOREIGN KEY(receipt_id) REFERENCES receipts(id)
               )"""
        )
    elif "receipt_id" not in _columns(conn, "receipt_hashes"):
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS trg_hash_no_delete;
            DROP TRIGGER IF EXISTS trg_hash_no_update;
            ALTER TABLE receipt_hashes RENAME TO receipt_hashes_legacy;
            CREATE TABLE receipt_hashes(
                receipt_id INTEGER PRIMARY KEY,
                receipt_no TEXT UNIQUE NOT NULL,
                hmac_value TEXT,
                signed_fields_json TEXT,
                signed_at TEXT,
                algorithm TEXT NOT NULL DEFAULT 'HMAC-SHA256' CHECK(algorithm='HMAC-SHA256'),
                legacy_sha256_hash TEXT,
                FOREIGN KEY(receipt_id) REFERENCES receipts(id)
            );
            CREATE TRIGGER trg_legacy_hash_no_delete
            BEFORE DELETE ON receipt_hashes_legacy BEGIN
                SELECT RAISE(ABORT,'legacy receipt hashes cannot be deleted');
            END;
            CREATE TRIGGER trg_legacy_hash_no_update
            BEFORE UPDATE ON receipt_hashes_legacy BEGIN
                SELECT RAISE(ABORT,'legacy receipt hashes cannot be updated');
            END;
            """
        )
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_hash_algorithm_insert
        BEFORE INSERT ON receipt_hashes
        WHEN NEW.algorithm <> 'HMAC-SHA256' BEGIN
            SELECT RAISE(ABORT,'receipt hash algorithm must be HMAC-SHA256');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_hash_no_delete
        BEFORE DELETE ON receipt_hashes BEGIN
            SELECT RAISE(ABORT,'receipt hashes cannot be deleted');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_hash_no_update
        BEFORE UPDATE ON receipt_hashes BEGIN
            SELECT RAISE(ABORT,'receipt hashes cannot be updated');
        END;
        """
    )


def signed_receipt_fields(conn: sqlite3.Connection, receipt_id: int) -> dict:
    """Return the canonical complete field set signed for one receipt."""
    receipt = conn.execute(
        "SELECT id,receipt_no,student_id,total_paid,receipt_type FROM receipts WHERE id=?",
        (receipt_id,),
    ).fetchone()
    if receipt is None:
        raise ValueError("Receipt was not found.")
    rows = conn.execute(
        """
        SELECT p.receipt_no,p.student_id,p.amount_paid,p.payment_date,p.fee_head_id,
               p.amount_due,p.balance,p.payment_mode,
               CASE WHEN UPPER(p.payment_mode)='CHEQUE' THEN p.cheque_number
                    WHEN UPPER(p.payment_mode)='UPI' THEN p.upi_reference ELSE '' END,
               p.collected_by
        FROM payments p WHERE p.receipt_no=? ORDER BY p.id
        """,
        (receipt[1],),
    ).fetchall()
    payments = []
    for row in rows:
        values = list(row) + [receipt[4], receipt[3]]
        payments.append(dict(zip(SIGNED_PAYMENT_FIELDS, values)))
    return {
        "receipt_id": receipt[0],
        "receipt_no": receipt[1],
        "receipt_type": receipt[4],
        "receipt_total": receipt[3],
        "payments": payments,
    }


def canonical_signed_json(fields: dict) -> str:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_receipt_hmac(fields: dict, key: bytes | None = None) -> str:
    payload = canonical_signed_json(fields).encode("utf-8")
    return hmac.new(key or integrity_key(), payload, hashlib.sha256).hexdigest()


def sign_receipt(conn: sqlite3.Connection, receipt_id: int) -> str:
    """Insert one immutable HMAC record for a newly completed receipt."""
    fields = signed_receipt_fields(conn, receipt_id)
    value = compute_receipt_hmac(fields)
    conn.execute(
        """INSERT INTO receipt_hashes(
               receipt_id,receipt_no,hmac_value,signed_fields_json,signed_at,algorithm
           ) VALUES(?,?,?,?,?,?)""",
        (receipt_id, fields["receipt_no"], value, canonical_signed_json(fields), now_str(), ALGORITHM),
    )
    return value
