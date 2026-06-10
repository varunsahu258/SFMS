"""Receipt integrity verification and machine fingerprint controls for SFMS."""

from __future__ import annotations

import socket
import sqlite3
import time
import tkinter as tk
import uuid
from tkinter import messagebox

from utils import compute_hash, now_str

MACHINE_ID_SETTING = "machine_id"


def _row_value(row, key: str, index: int):
    """Read a value from either sqlite3.Row or a positional SQLite row."""
    return row[key] if hasattr(row, "keys") else row[index]


def _receipt_hash(conn: sqlite3.Connection, receipt_no: str) -> str | None:
    """Recompute the aggregate receipt hash from immutable payment rows."""
    rows = conn.execute(
        """
        SELECT id, student_id, fee_head_id, amount_paid, payment_date, hash
        FROM payments
        WHERE receipt_no = ?
        ORDER BY id
        """,
        (receipt_no,),
    ).fetchall()
    if not rows:
        return None
    student_id = _row_value(rows[0], "student_id", 1)
    payment_date = _row_value(rows[0], "payment_date", 4)
    for row in rows:
        payment_id = _row_value(row, "id", 0)
        row_student_id = _row_value(row, "student_id", 1)
        fee_head_id = _row_value(row, "fee_head_id", 2)
        amount_paid = float(_row_value(row, "amount_paid", 3) or 0)
        stored_payment_hash = str(_row_value(row, "hash", 5) or "")
        expected_payment_hash = compute_hash(receipt_no, row_student_id, amount_paid, _row_value(row, "payment_date", 4))
        allocations = conn.execute(
            """
            SELECT a.amount_allocated,c.student_id,c.fee_head_id
            FROM payment_allocations a JOIN student_charges c ON c.id=a.charge_id
            WHERE a.payment_id=?
            """, (payment_id,)
        ).fetchall()
        if not allocations or abs(sum(float(item[0]) for item in allocations)-amount_paid) > 0.005:
            return None
        if any(item[1] != row_student_id or item[2] != fee_head_id for item in allocations):
            return None
        if stored_payment_hash != expected_payment_hash:
            return None
    total_paid = sum(float(_row_value(row, "amount_paid", 3) or 0) for row in rows)
    return compute_hash(receipt_no, student_id, total_paid, payment_date)


def verify_all_hashes(conn) -> dict:
    """Verify every stored receipt hash and audit each detected mismatch."""
    result = {"ok": [], "mismatch": []}
    rows = conn.execute(
        "SELECT receipt_no, sha256_hash FROM receipt_hashes ORDER BY receipt_no"
    ).fetchall()
    for row in rows:
        receipt_no = str(_row_value(row, "receipt_no", 0))
        stored_hash = str(_row_value(row, "sha256_hash", 1) or "")
        computed_hash = _receipt_hash(conn, receipt_no)
        if computed_hash is not None and computed_hash == stored_hash:
            result["ok"].append(receipt_no)
            continue

        result["mismatch"].append(receipt_no)
        conn.execute(
            """
            INSERT INTO audit_log (
                timestamp, user_id, action, table_name, record_id,
                old_value, new_value, tamper_attempt
            ) VALUES (?, NULL, 'TAMPER_DETECTED', 'receipt_hashes', ?, ?, ?, 1)
            """,
            (now_str(), receipt_no, stored_hash, computed_hash),
        )
    conn.commit()
    return result


def _show_integrity_warning(count: int) -> None:
    """Schedule the integrity warning on Tk's UI thread when a root is ready."""
    message = f"WARNING: {count} receipt(s) may have been tampered. Contact developer."
    for _attempt in range(100):
        root = tk._default_root
        if root is not None:
            try:
                root.after(0, lambda: messagebox.showwarning("SFMS Integrity Warning", message))
                return
            except tk.TclError:
                pass
        time.sleep(0.05)


def startup_integrity_check(conn) -> list:
    """Run receipt verification in a daemon worker and return mismatch receipt numbers."""
    try:
        result = verify_all_hashes(conn)
        mismatches = result["mismatch"]
        if mismatches:
            _show_integrity_warning(len(mismatches))
        return mismatches
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def record_machine_fingerprint(conn):
    """Store the first machine identity or audit and warn when it changes."""
    fingerprint = f"{socket.gethostname()}_{uuid.getnode()}"
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (MACHINE_ID_SETTING,),
    ).fetchone()
    existing = str(row[0] or "") if row else ""
    if not existing:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (MACHINE_ID_SETTING, fingerprint),
        )
        conn.commit()
        return
    if existing == fingerprint:
        return

    conn.execute(
        """
        INSERT INTO audit_log (
            timestamp, user_id, action, table_name, record_id,
            old_value, new_value, tamper_attempt
        ) VALUES (?, NULL, 'MACHINE_CHANGE', 'settings', ?, ?, ?, 0)
        """,
        (now_str(), MACHINE_ID_SETTING, existing, fingerprint),
    )
    conn.execute(
        "UPDATE settings SET value = ? WHERE key = ?",
        (fingerprint, MACHINE_ID_SETTING),
    )
    conn.commit()
    messagebox.showwarning(
        "SFMS Machine Warning",
        "WARNING: This database was last used on a different computer.",
    )
