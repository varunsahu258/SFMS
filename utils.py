"""General utility functions for SFMS."""

from __future__ import annotations

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


def ensure_receipt_sequence(conn) -> None:
    """Create and seed the serialized receipt sequence from existing receipts."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS receipt_sequence (
               id INTEGER PRIMARY KEY CHECK(id = 1),
               last_receipt_no INTEGER NOT NULL DEFAULT 0
           )"""
    )
    row = conn.execute("SELECT 1 FROM receipt_sequence WHERE id=1").fetchone()
    if row is None:
        max_seen = 0
        for (receipt_no,) in conn.execute("SELECT receipt_no FROM receipts WHERE receipt_no IS NOT NULL"):
            try:
                max_seen = max(max_seen, int(str(receipt_no).rsplit(RECEIPT_SEPARATOR, 1)[1]))
            except (IndexError, ValueError):
                continue
        conn.execute(
            "INSERT INTO receipt_sequence(id,last_receipt_no) VALUES(1,?)",
            (max_seen,),
        )


def get_next_receipt_no(conn) -> int:
    """Return the next serialized receipt number using SQLite's write lock."""
    ensure_receipt_sequence(conn)
    conn.execute("UPDATE receipt_sequence SET last_receipt_no = last_receipt_no + 1 WHERE id = 1")
    row = conn.execute("SELECT last_receipt_no FROM receipt_sequence WHERE id = 1").fetchone()
    return int(row[0])


def generate_receipt_no(conn) -> str:
    """Generate the next receipt number in RCP-YYYY-NNNNNN format safely."""
    year = datetime.now().strftime("%Y")
    prefix = f"{RECEIPT_PREFIX}{RECEIPT_SEPARATOR}{year}{RECEIPT_SEPARATOR}"
    next_sequence = get_next_receipt_no(conn)
    return f"{prefix}{next_sequence:0{RECEIPT_SEQUENCE_WIDTH}d}"
