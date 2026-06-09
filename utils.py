"""General utility functions for SFMS."""

from __future__ import annotations

import hashlib
from datetime import datetime

from config import (
    CURRENCY_PREFIX,
    DATE_FORMAT,
    DATETIME_FORMAT,
    RECEIPT_PREFIX,
    RECEIPT_SEPARATOR,
    RECEIPT_SEQUENCE_WIDTH,
)


def format_currency(amount) -> str:
    """Return an amount formatted as rupees with thousands separators."""
    return f"{CURRENCY_PREFIX} {float(amount):,.2f}"


def today_str() -> str:
    """Return today's date in DD-MM-YYYY format."""
    return datetime.now().strftime(DATE_FORMAT)


def now_str() -> str:
    """Return the current date and time in DD-MM-YYYY HH:MM:SS format."""
    return datetime.now().strftime(DATETIME_FORMAT)


def generate_receipt_no(conn) -> str:
    """Generate the next yearly receipt number in RCP-YYYY-NNNNNN format."""
    year = datetime.now().strftime("%Y")
    prefix = f"{RECEIPT_PREFIX}{RECEIPT_SEPARATOR}{year}{RECEIPT_SEPARATOR}"
    row = conn.execute(
        """
        SELECT MAX(receipt_no)
        FROM receipts
        WHERE receipt_no LIKE ?
        """,
        (f"{prefix}%",),
    ).fetchone()
    max_receipt_no = row[0] if row else None
    next_sequence = 1
    if max_receipt_no:
        next_sequence = int(max_receipt_no.rsplit(RECEIPT_SEPARATOR, 1)[1]) + 1
    return f"{prefix}{next_sequence:0{RECEIPT_SEQUENCE_WIDTH}d}"


def compute_hash(receipt_no, student_id, amount_paid, payment_date) -> str:
    """Return the SHA-256 hex digest of the concatenated payment hash fields."""
    value = f"{receipt_no}{student_id}{amount_paid}{payment_date}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
