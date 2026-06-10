"""Receipt integrity verification and machine fingerprint controls for SFMS."""

from __future__ import annotations

import hmac
import socket
import sqlite3
import time
import tkinter as tk
import uuid
from tkinter import messagebox

from utils import now_str

MACHINE_ID_SETTING = "machine_id"


def _row_value(row, key: str, index: int):
    """Read a value from either sqlite3.Row or a positional SQLite row."""
    return row[key] if hasattr(row, "keys") else row[index]


def verify_single_receipt(receipt_id: int, conn: sqlite3.Connection | None = None) -> tuple[bool, str]:
    """Verify one receipt HMAC, opening DB_PATH when no connection is supplied."""
    from config import DB_PATH
    from receipt_integrity import ALGORITHM, canonical_signed_json, compute_receipt_hmac, signed_receipt_fields

    owns_connection = conn is None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        receipt = conn.execute("SELECT receipt_no FROM receipts WHERE id=?", (receipt_id,)).fetchone()
        if receipt is None:
            return False, "receipt not found"
        payment = conn.execute("SELECT 1 FROM payments WHERE receipt_no=? LIMIT 1", (receipt[0],)).fetchone()
        if payment is None:
            return False, "orphan receipt: no corresponding payment"
        stored = conn.execute(
            "SELECT hmac_value,signed_fields_json,algorithm FROM receipt_hashes WHERE receipt_id=?",
            (receipt_id,),
        ).fetchone()
        if stored is None:
            return False, "missing receipt_hashes entry"
        if stored[2] != ALGORITHM:
            return False, f"unsupported integrity algorithm: {stored[2]}"
        fields = signed_receipt_fields(conn, receipt_id)
        canonical = canonical_signed_json(fields)
        if stored[1] != canonical:
            return False, "signed field snapshot differs from receipt data"
        expected = compute_receipt_hmac(fields)
        if not hmac.compare_digest(str(stored[0] or ""), expected):
            return False, "HMAC mismatch"
        return True, "ok"
    except RuntimeError as exc:
        return False, str(exc)
    finally:
        if owns_connection:
            conn.close()


def _sequence_gaps(receipt_numbers: list[str]) -> list[str]:
    """Return missing RCP-YYYY-NNNNNN receipt numbers grouped by prefix."""
    import re

    groups: dict[str, set[int]] = {}
    widths: dict[str, int] = {}
    for receipt_no in receipt_numbers:
        match = re.fullmatch(r"(.+?-)(\d+)", str(receipt_no))
        if not match:
            continue
        prefix, sequence = match.groups()
        groups.setdefault(prefix, set()).add(int(sequence))
        widths[prefix] = max(widths.get(prefix, 0), len(sequence))
    gaps: list[str] = []
    for prefix, values in groups.items():
        if len(values) < 2:
            continue
        for missing in sorted(set(range(min(values), max(values) + 1)) - values):
            gaps.append(f"{prefix}{missing:0{widths[prefix]}d}")
    return gaps


def _audit_integrity_issue(conn: sqlite3.Connection, record_id: str, reason: str) -> None:
    conn.execute(
        """INSERT INTO audit_log(timestamp,user_id,action,table_name,record_id,
               old_value,new_value,tamper_attempt)
           VALUES (?,NULL,'TAMPER_DETECTED','receipt_hashes',?,NULL,?,1)""",
        (now_str(), record_id, reason),
    )


def verify_all_hashes(conn) -> dict:
    """Verify HMACs and detect missing hashes, orphan records, and sequence gaps."""
    receipt_rows = conn.execute("SELECT id,receipt_no FROM receipts ORDER BY id").fetchall()
    receipts = {int(row[0]): str(row[1]) for row in receipt_rows}
    result = {
        "ok": [], "mismatch": [], "orphan_payments": [], "orphan_receipts": [],
        "missing_hashes": [], "sequence_gaps": _sequence_gaps(list(receipts.values())),
    }

    orphan_payment_rows = conn.execute(
        """SELECT DISTINCT p.receipt_no FROM payments p
           LEFT JOIN receipts r ON r.receipt_no=p.receipt_no
           LEFT JOIN receipt_hashes h ON h.receipt_no=p.receipt_no
           WHERE r.id IS NULL OR h.receipt_id IS NULL ORDER BY p.receipt_no"""
    ).fetchall()
    result["orphan_payments"] = [str(row[0]) for row in orphan_payment_rows]

    for receipt_id, receipt_no in receipts.items():
        has_payment = conn.execute("SELECT 1 FROM payments WHERE receipt_no=? LIMIT 1", (receipt_no,)).fetchone()
        if not has_payment:
            result["orphan_receipts"].append(receipt_no)
            continue
        has_hash = conn.execute("SELECT 1 FROM receipt_hashes WHERE receipt_id=?", (receipt_id,)).fetchone()
        if not has_hash:
            result["missing_hashes"].append(receipt_no)
            continue
        ok, reason = verify_single_receipt(receipt_id, conn)
        if ok:
            result["ok"].append(receipt_no)
        else:
            result["mismatch"].append(receipt_no)
            _audit_integrity_issue(conn, receipt_no, reason)

    for category in ("orphan_payments", "orphan_receipts", "missing_hashes", "sequence_gaps"):
        for receipt_no in result[category]:
            _audit_integrity_issue(conn, receipt_no, category)
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
        mismatches = sorted(set(
            result["mismatch"] + result["orphan_payments"] + result["orphan_receipts"]
            + result["missing_hashes"] + result["sequence_gaps"]
        ))
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
